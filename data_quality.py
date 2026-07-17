"""Shared data-quality contract for the viral video pipeline.

The pipeline must never treat missing evidence as a real zero or infer visual and
performance facts from an audio transcript.  This module intentionally has no
external dependencies so it can be used by ingestion, scoring and reporting.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import fcntl


OUTCOME_UNIQUE_SUCCESS = "unique_success"
OUTCOME_DUPLICATE = "duplicate"
OUTCOME_QUARANTINED = "quarantined"
OUTCOME_QUEUED = "queued"
OUTCOME_WRITE_UNVERIFIED = "write_unverified"
OUTCOME_TECHNICAL_ERROR = "technical_error"

EVIDENCE_VERIFIED = "verified"
EVIDENCE_TEXT_ONLY = "text_only"
EVIDENCE_INSUFFICIENT = "insufficient"

_TRACKING_QUERY_PREFIXES = ("utm_",)
_TRACKING_QUERY_KEYS = {
    "fbclid",
    "gclid",
    "igshid",
    "is_copy_url",
    "is_from_webapp",
    "sender_device",
    "share_app_id",
    "share_iid",
    "share_link_id",
    "social_share_type",
    "timestamp",
}


def _drop_tracking_query(url: str) -> str:
    parsed = urlparse(url.strip())
    kept = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        lowered = key.lower()
        if lowered in _TRACKING_QUERY_KEYS or lowered.startswith(_TRACKING_QUERY_PREFIXES):
            continue
        kept.append((key, value))
    path = parsed.path.rstrip("/") or "/"
    return urlunparse(
        (
            parsed.scheme.lower() or "https",
            parsed.netloc.lower(),
            path,
            "",
            urlencode(kept, doseq=True),
            "",
        )
    )


def canonical_video_identity(url: str) -> dict[str, str]:
    """Return canonical URL and a stable cross-URL identity when possible."""
    if not isinstance(url, str) or not url.strip():
        raise ValueError("video URL is empty")
    cleaned = _drop_tracking_query(url)
    parsed = urlparse(cleaned)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"invalid video URL: {url!r}")
    host = parsed.netloc.lower().removeprefix("www.")
    path = parsed.path

    match = re.search(r"/@([^/?#]+)/video/(\d+)", path)
    if "tiktok.com" in host and match:
        handle, video_id = match.groups()
        canonical = f"https://www.tiktok.com/@{handle}/video/{video_id}"
        return {"platform": "TikTok", "video_id": video_id, "identity": f"tiktok:{video_id}", "canonical_url": canonical}

    match = re.search(r"/(?:reel|reels|p)/([^/?#]+)", path)
    if "instagram.com" in host and match:
        video_id = match.group(1)
        canonical = f"https://www.instagram.com/reel/{video_id}"
        return {"platform": "Reels", "video_id": video_id, "identity": f"instagram:{video_id}", "canonical_url": canonical}

    youtube_id = ""
    if host == "youtu.be":
        youtube_id = path.strip("/").split("/")[0]
    elif "youtube.com" in host:
        match = re.search(r"/(?:shorts|embed)/([^/?#]+)", path)
        if match:
            youtube_id = match.group(1)
        else:
            youtube_id = dict(parse_qsl(parsed.query)).get("v", "")
    if youtube_id:
        canonical = f"https://www.youtube.com/shorts/{youtube_id}"
        return {"platform": "Shorts", "video_id": youtube_id, "identity": f"youtube:{youtube_id}", "canonical_url": canonical}

    match = re.search(r"/(?:reel|reels|watch)/(?:[^/?#]+/)?(\d+)", path)
    if ("facebook.com" in host or host == "fb.watch") and match:
        video_id = match.group(1)
        canonical = f"https://www.facebook.com/reel/{video_id}"
        return {"platform": "FB", "video_id": video_id, "identity": f"facebook:{video_id}", "canonical_url": canonical}

    identity = f"url:{cleaned}"
    return {"platform": "", "video_id": "", "identity": identity, "canonical_url": cleaned}


def optional_int(value: Any) -> int | None:
    """Convert a real numeric value; missing/invalid values remain None, never 0."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        stripped = value.replace(",", "").strip()
        if stripped and re.fullmatch(r"-?\d+", stripped):
            return int(stripped)
    return None


def has_usable_transcript(transcript: str | None) -> bool:
    if not transcript or not transcript.strip():
        return False
    blocked_markers = ("待補充", "無法下載音頻", "額度不足", "純視覺影片")
    return not any(marker in transcript for marker in blocked_markers)


def evaluate_evidence(metadata: dict[str, Any], transcript: str | None, has_visual_evidence: bool) -> dict[str, Any]:
    view_count = optional_int(metadata.get("view_count"))
    if view_count is not None and view_count < 0:
        view_count = None
    metadata_ready = bool(metadata.get("uploader")) and view_count is not None
    transcript_ready = has_usable_transcript(transcript)

    if metadata_ready and transcript_ready and has_visual_evidence:
        status = EVIDENCE_VERIFIED
    elif transcript_ready:
        status = EVIDENCE_TEXT_ONLY
    else:
        status = EVIDENCE_INSUFFICIENT

    return {
        "status": status,
        "metadata_ready": metadata_ready,
        "transcript_ready": transcript_ready,
        "visual_ready": bool(has_visual_evidence),
        "view_count": view_count,
        "uploader": metadata.get("uploader") or None,
        "upload_date": metadata.get("upload_date") or None,
    }


def viral_data_text(evidence: dict[str, Any]) -> str:
    """Stable machine-readable text for the current rich-text Notion property."""
    payload = {
        "view_count": evidence.get("view_count"),
        "uploader": evidence.get("uploader"),
        "upload_date": evidence.get("upload_date"),
        "evidence_status": evidence.get("status"),
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def sanitize_unverifiable_analysis(analysis: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    """Remove claims the pipeline has no evidence to make."""
    sanitized = dict(analysis)
    sanitized["evidence_status"] = evidence["status"]
    sanitized["爆款數據"] = viral_data_text(evidence)

    if not evidence.get("visual_ready"):
        unavailable = {"證據狀態": "需人工補充：目前流程未取得影片畫面，不得推定視覺內容"}
        sanitized["視覺錘分析"] = unavailable
        sanitized["視覺設計亮點"] = unavailable
        tips = dict(sanitized.get("剪輯師應用建議") or {})
        for key in ("前3秒剪輯指令", "節奏時間軸", "視覺錘強調方式", "音效與音樂建議", "熱門音樂趨勢", "剪輯技巧建議"):
            tips[key] = "需人工補充：未取得影片畫面／音樂證據"
        sanitized["剪輯師應用建議"] = tips
        sanitized["視覺錘類型"] = None
        sanitized["熱門音樂"] = "需人工補充"

    performance = dict(sanitized.get("爆款原因深度分析") or {})
    performance["演算法層面"] = "無留存率、完播率與互動密度資料，禁止判斷"
    sanitized["爆款原因深度分析"] = performance
    sanitized["廣告投放潛力"] = None
    return sanitized


def may_publish_guidance(evidence: dict[str, Any]) -> bool:
    """Actionable guidance requires metadata, transcript and real visual evidence."""
    return evidence.get("status") == EVIDENCE_VERIFIED


def load_processed_registry(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"videos": {}, "in_flight": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"processed registry unreadable: {path}") from exc
    if not isinstance(data.get("videos"), dict):
        raise RuntimeError(f"processed registry has invalid shape: {path}")
    if "in_flight" not in data:
        data["in_flight"] = {}
    if not isinstance(data["in_flight"], dict):
        raise RuntimeError(f"processed registry has invalid in_flight shape: {path}")
    return data


def _atomic_registry_write(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix="processed_videos_", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


@contextmanager
def _registry_lock(path: Path):
    """Serialize cross-process claim/update operations for one registry."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")
    with lock_path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def is_locally_processed(identity: str, path: Path) -> bool:
    return identity in load_processed_registry(path)["videos"]


def claim_video(identity: str, path: Path, run_id: str, stale_after_seconds: int = 21600) -> str:
    """Atomically reserve an identity. Return claimed, processed, or in_flight."""
    with _registry_lock(path):
        data = load_processed_registry(path)
        if identity in data["videos"]:
            return "processed"
        claim = data["in_flight"].get(identity)
        if claim and claim.get("run_id") != run_id:
            try:
                claimed_at = datetime.fromisoformat(claim["claimed_at"])
                age_seconds = (datetime.now().astimezone() - claimed_at).total_seconds()
            except (KeyError, TypeError, ValueError):
                age_seconds = 0
            if age_seconds <= stale_after_seconds:
                return "in_flight"
        data["in_flight"][identity] = {"run_id": run_id, "claimed_at": datetime.now().astimezone().isoformat()}
        _atomic_registry_write(path, data)
        return "claimed"


def release_video_claim(identity: str, path: Path, run_id: str) -> None:
    with _registry_lock(path):
        data = load_processed_registry(path)
        claim = data["in_flight"].get(identity)
        if claim and claim.get("run_id") == run_id:
            del data["in_flight"][identity]
            _atomic_registry_write(path, data)


def mark_locally_processed(identity: str, canonical_url: str, notion_url: str, path: Path, run_id: str) -> None:
    with _registry_lock(path):
        data = load_processed_registry(path)
        data["videos"][identity] = {
            "canonical_url": canonical_url,
            "notion_url": notion_url,
            "run_id": run_id,
            "processed_at": datetime.now().astimezone().isoformat(),
        }
        claim = data["in_flight"].get(identity)
        if claim and claim.get("run_id") == run_id:
            del data["in_flight"][identity]
        _atomic_registry_write(path, data)
