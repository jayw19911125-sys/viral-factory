import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import viral_factory
from data_quality import OUTCOME_QUARANTINED


class PipelineSafetyTests(unittest.TestCase):
    @patch.object(viral_factory, "write_to_notion_via_mcp")
    @patch.object(viral_factory, "analyze_with_gpt4o")
    @patch.object(viral_factory, "transcribe_audio", return_value="可用逐字稿")
    @patch.object(viral_factory, "download_video", return_value="/tmp/audio.mp3")
    @patch.object(
        viral_factory,
        "fetch_video_metadata",
        return_value={"title": "t", "uploader": "a", "view_count": 97200},
    )
    @patch.object(viral_factory, "check_duplicate_via_mcp", return_value=False)
    def test_no_visual_evidence_quarantines_before_ai_or_write(
        self, _dedupe, _metadata, _download, _transcribe, analyze, notion_write
    ):
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            viral_factory, "PROCESSED_REGISTRY", Path(tmp) / "processed.json"
        ):
            result = viral_factory.process_single_video(
                "https://www.tiktok.com/@a/video/123", run_id="run-test"
            )
        self.assertEqual(result["outcome"], OUTCOME_QUARANTINED)
        self.assertFalse(result["success"])
        analyze.assert_not_called()
        notion_write.assert_not_called()

    def test_slack_parser_extracts_trackable_message(self):
        parsed = viral_factory._parse_slack_send_output(
            'Tool execution result:\n{"ok":true,"channel":"C1","ts":"123.456"}'
        )
        self.assertEqual(parsed["channel_id"], "C1")
        self.assertEqual(parsed["message_ts"], "123.456")

    def test_slack_parser_extracts_ids_from_formatted_wrapper(self):
        parsed = viral_factory._parse_slack_send_output(
            "Message sent to C0AUH4QKF5M\nMessage_ts: 1784251063.838589"
        )
        self.assertEqual(parsed["channel_id"], "C0AUH4QKF5M")
        self.assertEqual(parsed["message_ts"], "1784251063.838589")


if __name__ == "__main__":
    unittest.main()
