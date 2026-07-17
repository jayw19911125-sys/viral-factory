import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import trending_fetcher


class TrendingFetcherTests(unittest.TestCase):
    def test_single_observation_is_not_fake_growth(self):
        weekly = {
            "videos": {
                "tiktok:1": {
                    "identity": "tiktok:1",
                    "url": "https://www.tiktok.com/@a/video/1",
                    "title": "old",
                    "handle": "a",
                    "platform": "tiktok",
                    "category": "test",
                    "view_history": [{"ts": "2026-07-17T09:00:00", "views": 99999}],
                },
                "tiktok:2": {
                    "identity": "tiktok:2",
                    "url": "https://www.tiktok.com/@a/video/2",
                    "title": "absolute",
                    "handle": "a",
                    "platform": "tiktok",
                    "category": "test",
                    "view_history": [{"ts": "2026-07-17T09:00:00", "views": 100000}],
                },
            }
        }
        rows = trending_fetcher.detect_anomalies(weekly)
        self.assertEqual([row["identity"] for row in rows], ["tiktok:2"])
        self.assertIsNone(rows[0]["growth_48h"])
        self.assertEqual(rows[0]["signal"], "absolute_views")

    def test_manual_queue_is_only_removed_after_terminal_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "manual_queue.txt"
            urls = [f"https://example.com/{index}" for index in range(8)]
            path.write_text("# queue\n" + "\n".join(urls) + "\n", encoding="utf-8")
            with patch.object(trending_fetcher, "MANUAL_QUEUE_FILE", path):
                selected = trending_fetcher.load_manual_queue(6)
                before_ack = path.read_text(encoding="utf-8")
                removed = trending_fetcher.acknowledge_manual_queue([
                    {"identity": "url:https://example.com/0", "outcome": "unique_success"},
                    {"identity": "url:https://example.com/1", "outcome": "quarantined"},
                ])
            self.assertEqual(selected, urls[:6])
            self.assertIn(urls[0], before_ack)
            self.assertEqual(removed, 1)
            after_ack = path.read_text(encoding="utf-8")
            self.assertNotIn(urls[0] + "\n", after_ack)
            self.assertIn(urls[1], after_ack)
            self.assertIn(urls[7], after_ack)


if __name__ == "__main__":
    unittest.main()
