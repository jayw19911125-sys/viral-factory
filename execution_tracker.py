"""Durable-enough local event log for Slack delivery and acknowledgement.

Monday remains the task-status SSOT.  This file only stores machine events needed
to reconcile Slack messages; it must never be interpreted as work completion.
"""

from __future__ import annotations

import json
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import fcntl


TRACKER_FILE = Path(__file__).parent / "data" / "execution_tracker.json"


def _now() -> str:
    return datetime.now().astimezone().isoformat()


@contextmanager
def _tracker_lock():
    TRACKER_FILE.parent.mkdir(parents=True, exist_ok=True)
    lock_path = TRACKER_FILE.with_suffix(TRACKER_FILE.suffix + ".lock")
    with lock_path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _read() -> dict:
    TRACKER_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not TRACKER_FILE.exists():
        return {}
    try:
        return json.loads(TRACKER_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        # Do not erase a corrupt tracker.  Force the caller to surface the error.
        raise RuntimeError(f"execution tracker unreadable: {TRACKER_FILE}")


def _atomic_write(data: dict) -> None:
    TRACKER_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix="execution_tracker_", suffix=".json", dir=TRACKER_FILE.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, TRACKER_FILE)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def log_delivery(
    video_id: str,
    user_role: str,
    status: str = "sent",
    *,
    run_id: str = "",
    channel_id: str = "",
    message_ts: str = "",
    recipient_id: str = "",
) -> None:
    with _tracker_lock():
        data = _read()
        today = datetime.now().astimezone().strftime("%Y-%m-%d")
        day = data.setdefault(today, {})
        video = day.setdefault(video_id, {"attempts": []})
        role = video.setdefault(user_role, {})
        event = {
            "status": status,
            "run_id": run_id or None,
            "channel_id": channel_id or None,
            "message_ts": message_ts or None,
            "recipient_id": recipient_id or None,
            "sent_at": _now(),
            "confirmed_at": None,
            "confirmed_by": None,
            "confirmation_type": None,
        }
        role.update(event)
        video.setdefault("attempts", []).append({"role": user_role, **event})
        _atomic_write(data)


def log_confirmation(
    video_id: str,
    user_role: str,
    *,
    confirmed_by: str,
    confirmation_type: str,
    message_ts: str,
    date_str: str | None = None,
) -> bool:
    with _tracker_lock():
        data = _read()
        date_str = date_str or datetime.now().astimezone().strftime("%Y-%m-%d")
        role = data.get(date_str, {}).get(video_id, {}).get(user_role)
        if not role or role.get("message_ts") != message_ts:
            return False
        role["status"] = "confirmed"
        role["confirmed_at"] = _now()
        role["confirmed_by"] = confirmed_by
        role["confirmation_type"] = confirmation_type
        for attempt in data.get(date_str, {}).get(video_id, {}).get("attempts", []):
            if attempt.get("role") == user_role and attempt.get("message_ts") == message_ts:
                attempt["status"] = "confirmed"
                attempt["confirmed_at"] = role["confirmed_at"]
                attempt["confirmed_by"] = confirmed_by
                attempt["confirmation_type"] = confirmation_type
        _atomic_write(data)
        return True


def get_daily_report(date_str: str | None = None) -> dict:
    date_str = date_str or datetime.now().astimezone().strftime("%Y-%m-%d")
    return _read().get(date_str, {})


if __name__ == "__main__":
    print(json.dumps(get_daily_report(), indent=2, ensure_ascii=False))
