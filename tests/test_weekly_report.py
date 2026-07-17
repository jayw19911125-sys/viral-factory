import unittest

from weekly_report import deterministic_stats


def prop_number(value):
    return {"type": "number", "number": value}


def prop_text(value):
    return {"type": "rich_text", "rich_text": [{"plain_text": value}] if value else []}


def prop_status(value):
    return {"type": "status", "status": {"name": value}}


class WeeklyReportTests(unittest.TestCase):
    def test_stats_are_unique_and_missing_is_not_zero(self):
        entries = [
            {
                "id": "1",
                "properties": {
                    "platform_video_id": prop_text("tiktok:1"),
                    "處理狀態": prop_status("unique_success"),
                    "觀看數": prop_number(97200),
                    "作者帳號": prop_text("creator"),
                    "證據狀態": prop_status("verified"),
                    "開頭鉤子拆解": prop_text("hook"),
                    "結構拆解": prop_text("structure"),
                },
            },
            {
                "id": "duplicate",
                "properties": {
                    "platform_video_id": prop_text("tiktok:1"),
                    "處理狀態": prop_status("unique_success"),
                    "觀看數": prop_number(1),
                },
            },
            {
                "id": "2",
                "properties": {
                    "platform_video_id": prop_text("tiktok:2"),
                    "處理狀態": prop_status("unique_success"),
                    "觀看數": prop_number(None),
                },
            },
            {
                "id": "historical-unverified",
                "properties": {
                    "platform_video_id": prop_text("tiktok:old"),
                    "觀看數": prop_number(999999999),
                    "作者帳號": prop_text("should-not-count"),
                },
            },
        ]
        stats = deterministic_stats(entries)
        self.assertEqual(stats["unique_count"], 2)
        self.assertEqual(stats["max_views"], 97200)
        self.assertEqual(stats["average_views"], 97200)
        self.assertEqual(stats["coverage"]["views"], 0.5)
        self.assertEqual(stats["excluded_status_count"], 1)


if __name__ == "__main__":
    unittest.main()
