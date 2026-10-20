"""Request-scoped paper advice, without a contract or a probationary employee (#549)."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..models import Task, TaskResult
from ..interruption import ShutdownInterrupted
from ..util import atomic_write_text, extract_json
from . import paper2agent, provenance


class PaperSkillSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    paper_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    step_ids: list[str] = Field(min_length=1)


SELECTION_SCHEMA = {"type": "array", "maxItems": 2, "items": PaperSkillSelection.model_json_schema()}
PLAN_RULE = (
    "Select at most two papers from Analysis precedents whose methods directly fit this request in paper_skills. "
    "Prefer papers with available code or data. For each give paper_id (DOI or PMID from the supplied citations), "
    "title, reason (why its method fits), and step_ids assigned to core employees. Use [] when none fits. "
    "These are Paper2Skill consultant skills attached only to those steps for this request, without hiring. "
    "Do not invent identifiers or put conversion receipts in the plan; labhq checks metadata before CP1 "
    "and builds selected skills only when their approved steps dispatch.")
UNAVAILABLE = "스킬 장착 불가(Paper2Agent 미설치)"


def paper_id(value: Any) -> str | None:
    text = str(value or "").strip()
    doi = re.search(r"(?:doi:\s*|https?://(?:dx\.)?doi\.org/)?(10\.\d{4,9}/\S+)", text, re.I)
    if doi:
        return "doi:" + doi[1].rstrip(".,;").lower()
    pmid = re.search(r"(?:\bPMID\s*:\s*|https?://pubmed\.ncbi\.nlm\.nih\.gov/)(\d+)", text, re.I)
    return "pmid:" + pmid[1] if pmid else None


def _access_level(paper: dict) -> str | None:
    levels = [str(paper.get("access_level") or "")]
    levels += [match.group(1) for citation in paper.get("citations", [])
               if (match := re.search(r"\baccess_level\s*=\s*(full_text|abstract|metadata)\b",
                                      str(citation), re.I))]
    normalized = {level.strip().lower() for level in levels if level.strip()}
    return "full_text" if "full_text" in normalized else "abstract" if "abstract" in normalized else (
        "metadata" if "metadata" in normalized else None)


def selections(plan: dict, precedents: dict, roster: list[dict]) -> list[dict]:
    papers = {pid: row for row in precedents.get("papers", [])
              for citation in row.get("citations", []) if (pid := paper_id(citation))}
    core = {a["id"] for a in roster if a.get("employment", "core") == "core"}
    steps = {s["id"]: s for s in plan.get("steps", []) if s.get("agent_id") in core}
    chosen = []
    for raw in plan.get("paper_skills") or []:
        try:
            row = PaperSkillSelection.model_validate(raw).model_dump()
        except ValueError:
            continue
        pid = paper_id(row["paper_id"])
        ids = list(dict.fromkeys(s for s in row["step_ids"] if s in steps))
        if pid not in papers or not ids or not row["reason"].strip():
            continue
        if any(r["paper_id"] == pid for r in chosen):
            continue
        chosen.append({**row, "paper_id": pid, "title": papers[pid]["title"], "step_ids": ids,
                       "agent_ids": list(dict.fromkeys(steps[s]["agent_id"] for s in ids)),
                       "access_level": _access_level(papers[pid])})
        if len(chosen) == 2:
            break
    return chosen


async def prepare_plan(plan: dict, precedents: dict, roster: list[dict], rid: str, settings, run) -> dict:
    rows = []
    accepted = selections(plan, precedents, roster)
    remaining = {row["paper_id"]: row for row in accepted}
    selected_rows = []
    for raw in plan.get("paper_skills") or []:
        pid = paper_id(raw.get("paper_id")) if isinstance(raw, dict) else None
        selected = remaining.pop(pid, None) if pid else None
        if selected is not None:
            selected_rows.append(selected)
            continue
        raw_id = raw.get("paper_id") if isinstance(raw, dict) else None
        selected_rows.append({**(raw if isinstance(raw, dict) else {}),
                              "paper_id": pid or str(raw_id or "미확인"),
                              "title": str((raw or {}).get("title") or raw_id or "미확인")
                              if isinstance(raw, dict) else "미확인",
                              "agent_ids": [], "access_level": None, "status": "skipped",
                              "skip_reason": "선행 연구 식별자 또는 정규직 담당 단계와 일치하지 않음"})
    for selected in selected_rows:
        if selected.get("status") == "skipped":
            rows.append(selected)
            continue
        if selected.get("access_level") != "full_text":
            level = selected.get("access_level") or "미확인"
            rows.append({**selected, "status": "skipped",
                         "skip_reason": f"본문 접근 수준이 {level}라 Paper2Skill을 만들지 않음"})
            continue
        # No filesystem, fulltext fetch or model turn before CP1. This descriptor, not a future build,
        # is part of the approved plan hash. Execution receipts never mutate the frozen plan.
        digest = hashlib.sha256(json.dumps(selected, ensure_ascii=False, sort_keys=True,
                                            separators=(",", ":")).encode()).hexdigest()
        rows.append({**selected, "status": "deferred", "selection_sha256": digest})
    if not rows:
        return {k: v for k, v in plan.items() if k != "paper_skill_results"}
    rows = [{**row, "selected": True, "built": False, "attached_steps": [], "called": False} for row in rows]
    warnings = [f"논문 스킬 생략: {r['title']} ({r['skip_reason']})" for r in rows if r["status"] == "skipped"]
    return {**plan, "paper_skill_results": rows,
            "warnings": list(dict.fromkeys([*(plan.get("warnings") or []), *warnings]))}


async def resolve_attachments(rows: list[dict], settings, run) -> list[dict]:
    """Resolve only a dispatched step's approved descriptors, through ordinary bounded CSO tasks."""
    resolved = []
    for selected in rows:
        if selected.get("status") != "deferred":
            resolved.append(selected)
            continue
        key = selected["selection_sha256"] + ":" + selected["step_id"]
        task = Task(agent_id=settings.recruit.agent_id, request_id=selected["request_id"],
                    prompt="Prepare approved paper advice", budget_usd=settings.recruit.max_budget_usd,
                    meta={"kind": "paper_skill", "paper_skill": selected, "max_attempts": 1,
                          "step_id": "paper_skill_" + hashlib.sha256(key.encode()).hexdigest()[:24],
                          "title": f"참고 논문 스킬: {selected['title']}"})
        try:
            result = await run(task)
            receipt = result.structured if result.ok and isinstance(result.structured, dict) else {}
            if receipt.get("status") not in {"ready", "reused", "failed", "unavailable"}:
                receipt = {"status": "failed", "error": result.error or "Paper2Skill 변환 실패"}
            if receipt.get("status") in {"ready", "reused"} and receipt.get("paper_id") != selected["paper_id"]:
                receipt = {"status": "failed", "error": "Paper2Skill 논문 ID 불일치"}
        except ShutdownInterrupted:
            raise
        except Exception as exc:
            receipt = {"status": "failed", "error": f"Paper2Skill 변환 실패 ({type(exc).__name__})"}
        # The converter cannot change the approved paper, employee, step or request.
        resolved.append({**receipt, **selected, "status": receipt["status"],
                         **({"error": receipt["error"]} if receipt.get("error") else {})})
    return resolved


def attachments(plan: dict, step_id: str, agent_id: str, rid: str) -> list[dict]:
    return [{**row, "request_id": rid, "agent_id": agent_id, "step_id": step_id}
            for row in plan.get("paper_skill_results", [])
            if row.get("status") in {"deferred", "ready", "reused"} and step_id in row.get("step_ids", [])
            and agent_id in row.get("agent_ids", [])]


def card_text(plan: dict) -> str:
    return "\n".join(
        f"참고 논문 스킬: {r['title']} → {', '.join(r.get('agent_ids') or [])}; 선정됨: 예; "
        f"만들어짐: {'예' if r.get('status') in {'ready', 'reused'} else '아니오'}"
        + (" (승인 후 담당 단계 실행 때 생성; 현재는 본문 접근 metadata만 확인)" if r.get('status') == 'deferred'
           else f" ({r.get('skip_reason') or r.get('error')})" if r.get('status') not in {"ready", "reused"} else "")
        + f"; 붙을 단계: {', '.join(r.get('step_ids') or []) or '없음'}; 호출됨: 아니오(실행 전)"
        for r in plan.get("paper_skill_results", []))


def records(plan: dict, results: dict | None = None) -> list[dict]:
    usage = []
    for step_id, result in (results or {}).items():
        for run in (result.get("provenance") or {}).get("runs", {}).values():
            usage.extend({**row, "step_id": step_id} for row in run.get("paper_skills", []))
    records = []
    for row in plan.get("paper_skill_results", []):
        used = [u for u in usage if u.get("paper_id") == row.get("paper_id")
                and u.get("step_id") in row.get("step_ids", [u.get("step_id")])
                and (not row.get("selection_sha256") or u.get("selection_sha256") == row["selection_sha256"])]
        called = [u for u in used if u.get("called") is True and u.get("call_status") == "referenced"
                  and all(re.fullmatch(r"[a-f0-9]{64}", str(u.get(k) or ''))
                          for k in ('skill_sha256', 'document_sha256'))
                  and re.fullmatch(r"paper-[a-f0-9]{24}", str(u.get('skill_id') or ''))
                  and u.get("evidence") == reference_token(u)]
        built = any(u.get("skill_sha256") for u in used) or row.get("status") in {"ready", "reused"}
        build_status = (used[-1].get('status') if used else None) or row.get('status')
        records.append({**{k: v for k, v in row.items() if k not in {"skill_dir", "runner_id"}},
                        "selected": True, "built": built, "status": build_status,
                        "attached_steps": list(dict.fromkeys(u["step_id"] for u in used if u.get("input_ref"))),
                        "called": bool(called), "call_status": "referenced" if called else
                        "unverified" if used or any(sid in (results or {}) for sid in row.get('step_ids', []))
                        else "not_dispatched",
                        "usage": used})
    return records


def appendix(plan: dict, results: dict | None = None) -> str:
    rows = records(plan, results)
    def values(row: dict, key: str, fallback: str) -> str:
        return ', '.join(dict.fromkeys(str(u[key]) for u in [row, *row['usage']] if u.get(key))) or fallback
    return ("참고 논문 스킬 기록:\n" + "\n".join(
        f"- {r['paper_id']} · {r['title']} → {', '.join(r.get('agent_ids') or [])}; {r['status']}; "
        f"선정됨: 예; 만들어짐: {'예' if r['built'] else '아니오'}"
        + (f" ({r.get('skip_reason') or r.get('error')})" if not r['built'] else "") + "; "
        f"Paper2Agent commit: {values(r, 'paper2agent_commit', '미기록')}; "
        f"skill hash: {values(r, 'skill_sha256', '없음')}; "
        f"붙음: {', '.join(r['attached_steps']) or '아니오'}; "
        f"호출됨: {'예(결과의 정확한 참조 확인)' if r['called'] else '아니오'} ({r['call_status']})"
        for r in rows)) if rows else ""


def reference_token(row: dict) -> str:
    """A result reference proves attribution, not tool execution or scientific efficacy."""
    return f"[paper-skill:{row.get('skill_id')}:{row.get('skill_sha256')}:{row.get('document_sha256')}]"


class PaperSkillBuild:
    """One ordinary recruiter turn, with a reusable, hash-checked talent receipt."""
    def __init__(self, runner, task: Task):
        self.runner, self.task = runner, task
        self.pid = paper_id(task.meta["paper_skill"]["paper_id"])
        if not self.pid or task.agent_id != runner.s.recruit.agent_id:
            raise ValueError("invalid paper skill conversion")
        key = hashlib.sha256(self.pid.encode()).hexdigest()[:24]
        self.talent = runner.registry.talent_dir / ("paper-skill-" + key)
        self.receipt = self.talent / "paper-skill.json"

    def result(self, row: dict) -> TaskResult:
        return TaskResult(task_id=self.task.id, agent_id=self.task.agent_id, ok=True, structured=row,
                          text=row.get("error", "Paper2Skill " + row["status"]), cost_usd=0)

    def prepare(self) -> tuple[Task, Path | None, TaskResult | None]:
        from ..doctor import _paper2agent_row
        s = self.runner.s
        agents = list(self.runner.registry.agents.values())
        if _paper2agent_row(s, agents)["status"] == "fail":
            return self.task, None, self.result({"status": "unavailable", "error": UNAVAILABLE})
        candidates = []
        if self.receipt.is_file():
            candidates.append(json.loads(self.receipt.read_text(encoding="utf-8")))
        for spec in self.runner.registry.talent_pool():
            c = spec.contract
            if c and paper_id(c.paper) == self.pid and c.skill_dir:
                candidates.append({"paper_id": self.pid, "skill_dir": c.skill_dir,
                                   "skill_sha256": c.skill_sha256,
                                   "paper2agent_commit": c.verification.get("paper2agent_commit")})
        for row in candidates:
            if (row.get("paper_id") == self.pid and row.get("skill_sha256")
                    and (Path(row["skill_dir"]) / "SKILL.md").is_file()
                    and provenance.folder_sha256(row["skill_dir"]) == row["skill_sha256"]):
                return self.task, None, self.result({**row, "status": "reused", "runner_id": s.runner.id})
        if candidates:  # retain the original evidence; never silently rebuild a changed talent skill
            return self.task, None, self.result({"status": "failed", "error": "인재풀 skill hash 불일치"})
        build = self.talent / "build" / self.task.id
        build.mkdir(parents=True, exist_ok=True)
        prompt = (
            "Use the installed Paper2Agent paper2skill skill to make a consultant skill from this paper.\n"
            f"Paper: {self.pid}\nTitle: {self.task.meta['paper_skill']['title']}\n"
            f"Method fit: {self.task.meta['paper_skill']['reason']}\nOutput directory: {build}\n"
            "Deliver SKILL.md with reviewed paper text, figures and tables. Do not build MCP tools, execute the "
            "paper's analysis, register a client, or create an employment contract. Return the offer-letter JSON "
            "with kind=consultant, the absolute skill_dir, exact verification_status and limitations; "
            "use null/empty arrays for absent MCP and repository parts. Never put credentials in files or output.")
        return self.task.model_copy(update={
            "prompt": prompt, "output_schema": paper2agent.OFFER_SCHEMA, "budget_usd": s.recruit.max_budget_usd,
            "meta": {**self.task.meta, "agent_overrides": {"permission_mode": s.recruit.permission_mode}}}), build, None

    def finish(self, result: TaskResult) -> TaskResult:
        offer = result.structured if isinstance(result.structured, dict) else extract_json(result.text)
        try:
            if not result.ok or not isinstance(offer, dict):
                raise ValueError("Paper2Skill 변환 실패")
            src = Path(offer.get("skill_dir") or "")
            passed, reason = paper2agent.probation("consultant", [], str(src), offer.get("verification_status"))
            if not passed or not (src / "SKILL.md").is_file():
                raise ValueError(reason or "SKILL.md 없음")
            digest = provenance.folder_sha256(src)
            if not digest:
                raise ValueError("skill hash 실패")
            dst = self.talent / "skill" / digest
            if not dst.exists():
                shutil.copytree(src, dst)
            if provenance.folder_sha256(dst) != digest:
                raise ValueError("skill copy hash 불일치")
            engine = paper2agent.recruiter_engine(self.runner.s, self.runner.registry.agents.get(self.task.agent_id))
            row = {"paper_id": self.pid, "status": "ready", "skill_dir": str(dst), "skill_sha256": digest,
                   "runner_id": self.runner.s.runner.id,
                   "paper2agent_commit": paper2agent.installed_commit(engine, self.runner.s) if engine != "mock" else None}
            atomic_write_text(self.receipt, json.dumps(row, ensure_ascii=False, indent=2))
        except (OSError, ValueError) as exc:
            row = {"status": "failed", "error": str(exc)}
        return result.model_copy(update={"ok": True, "structured": row, "text": row.get("error", "Paper2Skill ready")})
