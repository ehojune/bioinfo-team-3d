"""Read-only comparison of saved observations and actual request progress (#559).

No model recomputation: missing observations remain missing. Free text, filenames and paths are not rendered.
"""
from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from . import semantics as sem
from . import semantics_shadow as shadow


def _dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _items(value: Any) -> list:
    return value if isinstance(value, list) else []


def build_request_report(settings: Any, rid: str) -> dict:
    if not re.fullmatch(r"req_[A-Za-z0-9]{1,64}", rid):
        raise ValueError("요청 ID는 req_로 시작하는 영문·숫자여야 합니다")
    root = settings.path(settings.gateway.state_dir)
    db = root / "gateway.sqlite3"
    if not db.exists():
        raise ValueError("요청을 찾지 못했습니다")
    # rid is constrained above; the existing reader handles live WAL and read-only closed stores.
    rows = sem._read_state(db, "SELECT kind, body FROM state WHERE "
                           f"(kind='request' AND key='{rid}') OR "
                           f"(kind='task' AND json_extract(body,'$.request_id')='{rid}') OR "
                           "(kind='approval_decision' AND "
                           f"json_extract(body,'$.approval.request_id')='{rid}')")
    records = [(kind, json.loads(body)) for kind, body in rows]
    req = next((value for kind, value in records if kind == "request"), None)
    if not isinstance(req, dict):
        raise ValueError("요청을 찾지 못했습니다")
    tasks = [value for kind, value in records if kind == "task" and isinstance(value, dict)]
    decisions = [value for kind, value in records if kind == "approval_decision" and isinstance(value, dict)]
    results = list(_dict(req.get("results")).values())
    # Direct requests may have no step results. Count their reported outputs once, from the task rows.
    output_results = results or [_dict(task.get("result")) for task in tasks if task.get("kind") == "direct"]
    actual = {
        "status": req.get("status"), "steps": len(_items(_dict(req.get("plan")).get("steps"))),
        "steps_done": sum(_dict(result).get("ok") is True for result in results),
        "steps_failed": sum(_dict(result).get("ok") is False for result in results),
        "tasks": len(tasks), "outputs_reported": sum(len(_items(_dict(r).get("outputs"))) for r in output_results),
        "approvals": {state: sum((d.get("state") or ("approved" if d.get("approved") else "denied")) == state
                                  for d in decisions) for state in ("approved", "denied", "expired", "timed_out")},
        "inputs": len(_items(req.get("references"))),
    }
    paths = shadow.ShadowPaths(root / "semantics")
    lines, broken = shadow.read_lines(paths)
    own = [line for line in lines if line.get("type") == "request" and line.get("request_id") == rid]
    valid = [line for line in own if shadow.readable_request(line)]
    observed = valid[-1] if valid else None
    warnings = []
    if not observed:
        warnings.append("그림자 기록 없음. 지금 다시 계산한 결과로 대신하지 않았습니다.")
    if broken or len(own) != len(valid):
        warnings.append(f"손상된 기록 제외: 요청 기록 {len(own) - len(valid)}건, 파일의 읽지 못한 줄 {broken}건")
    mode = shadow.configured(settings)
    if mode not in ("ab", "shadow"):
        warnings.append("현재 선택한 설정에서는 그림자 기록이 꺼져 있습니다.")
    try:
        disabled = shadow.read_disabled(paths)
    except (OSError, ValueError):
        disabled = {"reason": "unreadable"}
    if disabled:
        warnings.append("그림자 기록이 자동으로 중단된 상태입니다. 전체 report에서 이유를 확인하세요.")
    # Read the process identity without stale-file cleanup. Do not confuse today's config with history.
    from ..cli import _process_record
    process_state, record, _reason = _process_record(settings, "gateway", cleanup=False)
    mismatch = bool(record and record.get("config") != settings.config_path)
    if mismatch:
        prefix = "종료된 gateway" if process_state == "stale" else "gateway"
        warnings.append(f"{prefix} 실행 기록이 다른 설정 파일을 가리킵니다. 선택한 설정의 적용 여부를 확인하세요.")
    elif process_state == "unknown":
        warnings.append("gateway 실행 상태를 확인하지 못했습니다.")
    return {"request_id": rid, "shadow": observed, "observations": len(valid), "actual": actual,
            "semantic_devices": req.get("semantic_devices") if isinstance(req.get("semantic_devices"), dict) else None,
            "setting": mode, "config_mismatch": mismatch, "warnings": warnings}


_STATUS = {"done": "완료", "failed": "실패", "cancelled": "취소", "rejected": "거절",
           "running": "진행 중", "interrupted": "중단", "queued": "대기",
           "waiting_pi": "PI 응답 대기", "waiting_quota": "사용량 한도 대기", "waiting_login": "로그인 대기",
           "waiting_facilities_fix": "환경 수정 대기", "waiting_for_runner": "runner 연결 대기"}
_ACTIONS = {"approval.decide": "승인 결정", "ask.answer": "질문 답변", "request.followup": "이어 묻기",
            "task.cancel": "작업 취소", "recruit.start": "직원 추가", "contract.update": "계약 변경",
            "hpc.submit": "클러스터 작업 제출"}
_REASONS = {"type_unknown": "종류 모름", "hash_unknown": "파일 일치 여부 모름", "zone_excluded": "접근 범위 밖",
            "version_changed": "판본 변경", "not_generated": "생성 근거 없음", "incomplete": "계산 미완료",
            "input_unknown": "입력 모름", "input_mismatch": "입력 불일치", "target_type_unknown": "필요 종류 모름",
            "target_type_mismatch": "필요 종류 불일치"}


def _kinds(prov: Mapping[str, Any]) -> str:
    # Stored buckets are counts, not raw local declarations or file names.
    cells = _dict(_dict(prov.get("types")).get("data_type"))
    parts = []
    for bucket, bases in cells.items():
        label = {"local": "자체 분류", "unknown": "종류 모름", "withheld": "접근 범위 미확인"}.get(bucket, bucket)
        counts = _dict(bases)
        parts.append(f"{label} {sum(counts.values())}개")
    return ", ".join(parts) or "종류 기록 없음"


def render_request_report(rep: Mapping[str, Any]) -> str:
    actual, line = rep["actual"], rep["shadow"]
    out = [f"요청 {rep['request_id']} 비교", "저장된 그림자 관측과 현재 요청 기록을 비교합니다."]
    devices = rep.get("semantic_devices")
    if isinstance(devices, Mapping):
        from .semantic_devices import execution_record
        out += ["", execution_record(devices), ""]
    out.extend(rep["warnings"])
    if line:
        try:
            stamp = datetime.fromtimestamp(line.get("ts"), timezone.utc).isoformat(timespec="seconds")
        except (TypeError, ValueError, OverflowError, OSError):
            stamp = "기록 없음"
        out += [f"관측 시각 {stamp} · 기록 차수 {line.get('epoch', '?')} · 관측 {rep['observations']}건 중 마지막",
                "그림자가 본 것"]
        prov, objects = _dict(line.get("provenance")), _dict(line.get("objects"))
        fit = _dict(line.get("input_fit"))
        out.append("입력 종류: 종류별 목록은 당시 그림자 기록에 남기지 않음")
        out.append("입력과 작업 방법: " + (f"맞음 {fit.get('fit', 0)} · 불일치 {fit.get('mismatch', 0)} · "
                   f"모름 {fit.get('unknown', 0)}" if fit else "판정 기록 없음"))
        out.append("산출 종류: " + _kinds(prov))
        if prov.get("status") == "ok":
            out.append(f"재사용 후보 {prov.get('candidates', 0)}건 · " +
                       ("계산 미완료" if prov.get("incomplete") else "계산 완료"))
            excluded = _dict(prov.get("excluded"))
            out.append("후보 제외 이유: " + (", ".join(f"{_REASONS.get(k, '기타')} {n}" for k, n in excluded.items()
                                                    if n) or "없음"))
        else:
            out.append("재사용 후보: 계산 실패 또는 시간 초과로 판정하지 못함")
        out.append(f"연결을 확인하지 못한 항목: {objects.get('unresolved', '기록 없음')}")
        if objects.get("status") != "ok":
            out.append("대상과 연결: 계산 실패 또는 시간 초과로 판정하지 못함")
        if line.get("arm"):
            out.append("계획 참고 배정: " + ("후보를 보여 주는 쪽" if line["arm"] == "advisory" else "관측만 하는 쪽"))
        else:
            out.append("계획 참고 배정: 해당 없음. 일반 요청도 그림자는 기록합니다.")
        out.append("행동 판단: 관측 시점의 가능 여부이며 실행 지시가 아닙니다.")
        acts = _dict(line.get("actions"))
        shape_check = getattr(shadow, "_actions_shape", None)
        if acts.get("status") == "ok" and shape_check and not shape_check(line):
            for name, tally in _dict(acts.get("now")).items():
                out.append(f"  {_ACTIONS.get(name, '기타 행동')}: 가능 {tally.get('open', 0)} · "
                           f"불가 {tally.get('blocked', 0)} · 모름 {tally.get('unknown', 0)}")
            out.append("  클러스터 작업 제출: 그림자가 실행하지 않음")
        else:
            out.append("  판단 기록 없음 또는 계산 실패")
    else:
        out += ["그림자가 본 것: 입력·산출 종류, 재사용 후보, 경고, 행동 판단 모두 확인 불가"]
    approvals = actual["approvals"]
    out += ["실제 진행", f"상태: {_STATUS.get(actual['status'], '진행 상태 확인 필요')}",
            f"단계 {actual['steps']}개 · 완료 {actual['steps_done']} · 실패 {actual['steps_failed']} · 직원 실행 {actual['tasks']}건",
            f"요청에 등록한 입력 {actual['inputs']}개 · 보고된 산출 {actual['outputs_reported']}개",
            f"결정: 승인 {approvals['approved']} · 거절 {approvals['denied']} · 만료 {approvals['expired']} · 시간 초과 {approvals.get('timed_out', 0)}",
            "종류는 선언·이름표이고 산출은 직원 보고 기준입니다. 파일 내용이나 재현성을 검증한 결과는 아닙니다.",
            "현재 설정과 실행 기록은 과거 관측 시점의 설정을 증명하지 않습니다."]
    return "\n".join(out)
