"""Request-scoped account of what the six meaning devices actually did (#737)."""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from .. import vocab as output_vocab
from ..vocab import topic_checklists


REPORT_MD = "semantic_report.md"
REPORT_JSON = "semantic_report.json"
STATUSES = ("작동", "켜졌으나 미사용", "꺼짐", "오류")
_MEANING_KEY = 'semantics'
_SHADOW_MODULE = f"labhq/research/{_MEANING_KEY}_shadow.py"
_OBJECTS_MODULE = f"labhq/research/{_MEANING_KEY}_objects.py"
_INPUT_FIT_MODULE = f"labhq/research/{_MEANING_KEY}_input_fit.py"
def enabled(settings: Any) -> bool:
    return bool(getattr(getattr(settings, "research", None), "semantic_report", True))


def applies(req: Mapping[str, Any], settings: Any) -> bool:
    """The device audit belongs only to requests that entered the research contract."""
    return enabled(settings) and isinstance(req.get("research_contract"), Mapping)


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _public(value: Any, settings: Any, req: Mapping[str, Any]) -> Any:
    if isinstance(value, str):
        # Reuse the publication cleaner shared by portfolio and result storage.
        from ..portfolio import clean_publication_text
        return clean_publication_text(REPORT_JSON, value, settings, dict(req))
    if isinstance(value, Mapping):
        return {str(key): _public(item, settings, req) for key, item in value.items()}
    if isinstance(value, list):
        return [_public(item, settings, req) for item in value]
    return value


def _stamp(value: Any) -> str:
    try:
        return datetime.fromtimestamp(float(value), timezone.utc).astimezone().isoformat(timespec="seconds")
    except (TypeError, ValueError, OSError, OverflowError):
        return "시각 미기록"


def _mode(settings: Any, devices: Mapping[str, Any]) -> str:
    health = _dict(devices.get(_MEANING_KEY))
    if isinstance(health.get("setting"), str):
        return health["setting"]
    raw = getattr(settings, _MEANING_KEY, None)
    value = raw.get("mode", "off") if isinstance(raw, Mapping) else raw
    return str(value) if value not in (None, False, "") else "off"


def _safe_error(row: Any) -> dict[str, str] | None:
    if not isinstance(row, Mapping):
        return None
    fields = ("code", "device", "stage", "at", "message")
    return {field: str(row.get(field) or "기록 없음")[:240] for field in fields}


def _errors(devices: Mapping[str, Any]) -> list[dict[str, str]]:
    return [safe for row in devices.get("errors") or [] if (safe := _safe_error(row)) is not None]


def _error_for(errors: list[dict[str, str]], *names: str) -> dict[str, str] | None:
    folded = {name.casefold() for name in names}
    return next((row for row in errors if row["device"].casefold() in folded), None)


def _off_record(devices: Mapping[str, Any], errors: list[dict[str, str]], mode: str,
                request_time: str) -> dict[str, str] | None:
    health = _dict(devices.get(_MEANING_KEY))
    error = _error_for(errors, _MEANING_KEY)
    if error and error["stage"] in {"setting", "auto_off", "startup"}:
        return {"reason": error["code"], "at": error["at"]}
    if health.get("stopped") or health.get("status") == "off":
        return {"reason": str(health.get("reason") or "auto_off"),
                "at": str(health.get("stopped_at") or request_time)}
    if mode == "off":
        return {"reason": "configured_off", "at": request_time}
    return None


def _valid_observation(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    models = [value.get(key) for key in ("provenance", "objects", "input_fit")]
    if not all(model is None or isinstance(model, Mapping) for model in models):
        return False
    provenance = _dict(value.get("provenance"))
    fit = _dict(value.get("input_fit"))
    candidates = provenance.get("candidates", 0)
    excluded = provenance.get("excluded", {})
    return (isinstance(candidates, int) and not isinstance(candidates, bool) and candidates >= 0
            and isinstance(excluded, Mapping)
            and all(isinstance(item, int) and not isinstance(item, bool) and item >= 0
                    for item in excluded.values())
            and all(isinstance(item, int) and not isinstance(item, bool) and item >= 0
                    for item in fit.values())
            and isinstance(value.get("referenced"), (bool, type(None))))


def _read_observation(req: Mapping[str, Any], settings: Any, mode: str) -> tuple[dict[str, Any] | None, str | None]:
    fixed = req.get("semantics_observation")
    if isinstance(fixed, Mapping):
        return ((dict(fixed), None) if _valid_observation(fixed)
                else (None, "현재 요청 관측 구조 손상"))
    if req.get("semantics_observation_error"):
        return None, str(req["semantics_observation_error"])
    if mode not in {"shadow", "ab"}:
        return None, None
    try:
        root = settings.path(settings.gateway.state_dir) / _MEANING_KEY
        own: list[dict[str, Any]] = []
        for path in (root / "shadow.1.jsonl", root / "shadow.jsonl"):
            try:
                text = path.read_text(encoding="utf-8")
            except FileNotFoundError:
                continue
            for raw in text.splitlines():
                if not raw.strip():
                    continue
                try:
                    value = json.loads(raw)
                except ValueError:
                    continue
                if (isinstance(value, dict) and value.get("type") == "request"
                        and value.get("request_id") == req.get("id")):
                    own.append(value)
        if not own:
            return None, None
        latest = own[-1]
        return ((dict(latest), None) if _valid_observation(latest)
                else (None, "현재 요청 관측 구조 손상"))
    except (OSError, TypeError, ValueError):
        return None, "관측 파일 읽기 실패"


def _plan_refs(plan: Mapping[str, Any]) -> set[str]:
    return {ref for step in plan.get("steps") or [] if isinstance(step, Mapping)
            for ref in step.get("input_refs") or [] if isinstance(ref, str)}


def _count_tree(value: Any) -> Any:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, Mapping):
        return {str(key): counted for key, item in value.items() if (counted := _count_tree(item)) is not None}
    return None


def _model_failure(model: Mapping[str, Any], *, code: str, device: str, stage: str,
                   label: str, at: str) -> dict[str, str] | None:
    status = model.get("status")
    if status in {None, "ok"}:
        return None
    outcome = "timeout으로" if status == "timeout" else "error로"
    return {
        "code": code, "device": device, "stage": stage, "at": at,
        "message": f"{label} 관측이 {outcome} 끝나 계산하지 못했습니다.",
    }


def _append_error(errors: list[dict[str, str]], issue: dict[str, str] | None) -> dict[str, str] | None:
    if issue is None:
        return None
    previous = next((row for row in errors if row.get("code") == issue["code"]), None)
    if previous is not None:
        return previous
    errors.append(issue)
    return issue


def _device(device_id: str, name: str, status: str, *, inputs: list[str], decisions: list[Any], impact: str,
            code: list[tuple[str, str]], records: list[str], **extra: Any) -> dict[str, Any]:
    return {
        "id": device_id, "name": name, "status": status, "inputs": inputs,
        "decisions": decisions or ["판단 기록 없음"], "impact": impact,
        "code_locations": [{"module": module, "function": function} for module, function in code],
        "record_locations": records, **extra,
    }


def _vocabulary_device(plan: Mapping[str, Any], errors: list[dict[str, str]]) -> dict[str, Any]:
    error = _error_for(errors, "단어장", "EDAM map")
    topics = [value for value in plan.get("topics") or [] if isinstance(value, str)]
    declared = [{key: value for key, value in row.items() if key in {"name", "data_type", "format"}}
                for step in plan.get("steps") or [] if isinstance(step, Mapping)
                for row in step.get("output_types") or [] if isinstance(row, Mapping)]
    decisions: list[Any] = []
    try:
        vocab = output_vocab.current()
        if vocab is None:
            raise ValueError("vocabulary_unavailable")
        selected = sorted(set(topics + [str(row[field]) for row in declared for field in ("data_type", "format")
                                        if isinstance(row.get(field), str)]))
        decisions = [{"key": key, "edam_id": vocab.edam_id(key) or "local"} for key in selected]
        if vocab.edam_problem:
            decisions.append({"edam_map": "ignored", "reason": vocab.edam_problem})
    except (OSError, TypeError, ValueError) as exc:
        error = error or {"device": "단어장", "path": "vocab", "reason": type(exc).__name__,
                          "at": "시각 미기록", "impact": "topic·산출 종류를 판정하지 못함", "recovery": "단어장 점검"}
    status = "오류" if error else "작동" if topics or declared else "켜졌으나 미사용"
    return _device(
        "vocabulary_edam", "단어장·EDAM", status,
        inputs=["plan.topics", "plan.steps[].output_types"],
        decisions=decisions or [{"unused_reason": "plan.topics와 plan.steps[].output_types 선언 없음"}],
        impact=("고른 topic과 자료·산출 종류를 계획에 고정" if status == "작동" else
                "topic·자료·산출 종류에 미친 영향 없음"),
        code=[("labhq/vocab/topics.py", "normalize"), ("labhq/vocab/declare.py", "normalize_entries"),
              ("labhq/vocab/__init__.py", "Vocab.edam_id")],
        records=["semantic_report.json:devices[vocabulary_edam]", "request.plan.topics",
                 "request.plan.steps[].output_types", "request.output_types_stats"],
        topics=topics, output_types=declared, **({"error": error} if error else {}),
    )


def _checklist_device(req: Mapping[str, Any], plan: Mapping[str, Any], errors: list[dict[str, str]]) -> dict[str, Any]:
    error = _error_for(errors, "체크리스트")
    items: list[dict[str, Any]] = []
    try:
        contract = _dict(req.get("research_contract"))
        frozen = contract.get("checklist_snapshot")
        if isinstance(frozen, list):
            required = [topic_checklists.ChecklistItem(
                str(row["id"]), str(row["check"]), str(row["why"]), str(row["topic"]))
                for row in frozen if isinstance(row, Mapping)]
        else:
            required = topic_checklists.requirements(plan.get("topics"), topic_checklists.load())
        answers = plan.get("checklist") if isinstance(plan.get("checklist"), Mapping) else {}
        review = _dict(req.get("review")) or _dict(_dict(req.get("research_contract")).get("review"))
        verdicts = topic_checklists.review_verdicts(review.get("checklist"), required)
        for item in required:
            slots = topic_checklists.check_slots(answers, item, verdicts)
            for slot in slots:
                answer = slot.answer
                items.append({
                    "id": item.id if not slot.topic else f"{slot.topic}.{item.id}", "topic": slot.topic or item.topic,
                    "plan_included": bool(answer), "plan_answer": answer or "누락",
                    "owner_steps": list(slot.steps), "review_status": slot.status or "미판정",
                    "review_evidence": slot.evidence or "기록 없음",
                })
    except (OSError, TypeError, ValueError) as exc:
        error = error or {"device": "체크리스트", "path": "vocab/topic_checklists", "reason": type(exc).__name__,
                          "at": "시각 미기록", "impact": "항목별 계획·리뷰 판정을 만들지 못함", "recovery": "체크리스트 점검"}
    status = "오류" if error else "작동" if items else "켜졌으나 미사용"
    decisions = [{"id": item["id"], "plan_included": item["plan_included"],
                  "owner_steps": item["owner_steps"], "review_status": item["review_status"]} for item in items]
    return _device(
        "checklist", "체크리스트", status, inputs=["plan.topics", "plan.checklist", "review.checklist"],
        decisions=decisions or [{"unused_reason": "선택한 topic에 checklist 항목이 없거나 topic을 고르지 않음"}],
        impact=("담당 단계와 reviewer 판정을 항목별로 추적" if items else "계획에 적용할 항목 없음"),
        code=[("labhq/vocab/topic_checklists.py", "requirements"),
              ("labhq/vocab/topic_checklists.py", "check_slots"),
              ("labhq/orchestrator/cso.py", "checklist_appendix")],
        records=["semantic_report.json:devices[checklist].items", "request.plan.checklist",
                 "request.research_contract.review.checklist", "report_appendix.md"],
        items=items, **({"error": error} if error else {}),
    )


def _pack_device(req: Mapping[str, Any], plan: Mapping[str, Any], errors: list[dict[str, str]]) -> dict[str, Any]:
    error = _error_for(errors, "research pack", "분석 pack")
    contract = _dict(req.get("research_contract"))
    raw = contract.get("pack_applicability", plan.get("pack_applicability"))
    applicability = _dict(raw)
    decisions = [{"pack": key, "applied": bool(_dict(value).get("applied")),
                  "matched_topics": [topic for topic in _dict(value).get("matched_topics") or []
                                     if isinstance(topic, str)],
                  "reason": str(_dict(value).get("reason") or "기록 없음")}
                 for key, value in sorted(applicability.items())]
    status = "오류" if error else "작동" if decisions else "켜졌으나 미사용"
    applied = [row["pack"] for row in decisions if row["applied"]]
    excluded = [row["pack"] for row in decisions if not row["applied"]]
    return _device(
        "analysis_pack", "분석 pack", status,
        inputs=["plan.topics", "plan.pack_values", "research_contract.pack_applicability"],
        decisions=decisions or [{"unused_reason": "pack_applicability 기록 없음; 적용한 configured pack 없음"}],
        impact=(f"적용 {len(applied)}개·제외 {len(excluded)}개를 고정 계획에 반영" if decisions else "계획에 적용한 pack 없음"),
        code=[("labhq/research/packs.py", "assess_pack_applicability"),
              ("labhq/research/packs.py", "select_applied_packs"),
              ("labhq/orchestrator/cso.py", "Orchestrator.run_request")],
        records=["semantic_report.json:devices[analysis_pack]", "request.plan.pack_applicability",
                 "request.research_contract.pack_applicability", "request.research_contract.pack_snapshot"],
        applied=applied, excluded=excluded, **({"error": error} if error else {}),
    )


def _semantic_devices(req: Mapping[str, Any], plan: Mapping[str, Any], settings: Any,
                      health: Mapping[str, Any], errors: list[dict[str, str]], request_time: str
                      ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    mode = _mode(settings, health)
    observation, observation_error = _read_observation(req, settings, mode)
    observation_issue = None
    if observation_error:
        observation_issue = {
            "code": "semantics_observed_state_failed", "device": _MEANING_KEY,
            "stage": "advisory_projection", "at": request_time,
            "message": "현재 요청의 의미 관측을 읽지 못해 요청별 평가를 완료하지 못했습니다.",
        }
        if not any(row.get("code") == observation_issue["code"] for row in errors):
            errors.append(observation_issue)
    off = _off_record(health, errors, mode, request_time)
    semantic_error = _error_for(errors, _MEANING_KEY)
    if semantic_error and semantic_error.get("stage") in {"setting", "auto_off", "startup"}:
        semantic_error = None
    provenance = _dict((observation or {}).get("provenance"))
    objects = _dict((observation or {}).get("objects"))
    fit = _dict((observation or {}).get("input_fit"))
    observation_at = _stamp((observation or {}).get("ts"))
    provenance_error = _append_error(errors, _model_failure(
        provenance, code=f"{_MEANING_KEY}_shadow_provenance_failed", device=_MEANING_KEY,
        stage="shadow_provenance", label="재사용 후보 provenance", at=observation_at,
    )) if observation else None
    objects_error = _append_error(errors, _model_failure(
        objects, code=f"{_MEANING_KEY}_shadow_objects_failed", device="운영 ontology",
        stage="shadow_objects", label="객체·관계", at=observation_at,
    )) if observation else None

    def model_status(model_error: Mapping[str, Any] | None) -> str:
        if semantic_error or observation_issue or model_error:
            return "오류"
        if off:
            return "꺼짐"
        return "작동" if observation else "켜졌으나 미사용"

    sem_status = model_status(provenance_error)
    sem_decisions: list[Any] = []
    if observation and not provenance_error:
        sem_decisions.append({"input_fit": {key: int(fit.get(key) or 0) for key in ("fit", "mismatch", "unknown")}})
        sem_decisions.append({"reuse_candidates": int(provenance.get("candidates") or 0),
                              "excluded": {str(key): int(value)
                                           for key, value in _dict(provenance.get("excluded")).items()
                                           if isinstance(value, int)}})
    elif provenance_error:
        sem_decisions.append("재사용 후보 provenance 계산 못 함")
    elif off:
        sem_decisions.append({"off": off})
    else:
        sem_decisions.append("평가 안 됨: 요청별 관측 기록 없음")
    semantics = _device(
        _MEANING_KEY, _MEANING_KEY, sem_status,
        inputs=["request.references", "plan.steps[].input_refs", "results[].output_types", "semantics shadow 기록"],
        decisions=sem_decisions,
        impact=("입력 적합도와 재사용 후보를 계산하지 못함" if provenance_error else
                "입력 적합도와 재사용 후보를 계산" if observation else
                "요청 행동에 미친 영향 없음; 평가 안 됨"),
        code=[(_SHADOW_MODULE, "compute_provenance"), (_INPUT_FIT_MODULE, "evaluate")],
        records=["semantic_report.json:devices[semantics]", "state/semantics/shadow.jsonl",
                 "request.semantic_devices.errors"],
        observation_at=observation_at, epoch=(observation or {}).get("epoch"),
        **({"off": off} if off else {}),
        **({"error": provenance_error or semantic_error or observation_issue}
           if provenance_error or semantic_error or observation_issue else {}),
    )

    ontology_status = model_status(objects_error)
    count_fields = ("objects", "links", "artifact_types", "link_total", "unresolved",
                    "unresolved_by", "pending_approvals", "pending_jobs")
    object_counts = {key: counted for key in count_fields if key in objects
                     and (counted := _count_tree(objects[key])) is not None}
    ontology_decisions: list[Any] = (["객체·관계 집계 계산 못 함"] if objects_error else
                                     [{"object_relation_counts": object_counts}]
                                     if observation else [{"off": off}] if off else
                                     ["평가 안 됨: 요청별 객체·관계 기록 없음"])
    ontology = _device(
        "operational_ontology", "운영 ontology", ontology_status,
        inputs=["plan", "results", "승인·task 상태의 정규화 객체와 관계"], decisions=ontology_decisions,
        impact=("객체·관계와 미해결 항목을 계산하지 못함" if objects_error else
                "객체·관계와 미해결 항목을 집계" if observation else
                "CSO·직원 행동에 미친 영향 없음; 평가 안 됨"),
        code=[(_SHADOW_MODULE, "compute_objects"),
              (_OBJECTS_MODULE, "build_view")],
        records=["semantic_report.json:devices[operational_ontology]", "state/semantics/shadow.jsonl"],
        **({"off": off} if off else {}),
        **({"error": objects_error or semantic_error or observation_issue}
           if objects_error or semantic_error or observation_issue else {}),
    )

    ab = _dict(req.get("semantics_ab"))
    ab_error = _dict(ab.get("error")) if ab.get("status") == "error" else {}
    offered = [value for value in ab.get("offered") or [] if isinstance(value, str)]
    evaluated = bool(ab) and not ab_error
    referenced = ((observation or {}).get("referenced")
                  if isinstance((observation or {}).get("referenced"), bool)
                  else bool(set(offered) & _plan_refs(plan)) if evaluated else None)
    shadow_off = off or ({"reason": "mode_not_ab", "at": request_time} if mode != "ab" else None)
    shadow_status = ("오류" if ab_error else "꺼짐" if shadow_off else "작동" if evaluated
                     else "켜졌으나 미사용")
    evaluation = "평가 완료" if evaluated else "평가 안 됨"
    shadow = _device(
        "shadow_request", "그림자 요청", shadow_status,
        inputs=["첫 연구 plan", "semantics advisory 후보", "재계획 plan.input_refs"],
        decisions=[{"arm": ab.get("arm") or "기록 없음", "evaluation": evaluation,
                    "offered_count": len(offered), "referenced": referenced}],
        impact=("후보를 advisory 재계획에 제시" if evaluated and offered and ab.get("arm") == "advisory" else
                "후보가 없어 계획 변화 없음" if evaluated else "계획 변화 평가 안 됨"),
        code=[(_SHADOW_MODULE, "ShadowService.advisory_offer"),
              ("labhq/orchestrator/cso.py", "Orchestrator.run_request"),
              (_SHADOW_MODULE, "_add_ab_fields")],
        records=["semantic_report.json:devices[shadow_request]", f"request.{_MEANING_KEY}_ab",
                 "state/semantics/shadow.jsonl"],
        arm=ab.get("arm"), evaluation=evaluation, offered_count=len(offered), referenced=referenced,
        offered=offered,
        **({"off": shadow_off} if shadow_off else {}), **({"error": ab_error} if ab_error else {}),
    )
    return semantics, ontology, shadow


def build_report(req: Mapping[str, Any], settings: Any) -> dict[str, Any]:
    plan = _dict(req.get("plan"))
    health = _dict(req.get("semantic_devices"))
    errors = _errors(health)
    request_time = _stamp(req.get("finished_at") or req.get("updated_at") or req.get("created_at"))
    semantics, ontology, shadow = _semantic_devices(req, plan, settings, health, errors, request_time)
    devices = [_vocabulary_device(plan, errors), _checklist_device(req, plan, errors),
               _pack_device(req, plan, errors), semantics, ontology, shadow]
    mode = _mode(settings, health)
    contract = _dict(req.get("research_contract"))
    config_basis = {"semantic_report": enabled(settings), "semantics_mode": mode,
                    "pack_snapshot": _dict(contract.get("pack_snapshot")),
                    "checklist_snapshot": contract.get("checklist_snapshot"),
                    "vocab": _dict(req.get("output_types_stats")).get("vocab")}
    config_hash = hashlib.sha256(json.dumps(config_basis, ensure_ascii=False, sort_keys=True,
                                            separators=(",", ":")).encode()).hexdigest()
    report = {
        "version": 1, "request_id": str(req.get("id") or "기록 없음"), "generated_at": request_time,
        "execution_conditions": {
            "labhq_commit": str(req.get("labhq_commit") or "기록 없음"),
            "semantic_config_sha256": config_hash, "semantics_mode": mode,
            "last_ok_at": _dict(health.get(_MEANING_KEY)).get("last_ok_at") or "기록 없음",
        },
        "devices": devices, "errors": errors,
        "paths": {"markdown": REPORT_MD, "json": REPORT_JSON, "published": f"run/{REPORT_MD}"},
    }
    return _public(report, settings, req)


def freeze_observation(req: dict[str, Any], settings: Any) -> None:
    """Freeze the finished shadow row into the terminal request snapshot before its bundle is built."""
    mode = _mode(settings, _dict(req.get("semantic_devices")))
    observation, error = _read_observation(req, settings, mode)
    req.pop("semantics_observation_error", None)
    if observation is not None:
        req["semantics_observation"] = observation
    elif error:
        req.pop("semantics_observation", None)
        req["semantics_observation_error"] = error


async def freeze_pending_observation(shadow: Any, req: dict[str, Any], durable: dict[str, Any],
                                     settings: Any) -> None:
    """Wait for the request's shadow row, then copy its fixed result into durable state."""
    if shadow is None or not applies(req, settings):
        return
    timeout = max(5.0, float(shadow.cfg.timeout_s) + 1.0)
    if await asyncio.to_thread(shadow.drain, timeout):
        freeze_observation(req, settings)
    else:
        req["semantics_observation_error"] = "관측 worker 완료 확인 실패"
    for key in ("semantics_observation", "semantics_observation_error"):
        if key in req:
            durable[key] = copy.deepcopy(req[key])
        else:
            durable.pop(key, None)


def _brief(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        return ", ".join(f"{key}={_brief(item)}" for key, item in value.items())
    if isinstance(value, list):
        return ", ".join(_brief(item) for item in value) or "없음"
    if value is None:
        return "없음"
    if isinstance(value, bool):
        return "예" if value else "아니요"
    return str(value)


def _cell(value: Any) -> str:
    return " ".join(_brief(value).split()).replace("|", "\\|")


def summary(report: Mapping[str, Any], link: str = REPORT_MD) -> str:
    counts = {status: sum(row.get("status") == status for row in report.get("devices") or []) for status in STATUSES}
    states = " · ".join(f"{status} {count}개" for status, count in counts.items() if count)
    return f"온톨로지·의미 장치 보고서: {states}. [{REPORT_MD}]({link})에서 판단·영향·근거 위치를 확인하세요."


def render_report(report: Mapping[str, Any]) -> str:
    conditions = _dict(report.get("execution_conditions"))
    lines = [
        "# 온톨로지·의미 장치 보고서", "",
        f"- 요청: `{report.get('request_id')}`",
        f"- 생성 기준 시각: {report.get('generated_at')}",
        f"- semantics mode: `{conditions.get('semantics_mode')}` · 마지막 정상: {conditions.get('last_ok_at')}",
        f"- 관련 설정 hash: `{conditions.get('semantic_config_sha256')}`",
        "", summary(report), "",
        "| 장치 | 상태 | 입력 | 판단·결정 | 결과 영향 | 코드 위치 | 기록 위치 |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in report.get("devices") or []:
        decisions = _brief(row.get("decisions"))
        if row.get("id") == "vocabulary_edam":
            decisions = f"topic {len(row.get('topics') or [])}개 · 산출 종류 선언 {len(row.get('output_types') or [])}개"
        elif row.get("id") == "checklist":
            checklist_items = row.get("items") or []
            decisions = (f"항목 {len(checklist_items)}개 · 계획 포함 "
                         f"{sum(bool(item.get('plan_included')) for item in checklist_items)}개")
        elif row.get("id") == "analysis_pack":
            decisions = f"적용 {_brief(row.get('applied'))} · 제외 {_brief(row.get('excluded'))}"
        if row.get("id") == "shadow_request":
            decisions = (f"{row.get('evaluation')} · 후보 {row.get('offered_count', 0)}건 · "
                         f"참조 {_brief(row.get('referenced'))} / {decisions}")
        if row.get("off"):
            decisions = f"꺼짐: {row['off'].get('reason')} · {row['off'].get('at')} / {decisions}"
        code = ", ".join(f"`{item.get('module')}:{item.get('function')}`" for item in row.get("code_locations") or [])
        records = ", ".join(f"`{item}`" for item in row.get("record_locations") or [])
        lines.append(f"| {_cell(row.get('name'))} | {_cell(row.get('status'))} | {_cell(row.get('inputs'))} | "
                     f"{_cell(decisions)} | {_cell(row.get('impact'))} | {code} | {records} |")
    errors = [row for row in report.get("errors") or [] if isinstance(row, Mapping)]
    if errors:
        lines += ["", "## 오류", "", "| 오류 코드 | 장치 | 단계 | 시각 | 설명 |",
                  "|---|---|---|---|---|"]
        lines += [f"| {_cell(row.get('code'))} | {_cell(row.get('device'))} | "
                  f"{_cell(row.get('stage'))} | {_cell(row.get('at'))} | {_cell(row.get('message'))} |"
                  for row in errors]
    checklist = next((row for row in report.get("devices") or [] if row.get("id") == "checklist"), {})
    items = checklist.get("items") or []
    if items:
        lines += ["", "## 체크리스트 항목", "",
                  "| 항목 | 계획 포함 | 담당 단계 | 계획 답 | reviewer 판정 | 근거 |",
                  "|---|---|---|---|---|---|"]
        for item in items:
            lines.append(f"| `{_cell(item['id'])}` | {'예' if item['plan_included'] else '아니요'} | "
                         f"{_cell(item['owner_steps'])} | {_cell(item['plan_answer'])} | "
                         f"{_cell(item['review_status'])} | {_cell(item['review_evidence'])} |")
    return "\n".join(lines) + "\n"
