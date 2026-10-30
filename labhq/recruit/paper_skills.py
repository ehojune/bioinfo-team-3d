"""Request-scoped paper advice, without a contract or a probationary employee (#549)."""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import os
import re
import shutil
import stat
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..models import Task, TaskResult
from ..interruption import ShutdownInterrupted
from ..tools.annot_literature import source_access_metadata
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
    # Legacy scout schemas cannot carry these optional diagnostics. Verify every supplied value,
    # while core artifact identity/coverage/hash remains mandatory under either schema.
    optional = ("access_status", "supplementary_status", "pdf_file", "pdf_sha256", "oa_copy", "unread_reason", "unread_detail")
    if (not isinstance(original, dict) or any(original.get(key) != receipt.get(key) for key in immutable)
            or any(key in receipt and original.get(key) != receipt[key] for key in optional)):
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


SKILL_FILE_MAX_BYTES = 8192  # SKILL.md is read only up to its front matter
_SKILL_PARTS = (("figures", "figure"), ("tables", "table"), ("supp_figures", "supp_figs"), ("supp_tables", "supp_table"))


def _linked(path: Path) -> bool:
    """A symlink, or on Windows any reparse point such as a directory junction: none is followed when a skill folder is
    read. Read from the entry itself (never through it), so it works on Python 3.10 and 3.11, which have no
    ``os.path.isjunction``; a reparse point of another kind (a cloud placeholder) is refused too, which is the safe side."""
    try:
        info = os.lstat(path)
    except OSError:
        return False
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def skill_content(folder: Path, digest: str) -> dict[str, Any] | None:
    """What one built paper-skill folder holds (#660), read from the files and never summarised.

    Returns the SKILL.md name and one-line description, whether the paper's full text is in the folder (its size),
    and how many figure / table / supplement files it carries. None when the folder is not a plain skill folder of
    this receipt (missing, a link or junction anywhere on the way from the talent folder down, or the hash does not
    name it). Bounded: one small read and one directory listing per part, nothing followed."""
    try:
        root = Path(folder) / "skill" / digest
        if not re.fullmatch(r"[0-9a-f]{64}", str(digest)):
            return None
        # Every directory on the way is checked without following it, the talent folder itself included: a replaced
        # or tampered entry must never make this read a file or a listing outside the recorded skill.
        if any(_linked(p) for p in (Path(folder), Path(folder) / "skill", root, root / "references", root / "assets")):
            return None
        skill_md = root / "SKILL.md"
        if not root.is_dir() or not skill_md.is_file() or _linked(skill_md):
            return None
        with open(skill_md, "rb") as handle:
            head = handle.read(SKILL_FILE_MAX_BYTES).decode("utf-8", errors="replace")
        front = head.split("---", 2)[1] if head.startswith("---") and head.count("---") >= 2 else ""
        fields = {m.group(1): m.group(2).strip().strip("\"'") for m in re.finditer(r"(?m)^(name|description):\s*(.+)$", front)}
        content: dict[str, Any] = {"name": _plain(fields.get("name"), 80), "description": _plain(fields.get("description"), 160)}
        paper = root / "references" / "paper.md"
        supplement = root / "references" / "supplement.md"
        content["fulltext_bytes"] = paper.stat().st_size if paper.is_file() and not _linked(paper) else None
        content["supplement"] = supplement.is_file() and not _linked(supplement)
        for key, folder_name in _SKILL_PARTS:
            part = root / "assets" / folder_name
            if part.is_dir() and not _linked(part):
                with os.scandir(part) as entries:
                    content[key] = sum(1 for e in entries if e.is_file(follow_symlinks=False))
            else:
                content[key] = 0
        return content
    except (OSError, UnicodeError, ValueError):
        return None


def skill_origins(requests) -> dict[tuple[str, str], dict[str, Any]]:
    """Which request first built each skill (#660): {(paper_id, skill_sha256): {request_id, created_at}}.

    `requests` is an iterable of (request id, created_at, that request's plan ``paper_skill_results`` list). A request
    built a skill only where its row says ``ready``; ``reused`` means an earlier request built it. The earliest
    created_at wins, and a malformed row or time is skipped, never guessed."""
    found: dict[tuple[str, str], dict[str, Any]] = {}
    for request_id, created_at, rows in requests:
        if not isinstance(rows, list) or not isinstance(created_at, (int, float)) or isinstance(created_at, bool):
            continue
        for row in rows:
            if not isinstance(row, dict) or row.get("status") != "ready":
                continue
            pid, digest = paper_id(row.get("paper_id")), row.get("skill_sha256")
            if not pid or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
                continue
            known = found.get((pid, digest))
            if known is None or created_at < known["created_at"]:
                found[(pid, digest)] = {"request_id": str(request_id)[:60], "created_at": float(created_at)}
    return found


def cited_access(citations: list[str]) -> str | None:
    """The best `access_level=` a precedent's citations state, e.g. abstract or full_text; None when none says."""
    rank = {"full_text": 3, "abstract": 2, "abstract_only": 2, "metadata": 1}
    levels = [m.lower() for text in citations for m in re.findall(r"access_level\s*=\s*([A-Za-z_]+)", text)]
    return max(levels, key=lambda level: rank.get(level, 0)) if levels else None


def extraction_steps(steps: dict, assigned: list[str], title: str) -> list[str]:
    """#670: for a meta-analysis, the literature steps (lit_scout) that extract its included studies and are not
    assigned yet. Other papers gain none."""
    if not re.search(r"meta[\s-]?analy|systematic review|메타\s?분석|체계적\s?문헌", title, re.I):
        return []
    return [sid for sid, step in steps.items() if step.get("agent_id") == "lit_scout" and sid not in assigned]


def no_fulltext_reason(selected: dict, problem: str) -> str:
    """Say why no skill is made when the full text was not reached: the paper stays a planning reference only (#670)."""
    receipt = selected.get("fulltext_receipt")
    level = receipt.get("access_level") if isinstance(receipt, dict) else selected.get("cited_access")
    if isinstance(receipt, dict) and receipt.get("access_level") == "full_text":
        return problem  # a full text whose extraction is incomplete keeps its own reason
    seen = "초록만 확인했습니다" if level in {"abstract", "abstract_only"} else "본문 접근을 확인하지 못했습니다"
    return f"{problem}; {seen}. 스킬을 만들지 않고 계획 참고로만 씁니다"


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
        receipt = papers[pid].get("fulltext_receipt")
        # #670: a meta-analysis skill also goes, read-only, to the steps that extract its included studies.
        extraction = extraction_steps(steps, ids, str(papers[pid].get("title") or ""))
        ids = [*ids, *extraction]
        chosen.append({**row, "paper_id": pid, "title": papers[pid]["title"], "step_ids": ids,
                       **({"extraction_step_ids": extraction} if extraction else {}),
                       "cited_access": cited_access([c for c in papers[pid].get("citations", []) if isinstance(c, str)]),
                       "agent_ids": list(dict.fromkeys(steps[s]["agent_id"] for s in ids)),
                       "access_level": receipt.get("access_level") if isinstance(receipt, dict) else None,
                       "fulltext_receipt": receipt, **source_access_metadata(papers[pid])})
        if len(chosen) == 2:
            break
    return chosen


async def prepare_plan(plan: dict, precedents: dict, roster: list[dict], rid: str, settings, run) -> dict:
    if not paper2agent.enabled(settings):
        rows = [{**row, "title": str(row.get("title") or row.get("paper_id") or "미확인"),
                 "agent_ids": [], "access_level": None,
                 "status": "skipped", "skip_reason": "recruit_disabled", "selected": True,
                 "built": False, "attached_steps": [], "called": False}
                for row in plan.get("paper_skills") or [] if isinstance(row, dict)]
        if not rows:
            return {k: v for k, v in plan.items() if k != "paper_skill_results"}
        return {**plan, "paper_skill_results": rows,
                "warnings": list(dict.fromkeys([*(plan.get("warnings") or []),
                                                 "논문 스킬 생략: recruit.enabled=false"]))}
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
                         "skip_reason": no_fulltext_reason(selected, problem)})
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
    if not paper2agent.enabled(settings):
        return [{**row, "status": "unavailable", "error": "recruit_disabled"} for row in rows]
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
        self.tasks = {key: build_task(row, settings) for key, row in self.rows.items()} if paper2agent.enabled(settings) else {}
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
        if not paper2agent.enabled(settings):
            self.disabled.update({key: 'recruit_disabled' for key in self.rows})
        self.started: set[str] = set()
        self.stopped: set[str] = set()
        self.finished: set[str] = set()
        self.delivered: dict[tuple, dict] = {}
        self.deadline = 0.0
        self.watchdog: asyncio.Task | None = None

    async def start(self, run, spawn, cancel, progress, grace_s: float) -> None:
        self.spawn, self.cancel, self.progress, self.grace_s = spawn, cancel, progress, grace_s
        if not paper2agent.enabled(self.settings):
            self.disabled.update({key: 'recruit_disabled' for key in self.rows})
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
        if self.jobs:
            self.watchdog = spawn(self._expire())
        await progress(len(self.finished), len(self.rows))

    async def _expire(self) -> None:
        # The build lifetime is independent of whether a ready step consumes or omits the advice.
        await asyncio.sleep(max(0, self.deadline - asyncio.get_running_loop().time()))
        pending = [key for key, job in self.jobs.items() if not job.done()]
        self.disabled.update({key: 'paper_skill_late' for key in pending})
        await self._stop(pending)
        self.finished.update(pending)
        await self.progress(len(self.finished), len(self.rows))

    async def resolve(self, rows: list[dict]) -> list[dict]:
        """Snapshot at dispatch, with no suspension or cancellation of optional work."""
        if not paper2agent.enabled(self.settings):
            return [{**row, 'status': 'unavailable', 'error': 'recruit_disabled'} for row in rows]
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
                if job is not None and key not in self.disabled and job.done():
                    receipt = job.result()  # an already observed shutdown/cancellation still propagates
                # A receipt from another assigned step cannot replace this step's approved identity.
                self.delivered[delivery] = copy.deepcopy({**receipt, **selected,
                    'status': receipt['status'], **({'error': receipt['error']} if receipt.get('error') else {})})
            out.append(copy.deepcopy(self.delivered[delivery]))
        return out

    async def _stop(self, keys: list[str]) -> None:
        pending = [key for key in keys if not self.jobs[key].done()]
        stopping = [key for key in keys if key not in self.stopped and key in self.started
                    and (not self.jobs[key].done() or self.jobs[key].cancelled())]
        async def request_cancel(key):
            await self.cancel(self.tasks[key])
            self.stopped.add(key)  # an interrupted callback must not suppress later cleanup
        stops = [self.spawn(request_cancel(key)) for key in stopping]
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
        if self.watchdog is not None:
            self.watchdog.cancel()
        await self._stop(list(self.jobs))
        if self.watchdog is not None and self.watchdog.done() and not self.watchdog.cancelled():
            self.watchdog.exception()
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
        f"결과 참조: {'확인(도구 호출·효용 증거 아님)' if r['called'] else '실행 전' if r['call_status'] == 'not_dispatched' else '미확인'}"
        f" ({r['call_status']})"
        for r in rows)) if rows else ""


_STATUSES = {"deferred", "ready", "reused", "failed", "unavailable", "skipped"}
_REFERENCES = {"referenced", "unverified", "not_dispatched"}
_REASONS = {"result_reference", "input_changed", "step_failed", "no_result_reference"}


def _plain(value: Any, limit: int) -> str | None:
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", value).strip() if isinstance(value, str) else ""
    return text[:limit] or None


def _ids(values: Any, limit: int = 12) -> list[str]:
    rows = values if isinstance(values, list) else []
    return list(dict.fromkeys(v for v in rows if isinstance(v, str) and re.fullmatch(r"[\w.-]{1,64}", v)))[:limit]


def _member(value: Any, allowed: set[str]) -> str | None:
    # A stored record may hold a list or dict here; never hash it (#849 review).
    return value if isinstance(value, str) and value in allowed else None


def _hex(rows: list[dict], key: str, size: int) -> str | None:
    found = [row[key] for row in rows if isinstance(row.get(key), str) and re.fullmatch(rf"[a-f0-9]{{{size}}}", row[key])]
    return found[-1] if found else None


def literature_view(req: dict) -> dict:
    """Read-only rows for the literature scout's paper list (#660), from the stored request only.

    No file, build, download or model turn. A result reference is shown as such, never as a tool call or utility.
    """
    precedents = req.get("analysis_precedents")
    plan = req.get("plan") if isinstance(req.get("plan"), dict) else {}
    if not isinstance(precedents, dict) and not plan.get("paper_skill_results") and not plan.get("recruit_results"):
        return {"status": "absent", "papers": []}
    status = "ok" if isinstance(precedents, dict) and precedents.get("status") == "ok" else "unavailable"
    view: dict[str, Any] = {"status": status, "papers": []}
    if status != "ok":
        view["warning"] = _plain((precedents or {}).get("warning") if isinstance(precedents, dict) else None, 200)
    try:
        results = {k: v for k, v in (req.get("results") or {}).items() if isinstance(v, dict)}
        skills = records({"paper_skill_results": [r for r in plan.get("paper_skill_results") or [] if isinstance(r, dict)]},
                         results)
    except (AttributeError, TypeError, KeyError, ValueError):
        skills, view["damaged"] = [], True
    raw_recruits = plan.get("recruit_results")
    if raw_recruits is not None and not isinstance(raw_recruits, list):
        view["damaged"] = True  # a non-list hire record is shown as damaged, not iterated
    recruits = [r for r in raw_recruits if isinstance(r, dict)] if isinstance(raw_recruits, list) else []

    def skill_card(row: dict) -> dict:
        usage = [u for u in row.get("usage") or [] if isinstance(u, dict)]
        reason = next((_plain(u.get("preparation_error"), 200) for u in usage if u.get("preparation_error")), None)
        return {"status": _member(row.get("status"), _STATUSES) or "unknown",
                "built": row.get("built") is True,
                "reason": reason or _plain(row.get("skip_reason"), 200) or _plain(row.get("error"), 200),
                "selection_reason": _plain(row.get("reason"), 300),
                "agents": _ids(row.get("agent_ids"), 4),
                "skill_sha256": _hex([row, *usage], "skill_sha256", 64),
                "paper2agent_commit": _hex([row, *usage], "paper2agent_commit", 40),
                "selected_steps": _ids(row.get("step_ids")),
                "attached_steps": _ids(row.get("attached_steps")),
                "reference": _member(row.get("call_status"), _REFERENCES) or "unverified",
                "reference_reasons": list(dict.fromkeys(u["reason"] for u in usage if _member(u.get("reason"), _REASONS)))[:4]}

    def recruit_card(row: dict) -> dict:
        return {"status": _plain(row.get("status"), 40) or "unknown", "agent_id": (_ids([row.get("agent_id")], 1) or [None])[0],
                "step_ids": _ids(row.get("step_ids"))}

    used: set[int] = set()
    used_recruits: set[int] = set()
    papers = precedents.get("papers") if status == "ok" else None
    for paper in papers if isinstance(papers, list) else []:
        if not isinstance(paper, dict) or len(view["papers"]) >= 8:
            continue
        receipt = paper.get("fulltext_receipt") if isinstance(paper.get("fulltext_receipt"), dict) else {}
        citations = paper.get("citations") if isinstance(paper.get("citations"), list) else []
        keys = list(dict.fromkeys(pid for c in [*citations[:8], receipt.get("paper_id")]
                                  if isinstance(c, str) and (pid := paper_id(c))))[:4]
        row: dict[str, Any] = {"title": _plain(paper.get("title"), 300) or "제목 없음", "ids": keys,
                               "uses": ["planning"], "skill": None, "recruit": None}
        match = next((i for i, s in enumerate(skills) if i not in used and s.get("paper_id") in keys), None)
        if match is not None:
            used.add(match)
            row["uses"].append("paper_skill")
            row["skill"] = skill_card(skills[match])
        hire = next((i for i, r in enumerate(recruits) if i not in used_recruits and paper_id(str(r.get("paper") or "")) in keys), None)
        if hire is not None:
            used_recruits.add(hire)
            row["uses"].append("recruit")
            row["recruit"] = recruit_card(recruits[hire])
        view["papers"].append(row)
    # A selection or hire that names no listed paper still shows, with its own identifier only.
    for i, skill in enumerate(skills[:4]):
        if i not in used:
            pid = paper_id(str(skill.get("paper_id") or ""))
            view["papers"].append({"title": _plain(skill.get("title"), 300) or "제목 없음", "ids": [pid] if pid else [],
                                   "uses": ["paper_skill"], "skill": skill_card(skill), "recruit": None})
    for i, hire in enumerate(recruits[:4]):
        if i not in used_recruits:
            pid = paper_id(str(hire.get("paper") or ""))
            view["papers"].append({"title": _plain(hire.get("paper"), 300) or "논문 미기재", "ids": [pid] if pid else [],
                                   "uses": ["recruit"], "skill": None, "recruit": recruit_card(hire)})
    return view


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
        if not paper2agent.enabled(self.runner.s):
            return self.task, None, self.result({"status": "unavailable", "error": "recruit_disabled"})
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
        if not paper2agent.enabled(self.runner.s):
            # A conversion could already have spent money before a caller disabled preparation.
            return result.model_copy(update={"ok": False, "error": "recruit_disabled",
                                            "structured": {"status": "unavailable", "error": "recruit_disabled"},
                                            "text": "Paper2Skill disabled"})
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
