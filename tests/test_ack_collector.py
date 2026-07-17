import json
import unittest

from ack_collector import is_exact_ack, parse_thread_replies


class AckCollectorTests(unittest.TestCase):
    def test_structured_reply_is_bound_to_parent_and_user(self):
        stdout = "Tool execution result:\n" + json.dumps({
            "messages": [
                {"ts": "100.001", "user": "UBOT", "text": "parent"},
                {"ts": "100.002", "user": "U小鑫", "text": "OK 小鑫"},
            ]
        }, ensure_ascii=False)
        replies = parse_thread_replies(stdout, "100.001")
        self.assertEqual(replies, [{"user_id": "U小鑫", "message_ts": "100.002", "text": "OK 小鑫"}])
        self.assertTrue(is_exact_ack(replies[0]["text"], "planner"))

    def test_formatted_no_reply_is_not_confirmation(self):
        stdout = json.dumps({
            "messages": (
                "=== THREAD PARENT MESSAGE ===\n"
                "From: Sender (UBOT123)\n"
                "Message TS: 1784251063.838589\n"
                "OK 小鑫\n\nNo thread messsages"
            )
        })
        self.assertEqual(parse_thread_replies(stdout, "1784251063.838589"), [])

    def test_ack_must_be_exact(self):
        self.assertTrue(is_exact_ack("`OK 阿韋`", "editor"))
        self.assertFalse(is_exact_ack("我等等 OK 阿韋", "editor"))
        self.assertFalse(is_exact_ack("OK 小鑫", "editor"))


if __name__ == "__main__":
    unittest.main()
