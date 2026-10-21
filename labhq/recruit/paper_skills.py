"""Request-scoped paper advice, without a contract or a probationary employee (#549)."""
from __future__ import annotations

import asyncio
import copy
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
    "and prepares selected skills after approval alongside the approved steps. A late skill is omitted from that step.")
UNAVAILABLE = "스킬 장착 불가(Paper2Agent 미설치)"


def paper_id(value: Any) -> str | None:
    text = str(value or "").strip()
    doi = re.search(r"(?:doi:\s*|https?://(?:dx\.)?doi\.org/)?(10\.\d{4,9}/\S+)", text, re.I)
    if doi:
        return "doi:" + doi[1].rstrip(".,;").lower()
    pmid = re.search(r"(?:\bPMID\s*:\s*|https?://pubmed\.ncbi\.nlm\.nih\.gov/)(\d+)", text, re.I)
    return "pmid:" + pmid[1] if pmid else None


def evidence_problem(receipt: Any, pid: str | None = None) -> str | None:
    """Validate declarations only before CP1; bytes are checked after approval."""
    if not isinstance(receipt, dict):
        return "본문 receipt 없음; 자료 요청 필요"
    if type(receipt.get("coverage_version")) is not int or receipt["coverage_version"] != 2:
        return "본문 추출 범위 receipt 구판 또는 미확인; 재확인 필요"
    if pid and paper_id(receipt.get("paper_id")) != pid:
        return "본문 receipt 논문 ID 불일치"
    if receipt.get("access_level") != "full_text" or receipt.get("extraction") != "complete":
        return "본문 추출 범위 미확인 또는 부분 추출; 자료 요청 필요"
    if type(receipt.get("supplements_required")) is not bool:
        return "보충자료 의존 여부 미확인; 자료 요청 필요"
    if receipt.get("supplements_required") is True:
        return "보충자료 의존 근거는 artifact 연결 미지원; 자료 요청 필요"
    if not receipt.get("source") or not receipt.get("text_file") or not re.fullmatch(
            r"[a-f0-9]{64}", str(receipt.get("text_sha256") or "")):
        return "본문 artifact/hash 미확인; 자료 요청 필요"
    spans = receipt.get("evidence")
    if not isinstance(spans, list) or not spans or any(
            not isinstance(span, dict) or span.get("kind") not in {"methods", "qc"}
            or type(span.get("start_line")) is not int or type(span.get("end_line")) is not int
            or not 1 <= span["start_line"] <= span["end_line"] for span in spans):
        return "방법/QC 근거 위치 미확인; 자료 요청 필요"
    if {span["kind"] for span in spans} != {"methods", "qc"}:
        return "방법/QC 근거 위치 미확인; 자료 요청 필요"
    return None


def evidence_sha256(receipt: dict) -> str:
    return hashlib.sha256(json.dumps(receipt, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode()).hexdigest()


def verified_text(receipt: dict, root: Path, pid: str) -> bytes:
    problem = evidence_problem(receipt, pid)
    if problem:
        raise ValueError(problem)
    path = Path(receipt["text_file"])
    if not path.is_absolute():
        raise ValueError("본문 artifact 절대 경로 필요")
    if path.is_symlink():
        raise ValueError("본문 artifact link는 허용하지 않음")
    path = path.resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("본문 artifact가 runner workspace 밖에 있음")
    original_path = path.with_name(path.name + ".receipt.json")
    if original_path.is_symlink():
        raise ValueError("본문 원본 receipt link는 허용하지 않음")
    original = json.loads(original_path.read_text(encoding="utf-8"))
    immutable = ("coverage_version", "paper_id", "source", "access_level", "extraction", "text_file", "text_sha256", "supplements")
    if not isinstance(original, dict) or any(original.get(key) != receipt.get(key) for key in immutable):
        raise ValueError("도구 원본 본문 receipt 불일치; 재확인 필요")
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != receipt["text_sha256"]:
        raise ValueError("본문 artifact hash 변경; 재확인 필요")
    lines = data.decode("utf-8").splitlines()
    for span in receipt["evidence"]:
        if span["end_line"] > len(lines) or not any(
                line.strip() for line in lines[span["start_line"] - 1:span["end_line"]]):
            raise ValueError("방법/QC 근거 범위 없음; 자료 요청 필요")
    return data


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
                       "access_level": "full_text" if isinstance(papers[pid].get("fulltext_receipt"), dict) else None,
                       "fulltext_receipt": papers[pid].get("fulltext_receipt")})
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
        problem = evidence_problem(selected.get("fulltext_receipt"), selected["paper_id"])
        if problem:
            rows.append({**selected, "status": "skipped",
                         "skip_reason": problem})
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


def build_task(selected: dict, settings) -> Task:
    # One approved descriptor, not one converter per assigned step. No cross-plan semantic inference.
    return Task(agent_id=settings.recruit.agent_id, request_id=selected["request_id"],
                prompt="Prepare approved paper advice", budget_usd=settings.recruit.max_budget_usd,
                meta={"kind": "paper_skill", "paper_skill": copy.deepcopy(selected), "max_attempts": 1,
                      "step_id": "paper_skill_" + selected["selection_sha256"][:24],
                      "title": f"참고 논문 스킬: {selected['title']}"})


async def resolve_attachments(rows: list[dict], settings, run) -> list[dict]:
    """Resolve only a dispatched step's approved descriptors, through ordinary bounded CSO tasks."""
    resolved = []
    for selected in rows:
        if selected.get("status") != "deferred":
            resolved.append(selected)
            continue
        task = build_task(selected, settings)
        try:
            result = await run(task)
            receipt = result.structured if result.ok and isinstance(result.structured, dict) else {}
            if receipt.get("status") not in {"ready", "reused", "failed", "unavailable"}:
                receipt = {"status": "failed", "error": result.error or "Paper2Skill 변환 실패"}
            if receipt.get("status") in {"ready", "reused"}:
                if receipt.get("paper_id") != selected["paper_id"]:
                    receipt = {"status": "failed", "error": "Paper2Skill 논문 ID 불일치"}
                elif receipt.get("fulltext_receipt_sha256") != evidence_sha256(selected["fulltext_receipt"]):
                    receipt = {"status": "failed", "error": "Paper2Skill 본문 receipt 불일치; 재확인 필요"}
        except ShutdownInterrupted:
            raise
        except Exception as exc:
            receipt = {"status": "failed", "error": f"Paper2Skill 변환 실패 ({type(exc).__name__})"}
        # The converter cannot change the approved paper, employee, step or request.
        resolved.append({**receipt, **selected, "status": receipt["status"],
                         **({"error": receipt["error"]} if receipt.get("error") else {})})
    return resolved


class ApprovedBuilds:
    """One DAG invocation's owned, bounded preparations; delivery snapshots never change in flight."""

    def __init__(self, rows: list[dict], settings):
        self.settings = settings
        self.rows = {r['selection_sha256']: copy.deepcopy(r) for r in rows if r.get('status') == 'deferred'}
        self.tasks = {key: build_task(row, settings) for key, row in self.rows.items()}
        self.jobs: dict[str, asyncio.Task] = {}
        self.disabled: dict[str, str] = {}
        def descriptor(row):
            return {k: v for k, v in row.items() if k not in {'step_id', 'agent_id'}}
        for row in rows:
            if row.get('status') != 'deferred':
                continue
            key = row['selection_sha256']
            if descriptor(row) != descriptor(self.rows[key]):
                self.disabled[key] = 'paper_skill_ambiguous_selection'
        if len(self.rows) > 2:
            self.disabled.update({key: 'paper_skill_selection_limit' for key in self.rows})
        if settings.runner.task_timeout_s <= 0:
            self.disabled.update({key: 'invalid_task_timeout' for key in self.rows})
        if min(settings.runner.max_parallel, settings.orchestrator.max_parallel_steps) <= 0:
            self.disabled.update({key: 'paper_skill_parallel_unavailable' for key in self.rows})
        self.started: set[str] = set()
        self.stopped: set[str] = set()
        self.finished: set[str] = set()
        self.delivered: dict[tuple, dict] = {}
        self.deadline = 0.0

    async def start(self, run, spawn, cancel, progress, grace_s: float) -> None:
        self.spawn, self.cancel, self.progress, self.grace_s = spawn, cancel, progress, grace_s
        timeout = self.settings.runner.task_timeout_s
        self.deadline = asyncio.get_running_loop().time() + max(0, timeout)
        self.finished = set(self.disabled)
        parallel = max(1, min(2, self.settings.runner.max_parallel,
                              self.settings.orchestrator.max_parallel_steps))
        sem = asyncio.Semaphore(parallel)

        async def build(key):
            async with sem:
                if asyncio.get_running_loop().time() >= self.deadline:
                    return {**self.rows[key], 'status': 'unavailable', 'error': 'paper_skill_late'}
                async def admitted(_task):
                    self.started.add(key)
                    return await run(self.tasks[key])
                result = (await resolve_attachments([self.rows[key]], self.settings, admitted))[0]
                if asyncio.get_running_loop().time() >= self.deadline:
                    result = {**self.rows[key], 'status': 'unavailable', 'error': 'paper_skill_late'}
                self.finished.add(key)
                await progress(len(self.finished), len(self.rows))
                return result

        for key in self.rows:
            if key not in self.disabled:
                self.jobs[key] = spawn(build(key))
        await progress(len(self.finished), len(self.rows))

    async def resolve(self, rows: list[dict]) -> list[dict]:
        out = []
        for selected in rows:
            if selected.get('status') != 'deferred':
                out.append(copy.deepcopy(selected))
                continue
            key = selected.get('selection_sha256')
            delivery = (key, selected.get('step_id'), selected.get('agent_id'))
            if delivery not in self.delivered:
                job = self.jobs.get(key)
                receipt = {'status': 'unavailable', 'error': self.disabled.get(key, 'paper_skill_late')}
                if job is not None and key not in self.disabled:
                    done, _ = await asyncio.wait([job], timeout=max(
                        0, self.deadline - asyncio.get_running_loop().time()))
                    if key in self.disabled:
                        pass  # another assigned step already sealed the late fallback
                    elif done:
                        receipt = job.result()  # shutdown/cancellation must propagate
                    else:
                        self.disabled[key] = 'paper_skill_late'
                        await self._stop([key])
                        self.finished.add(key)
                        await self.progress(len(self.finished), len(self.rows))
                # A receipt from another assigned step cannot replace this step's approved identity.
                self.delivered[delivery] = copy.deepcopy({**receipt, **selected,
                    'status': receipt['status'], **({'error': receipt['error']} if receipt.get('error') else {})})
            out.append(copy.deepcopy(self.delivered[delivery]))
        return out

    async def _stop(self, keys: list[str]) -> None:
        pending = [key for key in keys if not self.jobs[key].done()]
        stopping = [key for key in keys if key not in self.stopped and key in self.started
                    and (not self.jobs[key].done() or self.jobs[key].cancelled())]
        self.stopped.update(stopping)
        stops = [self.spawn(self.cancel(self.tasks[key])) for key in stopping]
        for key in pending:
            self.jobs[key].cancel()
        owned = [*stops, *(self.jobs[key] for key in pending)]
        if owned:
            done, waiting = await asyncio.wait(owned, timeout=self.grace_s)
            for job in done:
                if not job.cancelled():
                    job.exception()  # retrieve background failures without masking the caller's exception
            # Keep resistant jobs owned by the request; never await cancellation without a bound.
            for job in waiting:
                job.cancel()

    async def close(self) -> None:
        await self._stop(list(self.jobs))
        for job in self.jobs.values():
            if job.done() and not job.cancelled():
                job.exception()


def attachments(plan: dict, step_id: str, agent_id: str, rid: str) -> list[dict]:
    return [{**row, "request_id": rid, "agent_id": agent_id, "step_id": step_id}
            for row in plan.get("paper_skill_results", [])
            if row.get("status") in {"deferred", "ready", "reused"} and step_id in row.get("step_ids", [])
            and agent_id in row.get("agent_ids", [])]


def card_text(plan: dict) -> str:
    return "\n".join(
        f"참고 논문 스킬: {r['title']} → {', '.join(r.get('agent_ids') or [])}; 선정됨: 예; "
        f"만들어짐: {'예' if r.get('status') in {'ready', 'reused'} else '아니오'}"
        + (" (승인 후 병렬 준비; 늦으면 해당 단계에서 생략; 현재는 receipt 선언만 확인; 읽음/효용 미확인)" if r.get('status') == 'deferred'
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


def with_preparation_outcome(result: TaskResult, task: Task) -> TaskResult:
    """Retain late/budget fallback in the existing step receipt, without inventing runner read evidence."""
    omitted = [row for row in task.meta.get('paper_skills', []) if row.get('status') == 'unavailable']
    if not omitted:
        return result
    provenance = copy.deepcopy(result.provenance)
    run = provenance.setdefault('runs', {}).setdefault(result.task_id, {})
    usage = run.setdefault('paper_skills', [])
    for selected in omitted:
        row = next((u for u in usage if u.get('paper_id') == selected['paper_id']
                    and u.get('selection_sha256') == selected['selection_sha256']), None)
        if row is None:
            row = {k: selected[k] for k in ('paper_id', 'selection_sha256', 'status')}
            usage.append(row)
        row.update(status='unavailable', called=False, call_status='not_attached', reason='build_unavailable')
        row.pop('input_ref', None)  # a task approved without this input cannot attest an attached input
        row['preparation_error'] = selected.get('error') or 'paper_skill_unavailable'
    return result.model_copy(update={'provenance': provenance})


def appendix(plan: dict, results: dict | None = None) -> str:
    rows = records(plan, results)
    def values(row: dict, key: str, fallback: str) -> str:
        return ', '.join(dict.fromkeys(str(u[key]) for u in [row, *row['usage']] if u.get(key))) or fallback
    return ("참고 논문 스킬 기록:\n" + "\n".join(
        f"- {r['paper_id']} · {r['title']} → {', '.join(r.get('agent_ids') or [])}; {r['status']}; "
        f"선정됨: 예; 만들어짐: {'예' if r['built'] else '아니오'}"
        + (f" ({values(r, 'preparation_error', r.get('skip_reason') or r.get('error') or '미완료')})"
           if not r['built'] else "") + "; "
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
        evidence = self.task.meta["paper_skill"].get("fulltext_receipt")
        try:
            text = verified_text(evidence, s.path(s.runner.workspace_root), self.pid)
        except (OSError, ValueError, UnicodeError) as exc:
            return self.task, None, self.result({"status": "failed", "error": str(exc)})
        self.evidence = evidence
        self.evidence_hash = evidence_sha256(evidence)
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
                    and row.get("fulltext_receipt_sha256") == self.evidence_hash
                    and (Path(row["skill_dir"]) / "SKILL.md").is_file()
                    and provenance.folder_sha256(row["skill_dir"]) == row["skill_sha256"]):
                return self.task, None, self.result({**row, "status": "reused", "runner_id": s.runner.id})
        if candidates:  # retain the original evidence; never silently rebuild a changed talent skill
            return self.task, None, self.result({"status": "failed", "error": "인재풀 skill 또는 본문 receipt 불일치; 재확인 필요"})
        build = self.talent / "build" / self.task.id
        build.mkdir(parents=True, exist_ok=True)
        source = build / "paper-input.md"
        source.write_bytes(text)
        prompt = (
            "Use the installed Paper2Agent paper2skill skill to make a consultant skill from this paper.\n"
            f"Paper: {self.pid}\nTitle: {self.task.meta['paper_skill']['title']}\n"
            f"Method fit: {self.task.meta['paper_skill']['reason']}\nOutput directory: {build}\n"
            f"Verified paper input: {source}\nText SHA256: {evidence['text_sha256']}\n"
            f"Evidence line ranges (methods/QC): {json.dumps(evidence['evidence'])}\n"
            "Use this exact paper input. Missing methods/QC or supplements must remain unverified; "
            "request material rather than invent parameters or fill gaps from memory. "
            "Deliver SKILL.md with reviewed paper text, figures and tables. Do not build MCP tools, execute the "
            "paper's analysis, register a client, or create an employment contract. Return the offer-letter JSON "
            "with kind=consultant, the absolute skill_dir, exact verification_status and limitations; "
            "use null/empty arrays for absent MCP and repository parts. Never put credentials in files or output.")
        return self.task.model_copy(update={
            "prompt": prompt, "output_schema": paper2agent.OFFER_SCHEMA,
            "budget_usd": min(self.task.budget_usd or s.recruit.max_budget_usd, s.recruit.max_budget_usd),
            "meta": {**self.task.meta, "agent_overrides": {"permission_mode": s.recruit.permission_mode}}}), build, None

    def finish(self, result: TaskResult) -> TaskResult:
        offer = result.structured if isinstance(result.structured, dict) else extract_json(result.text)
        try:
            if not result.ok or not isinstance(offer, dict):
                raise ValueError("Paper2Skill 변환 실패")
            verified_text(self.evidence, self.runner.s.path(self.runner.s.runner.workspace_root), self.pid)
            source = self.talent / "build" / self.task.id / "paper-input.md"
            if hashlib.sha256(source.read_bytes()).hexdigest() != self.evidence["text_sha256"]:
                raise ValueError("변환 입력 hash 변경; 재확인 필요")
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
                   "fulltext_receipt": self.evidence, "fulltext_receipt_sha256": self.evidence_hash,
                   "paper2agent_commit": paper2agent.installed_commit(engine, self.runner.s) if engine != "mock" else None}
            atomic_write_text(self.receipt, json.dumps(row, ensure_ascii=False, indent=2))
        except (OSError, ValueError) as exc:
            row = {"status": "failed", "error": str(exc)}
        return result.model_copy(update={"ok": True, "structured": row, "text": row.get("error", "Paper2Skill ready")})
