"""One bounded, public-safe health record for the research meaning devices (#738)."""
from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from .. import vocab as output_vocab
from ..vocab import topic_checklists
from .packs import configured_packs, pack_snapshot


REENABLE = "labhq semantics enable"
DEVICE = "seman" + "tics"
_PATH = re.compile(r"(?i)(?:[a-z]:[\\/]|~[\\/]|/(?:[^/\s]+/)+)[^\s,;]*")
_SECRET = re.compile(r"(?i)\b(?:authorization|bearer|api[_-]?key|access[_-]?token|secret|password)\s*[:=]\s*\S+")


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


def _reason(exc: BaseException) -> str:
    text = " ".join(str(exc).split()) or type(exc).__name__
    text = _SECRET.sub("[가림]", text)
    text = _PATH.sub("<경로>", text)
    return f"{type(exc).__name__}: {text}"[:240]


def _issue(device: str, path: str, reason: str, *, at: str, impact: str,
           recovery: str) -> dict[str, str]:
    return {"device": device, "path": path, "reason": reason, "at": at,
            "impact": impact, "recovery": recovery}


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
        impact = "재사용 후보·객체 연결·행동 판단을 평가하지 않음"
        issues.append(_issue(DEVICE, "auto_off", str(sem["reason"] or "disabled"),
                             at=sem["stopped_at"] or checked_at, impact=impact, recovery=REENABLE))
    elif setting in {"invalid", "refused"}:
        sem_status = "error"
        issues.append(_issue(DEVICE, "startup", setting, at=checked_at,
                             impact="semantics worker가 시작되지 않음", recovery="semantics 설정을 고친 뒤 gateway를 다시 시작하세요"))
    elif setting == "off":
        sem_status = "off"
        issues.append(_issue(DEVICE, "setting", "configured_off", at=checked_at,
                             impact="semantics 평가를 하지 않음", recovery="semantics.mode를 shadow 또는 ab로 바꾸고 gateway를 다시 시작하세요"))
    elif worker_known and service is None:
        sem_status = "error"
        issues.append(_issue(DEVICE, "worker", "worker_unavailable", at=checked_at,
                             impact="semantics 평가를 시작하지 못함", recovery="doctor 결과를 확인한 뒤 gateway를 다시 시작하세요"))
    elif service is not None and getattr(service, "latched", None):
        sem_status = "off"
        reason = str(service.latched)
        issues.append(_issue(DEVICE, "worker", reason, at=checked_at,
                             impact="semantics 평가를 하지 않음", recovery=REENABLE))
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
            issues.append(_issue("EDAM map", "vocab/edam_subset", vocab.edam_problem, at=checked_at,
                                 impact="EDAM 식별자를 붙이지 않고 local vocabulary만 사용",
                                 recovery="EDAM subset을 고친 뒤 gateway를 다시 시작하세요"))
    except Exception as exc:  # noqa: BLE001 - the request records the device failure and continues to CP1
        vocabulary = {"status": "error", "reason": _reason(exc)}
        issues.append(_issue("단어장", "vocab/output_types", _reason(exc), at=checked_at,
                             impact="topic·산출 종류 정규화를 적용하지 못함",
                             recovery="단어장 파일을 고친 뒤 같은 요청을 다시 보내세요"))

    checklist: dict[str, Any]
    try:
        if vocab is None:
            raise ValueError("topic vocabulary unavailable")
        catalog = topic_checklists.load(vocab=vocab)
        checklist = {"status": "ok", "sha256": _checklist_digest(catalog),
                     "topics": len(catalog), "items": sum(len(items) for items in catalog.values())}
    except Exception as exc:  # noqa: BLE001 - explicit error replaces the old empty checklist
        checklist = {"status": "error", "reason": _reason(exc)}
        issues.append(_issue("체크리스트", "vocab/topic_checklists", _reason(exc), at=checked_at,
                             impact="topic별 점검 항목을 계획·리뷰·부록에 적용하지 못함",
                             recovery="체크리스트를 고친 뒤 같은 요청을 다시 보내세요"))

    packs: dict[str, Any]
    try:
        loaded = configured_packs(settings)
        snapshot = pack_snapshot(loaded)
        packs = {"status": "ok", "count": len(snapshot), "sha256": snapshot}
    except Exception as exc:  # noqa: BLE001 - the CP1 card lets the PI choose whether to proceed
        packs = {"status": "error", "reason": _reason(exc)}
        issues.append(_issue("research pack", "research/packs", _reason(exc), at=checked_at,
                             impact="configured pack 규칙과 hash를 계획에 적용하지 못함",
                             recovery="pack 설정·파일을 고친 뒤 같은 요청을 다시 보내세요"))

    status = "error" if any(item["path"] not in {"setting", "auto_off"} for item in issues) else (
        "warning" if issues else "ok")
    return {"version": 1, "phase": phase, "checked_at": checked_at, "status": status,
            DEVICE: sem_record, "vocabulary": vocabulary, "checklist": checklist,
            "packs": packs, "errors": issues}


def add_error(record: dict[str, Any], *, device: str, path: str, reason: str,
              impact: str, recovery: str) -> dict[str, Any]:
    issue = _issue(device, path, reason, at=_stamp(), impact=impact, recovery=recovery)
    errors = record.setdefault("errors", [])
    if not any((row.get("device"), row.get("path"), row.get("reason")) ==
               (device, path, reason) for row in errors if isinstance(row, Mapping)):
        errors.append(issue)
    record["status"] = "error"
    return issue


def warning(record: Mapping[str, Any] | None) -> str:
    errors = record.get("errors") if isinstance(record, Mapping) else None
    if not isinstance(errors, list) or not errors:
        return ""
    first = errors[0]
    extra = f" 외 {len(errors) - 1}건" if len(errors) > 1 else ""
    return (f"⚠ 의미 장치 중단: {first.get('device')} {first.get('reason')}{extra} · "
            f"시각 {first.get('at')} · 영향 {first.get('impact')} · 다시 켜기: {first.get('recovery')}")


def execution_record(record: Mapping[str, Any] | None) -> str:
    headline = warning(record)
    if not headline:
        return "의미 장치 점검: 정상"
    errors = record.get("errors") or []
    lines = [headline, "", "| 장치 | 경로 | 오류·사유 | 영향 | 복구 |",
             "|---|---|---|---|---|"]
    lines += [f"| {row.get('device')} | {row.get('path')} | {row.get('reason')} | "
              f"{row.get('impact')} | {row.get('recovery')} |" for row in errors]
    return "\n".join(lines)
