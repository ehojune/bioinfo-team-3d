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


REENABLE = "labhq semantics enable"
DEVICE = "seman" + "tics"
log = logging.getLogger("labhq.semantic_devices")

_ERRORS: dict[str, tuple[str, str, str]] = {
    "configured_off": (DEVICE, "setting", "semantics 평가가 설정으로 꺼져 있습니다."),
    "semantics_config_invalid": (DEVICE, "startup", "semantics 설정을 읽지 못했습니다. 설정을 고친 뒤 gateway를 다시 시작하세요."),
    "semantics_worker_unavailable": (DEVICE, "startup", "semantics worker를 시작하지 못했습니다. doctor 결과를 확인하세요."),
    "vocabulary_edam_invalid": ("EDAM map", "load", "EDAM map을 적용하지 못해 local vocabulary만 사용합니다."),
    "vocabulary_load_failed": ("단어장", "load", "단어장을 불러오지 못했습니다."),
    "checklist_load_failed": ("체크리스트", "load", "체크리스트를 불러오지 못했습니다."),
    "checklist_render_failed": ("체크리스트", "render", "체크리스트 판정을 보고서 부록에 만들지 못했습니다."),
    "pack_load_failed": ("research pack", "load", "research pack을 불러오지 못했습니다."),
    "pack_snapshot_missing": ("research pack", "resume", "CP1에서 고정한 research pack snapshot을 복원하지 못했습니다."),
    "semantics_advisory_snapshot_failed": (DEVICE, "advisory_snapshot", "재사용 후보 snapshot을 만들지 못했습니다."),
    "semantics_observed_state_failed": (DEVICE, "advisory_projection", "재사용 후보 관측 상태를 읽지 못했습니다."),
    "semantics_provenance_failed": (DEVICE, "advisory_projection", "재사용 후보 provenance 요약을 만들지 못했습니다."),
    "semantics_advisory_projection_failed": (DEVICE, "advisory_projection", "재사용 후보를 평가하지 못했습니다."),
    "semantics_advisory_boundary_failed": (DEVICE, "advisory_boundary", "재사용 후보가 정보 경계를 통과하지 못했습니다."),
}
_STOP_REASONS = frozenset({"info_boundary", "consecutive_failures", "recent_failures", "worker_stuck",
                           "worker_busy", "wrong_identity", "breaker_storage", "sqlite_json1_missing"})


def _semantics_state(settings: Any) -> dict[str, Any]:
    """Keep every spelling of ``off`` from importing the removable semantics model."""
    raw = getattr(settings, DEVICE, None)
    mode = raw.get("mode", "off") if isinstance(raw, Mapping) else raw
    if mode in (None, False, "off"):
        return {"setting": "off", "stopped": False, "reason": None, "stopped_at": None,
                "epoch": None, "last_recorded_at": None, "last_ok_at": None, "boundary": [],
                "reenable_command": REENABLE, "legacy_false_positive": False}
    from . import semantics_shadow  # semantics-hook
    return semantics_shadow.runtime_state(settings)  # semantics-hook


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


def inspect(settings: Any, *, service: Any = None, worker_known: bool = False,
            phase: str = "start") -> dict[str, Any]:
    """Load every device and return only hashes, counts and public-safe errors.

    ``worker_known`` distinguishes a live gateway check from doctor, which cannot inspect another
    process's worker object. No request text, path, vocabulary definition or pack body is retained.
    """
    checked_at = _stamp()
    issues: list[dict[str, str]] = []
    sem = _semantics_state(settings)
    setting = sem["setting"]
    if sem["stopped"]:
        sem_status = "off"
        reason = str(sem["reason"] or "")
        code = reason if reason in _STOP_REASONS else "semantics_auto_off"
        _ERRORS.setdefault(code, (DEVICE, "auto_off", f"semantics 평가가 자동으로 중단됐습니다. {REENABLE}로 다시 켜세요."))
        issues.append(_issue(code, at=sem["stopped_at"] or checked_at))
    elif setting in {"invalid", "refused"}:
        sem_status = "error"
        issues.append(_issue("semantics_config_invalid", at=checked_at))
    elif setting == "off":
        sem_status = "off"
        issues.append(_issue("configured_off", at=checked_at))
    elif worker_known and service is None:
        sem_status = "error"
        issues.append(_issue("semantics_worker_unavailable", at=checked_at))
    elif service is not None and getattr(service, "latched", None):
        sem_status = "off"
        reason = str(service.latched)
        code = reason if reason in _STOP_REASONS else "semantics_auto_off"
        _ERRORS.setdefault(code, (DEVICE, "auto_off", f"semantics 평가가 자동으로 중단됐습니다. {REENABLE}로 다시 켜세요."))
        issues.append(_issue(code, at=checked_at))
    else:
        sem_status = "on"
    sem_record = {**sem, "status": sem_status,
                  "worker": ("running" if service is not None and not getattr(service, "latched", None)
                             else "off" if service is not None else "unknown")}

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

    status = "error" if any(item["stage"] not in {"setting", "auto_off"} for item in issues) else (
        "warning" if issues else "ok")
    return {"version": 1, "phase": phase, "checked_at": checked_at, "status": status,
            DEVICE: sem_record, "vocabulary": vocabulary, "checklist": checklist,
            "packs": packs, "errors": issues}


def add_error(record: dict[str, Any], *, code: str) -> dict[str, Any]:
    if code not in _ERRORS:
        code = "semantics_advisory_projection_failed"
    issue = _issue(code)
    errors = record.setdefault("errors", [])
    if not any(row.get("code") == code for row in errors if isinstance(row, Mapping)):
        errors.append(issue)
    record["status"] = "error"
    return issue


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
