"""Collect exact Slack thread acknowledgements for tracked notification messages.

This collector is intentionally fail-closed: an ACK counts only when the parent
message_ts matches the tracker, the reply author matches the intended recipient,
and the reply text is exactly ``OK`` or ``OK <role name>``.  Slack events remain
notification evidence only; they are never task-completion evidence.
"""

from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime
from typing import Any

from execution_tracker import get_daily_report, log_confirmation


ROLE_LABELS = {"planner": "小鑫", "editor": "阿韋"}


def _strip_wrapper(stdout: str) -> str:
    raw = re.sub(r"\x1b\[[0-9;]*m", "", stdout.strip())
    if "Tool execution result:" in raw:
        raw = raw.rsplit("Tool execution result:", 1)[-1].strip()
    return raw


def _formatted_replies(text: str, parent_ts: str) -> list[dict[str, str]]:
    """Parse the human-readable shape returned by some Slack MCP wrappers."""
    if re.search(r"no thread mess+sages", text, re.IGNORECASE):
        return []
    starts = list(re.finditer(r"(?m)^From:\s+.*?\((U[A-Z0-9]+)\)\s*$", text))
    replies = []
    for index, start in enumerate(starts):
        end = starts[index + 1].start() if index + 1 < len(starts) else len(text)
        block = text[start.start():end]
        ts_match = re.search(
            r"(?im)^Message(?:[_ ]TS|[_ ]ts|[_ ]id)?\s*:\s*(\d{9,12}\.\d{3,9})\s*$",
            block,
        )
        if not ts_match or ts_match.group(1) == parent_ts:
            continue
        body = block[ts_match.end():].strip()
        body = re.split(r"(?m)^===", body, maxsplit=1)[0].strip()
        replies.append({"user_id": start.group(1), "message_ts": ts_match.group(1), "text": body})
    return replies


def _structured_replies(node: Any, parent_ts: str) -> list[dict[str, str]]:
    replies: list[dict[str, str]] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            ts = next(
                (value.get(key) for key in ("message_ts", "ts", "timestamp", "id") if value.get(key)),
                None,
            )
            user = next(
                (value.get(key) for key in ("user_id", "user", "author_id", "sender_id") if value.get(key)),
                None,
            )
            text = next(
                (value.get(key) for key in ("text", "message", "content") if isinstance(value.get(key), str)),
                None,
            )
            if ts and user and text and str(ts) != parent_ts:
                replies.append({"user_id": str(user), "message_ts": str(ts), "text": text})
            for nested in value.values():
                walk(nested)
        elif isinstance(value, list):
            for nested in value:
                walk(nested)
        elif isinstance(value, str) and "From:" in value:
            replies.extend(_formatted_replies(value, parent_ts))

    walk(node)
    deduped = {}
    for reply in replies:
        deduped[(reply["message_ts"], reply["user_id"])] = reply
    return list(deduped.values())


def parse_thread_replies(stdout: str, parent_ts: str) -> list[dict[str, str]]:
    raw = _strip_wrapper(stdout)
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return _formatted_replies(raw, parent_ts)
    return _structured_replies(payload, parent_ts)


def is_exact_ack(text: str, role: str) -> bool:
    label = ROLE_LABELS.get(role)
    if not label:
        return False
    normalized = re.sub(r"[`*_~]", "", text).strip()
    return bool(re.fullmatch(rf"OK(?:\s+{re.escape(label)})?[.!！。]?", normalized, re.IGNORECASE))


def fetch_thread(channel_id: str, message_ts: str) -> list[dict[str, str]]:
    result = subprocess.run(
        [
            "manus-mcp-cli",
            "tool",
            "call",
            "slack_read_thread",
            "--server",
            "slack",
            "--input",
            json.dumps({"channel_id": channel_id, "message_ts": message_ts, "limit": 100}),
        ],
        capture_output=True,
        text=True,
        timeout=45,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Slack thread read failed: {result.stderr[:200]}")
    return parse_thread_replies(result.stdout, message_ts)


def collect_acknowledgements(date_str: str | None = None) -> dict[str, Any]:
    date_str = date_str or datetime.now().astimezone().strftime("%Y-%m-%d")
    report = get_daily_report(date_str)
    summary: dict[str, Any] = {
        "date": date_str,
        "tracked": 0,
        "confirmed": 0,
        "no_ack": 0,
        "untrackable": 0,
        "errors": [],
    }

    for video_id, video in report.items():
        for role in ROLE_LABELS:
            event = video.get(role) or {}
            if event.get("status") == "confirmed":
                continue
            channel_id = event.get("channel_id")
            parent_ts = event.get("message_ts")
            recipient_id = event.get("recipient_id")
            if not channel_id or not parent_ts or not recipient_id:
                if event:
                    summary["untrackable"] += 1
                continue

            summary["tracked"] += 1
            try:
                replies = fetch_thread(channel_id, parent_ts)
            except Exception as exc:
                summary["errors"].append({
                    "video_id": video_id,
                    "role": role,
                    "error": f"{type(exc).__name__}: {exc}",
                })
                continue

            acknowledgement = next(
                (
                    reply for reply in replies
                    if reply.get("user_id") == recipient_id and is_exact_ack(reply.get("text", ""), role)
                ),
                None,
            )
            if acknowledgement and log_confirmation(
                video_id,
                role,
                confirmed_by=recipient_id,
                confirmation_type="exact_thread_reply",
                message_ts=parent_ts,
                date_str=date_str,
            ):
                summary["confirmed"] += 1
            else:
                summary["no_ack"] += 1

    return summary


if __name__ == "__main__":
    print(json.dumps(collect_acknowledgements(), ensure_ascii=False, indent=2))
