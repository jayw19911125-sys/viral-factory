import json
import tempfile
import unittest
from pathlib import Path

from data_quality import (
    EVIDENCE_TEXT_ONLY,
    canonical_video_identity,
    claim_video,
    evaluate_evidence,
    is_locally_processed,
    mark_locally_processed,
    may_publish_guidance,
    optional_int,
    sanitize_unverifiable_analysis,
)


class DataQualityTests(unittest.TestCase):
    def test_tiktok_query_variants_share_identity(self):
        first = canonical_video_identity(
            "https://www.tiktok.com/@creator/video/123456789?utm_source=x&is_copy_url=1"
        )
        second = canonical_video_identity(
            "https://www.tiktok.com/@creator/video/123456789/"
        )
        self.assertEqual(first["identity"], "tiktok:123456789")
        self.assertEqual(first["canonical_url"], second["canonical_url"])

    def test_missing_number_never_becomes_zero(self):
        self.assertIsNone(optional_int(None))
        self.assertIsNone(optional_int(""))
        self.assertEqual(optional_int("97,200"), 97200)
        self.assertEqual(optional_int(0), 0)

    def test_text_only_evidence_cannot_publish_guidance(self):
        evidence = evaluate_evidence(
            {"view_count": 97200, "uploader": "creator"},
            "可用逐字稿",
            has_visual_evidence=False,
        )
        self.assertEqual(evidence["status"], EVIDENCE_TEXT_ONLY)
        self.assertFalse(may_publish_guidance(evidence))
        sanitized = sanitize_unverifiable_analysis(
            {"視覺錘分析": {"視覺錘是什麼": "臆測畫面"}}, evidence
        )
        self.assertIn("未取得影片畫面", sanitized["視覺錘分析"]["證據狀態"])
        self.assertIsNone(sanitized["廣告投放潛力"])

    def test_processed_registry_is_atomic_and_identity_based(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "processed.json"
            mark_locally_processed(
                "tiktok:123", "https://www.tiktok.com/@a/video/123", "https://notion.so/page", path, "run-1"
            )
            self.assertTrue(is_locally_processed("tiktok:123", path))
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(data["videos"]["tiktok:123"]["run_id"], "run-1")

    def test_registry_claim_blocks_concurrent_run_and_becomes_processed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "processed.json"
            self.assertEqual(claim_video("tiktok:123", path, "run-1"), "claimed")
            self.assertEqual(claim_video("tiktok:123", path, "run-2"), "in_flight")
            mark_locally_processed(
                "tiktok:123", "https://www.tiktok.com/@a/video/123", "https://notion.so/page", path, "run-1"
            )
            self.assertEqual(claim_video("tiktok:123", path, "run-2"), "processed")


if __name__ == "__main__":
    unittest.main()
