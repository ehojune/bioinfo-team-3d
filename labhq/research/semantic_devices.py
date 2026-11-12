"""One bounded, public-safe health record for the research meaning devices (#738)."""
from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from .. import vocab as output_vocab
from ..vocab import topic_checklists
from .packs import configured_packs, pack_snapshot


log = logging.getLogger("labhq.semantic_devices")

_ERRORS: dict[str, tuple[str, str, str]] = {
    "vocabulary_edam_invalid": ("EDAM map", "load", "EDAM map을 적용하지 못해 local vocabulary만 사용합니다."),
    "vocabulary_load_failed": ("단어장", "load", "단어장을 불러오지 못했습니다."),
    "checklist_load_failed": ("체크리스트", "load", "체크리스트를 불러오지 못했습니다."),
    "checklist_render_failed": ("체크리스트", "render", "체크리스트 판정을 보고서 부록에 만들지 못했습니다."),
    "device_check_failed": ("의미 장치", "check", "의미 장치를 점검하지 못했습니다."),
    "pack_load_failed": ("research pack", "load", "research pack을 불러오지 못했습니다."),
    "pack_snapshot_missing": ("research pack", "resume", "CP1에서 고정한 research pack snapshot을 복원하지 못했습니다."),
}


def _stamp(value: Any = None) -> str:
    try:
        ts = time.time() if value is None else float(value)
        return datetime.fromtimestamp(ts, timezone.utc).astimezone().isoformat(timespec="seconds")
    except (TypeError, ValueError, OSError, OverflowError):
        return "시각 미기록"


def _issue(code: str, *, at: str | None = None) -> dict[str, str]:
    device, stage, message = _ERRORS[code]
    return {"code": code, "device": device, "stage": stage, "at": at or _stamp(), "message": message}


def log_exception(code: str, exc: BaseException) -> None:
    """Log only a public error code and exception class, never its body or traceback."""
    safe_code = code if code in _ERRORS else "unknown_error"
    log.warning("meaning device failure %s (%s)", safe_code, type(exc).__name__)


def _checklist_digest(catalog: Mapping[str, list[Any]]) -> str:
    rows = [{"topic": topic, "id": item.id, "check": item.check, "why": item.why}
            for topic, items in sorted(catalog.items()) for item in items]
    return hashlib.sha256(json.dumps(rows, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def inspect(settings: Any, *, phase: str = "start") -> dict[str, Any]:
    """Keep core vocabulary, checklist and pack failures public-safe and request-scoped."""
    checked_at = _stamp()
    issues: list[dict[str, str]] = []
    vocabulary: dict[str, Any]
    vocab = None
    try:
        vocab = output_vocab.current()
        if vocab is None:
            raise ValueError("output type vocabulary unavailable")
        vocabulary = {"status": "ok" if not vocab.edam_problem else "error",
                      "sha256": vocab.sha256, "counts": vocab.counts(),
                      "edam_problem": vocab.edam_problem}
        if vocab.edam_problem:
            issues.append(_issue("vocabulary_edam_invalid", at=checked_at))
    except Exception as exc:  # noqa: BLE001 - the request records the device failure and continues to CP1
        log_exception("vocabulary_load_failed", exc)
        vocabulary = {"status": "error", "error_code": "vocabulary_load_failed"}
        issues.append(_issue("vocabulary_load_failed", at=checked_at))

    checklist: dict[str, Any]
    try:
        if vocab is None:
            raise ValueError("topic vocabulary unavailable")
        catalog = topic_checklists.load(vocab=vocab)
        checklist = {"status": "ok", "sha256": _checklist_digest(catalog),
                     "topics": len(catalog), "items": sum(len(items) for items in catalog.values())}
    except Exception as exc:  # noqa: BLE001 - explicit error replaces the old empty checklist
        log_exception("checklist_load_failed", exc)
        checklist = {"status": "error", "error_code": "checklist_load_failed"}
        issues.append(_issue("checklist_load_failed", at=checked_at))

    packs: dict[str, Any]
    try:
        loaded = configured_packs(settings)
        snapshot = pack_snapshot(loaded)
        packs = {"status": "ok", "count": len(snapshot), "sha256": snapshot}
    except Exception as exc:  # noqa: BLE001 - the CP1 card lets the PI choose whether to proceed
        log_exception("pack_load_failed", exc)
        packs = {"status": "error", "error_code": "pack_load_failed"}
        issues.append(_issue("pack_load_failed", at=checked_at))

    return {"version": 1, "phase": phase, "checked_at": checked_at,
            "status": "error" if issues else "ok", "vocabulary": vocabulary,
            "checklist": checklist, "packs": packs, "errors": issues}


def add_error(record: dict[str, Any], *, code: str) -> dict[str, Any]:
    if code not in _ERRORS:
        code = "device_check_failed"
    issue = _issue(code)
    errors = record.setdefault("errors", [])
    if not any(row.get("code") == code for row in errors if isinstance(row, Mapping)):
        errors.append(issue)
    record["status"] = "error"
    return issue


def card_line(record: Mapping[str, Any] | None) -> str:
    if not isinstance(record, Mapping):
        return ""
    labels = {"ok": "정상", "error": "오류"}
    rows = []
    for key, label in (("vocabulary", "단어장"), ("checklist", "점검표"), ("packs", "분야 팩")):
        device = record.get(key)
        status = device.get("status") if isinstance(device, Mapping) else None
        rows.append(f"{label} {labels.get(status, '미확인')}")
    return "의미 장치 상태: " + " · ".join(rows)


def warning(record: Mapping[str, Any] | None) -> str:
    errors = record.get("errors") if isinstance(record, Mapping) else None
    if not isinstance(errors, list) or not errors:
        return ""
    first = errors[0]
    extra = f" 외 {len(errors) - 1}건" if len(errors) > 1 else ""
    return (f"⚠ 의미 장치 중단: {first.get('device')} {first.get('code')}{extra} · "
            f"단계 {first.get('stage')} · 시각 {first.get('at')} · {first.get('message')}")


def execution_record(record: Mapping[str, Any] | None) -> str:
    headline = warning(record)
    if not headline:
        return "의미 장치 점검: 정상"
    errors = record.get("errors") or []
    lines = [headline, "", "| 오류 코드 | 장치 | 단계 | 시각 | 설명 |",
             "|---|---|---|---|---|"]
    lines += [f"| {row.get('code')} | {row.get('device')} | {row.get('stage')} | "
              f"{row.get('at')} | {row.get('message')} |" for row in errors]
    return "\n".join(lines)
