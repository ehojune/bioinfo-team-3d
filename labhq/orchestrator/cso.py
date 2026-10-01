"""Virtual-Biotech-style orchestration, adapted to a one-PI bioinformatics lab.

briefing (chief of staff) → CSO plan (JSON DAG + optional contract hires) → steps run in parallel
where dependencies allow (hibernate on HPC jobs, resume when they finish) → scientific reviewer
(3 criteria) → targeted revisions → CSO synthesis report.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..ask_results import ask_result, read_ask_results, rejected_step
from ..intake import (CLARIFYING_QUESTION_SCHEMA, QUESTION_RULE, has_structure, normalize_questions,
                      question_detail_lines, questions_summary)
from ..models import AskRequest, RunnerUnavailable, Task, TaskResult, hard_stop_kind, new_id, waiting
from ..research.contract import (RESEARCH_PLAN_SCHEMA, canonical_plan_json, classify_intake, freeze_plan,
                                 refresh_plan_approval, validate_research_plan)
from ..research.packs import configured_packs, pack_snapshot, render_pack_catalog
from ..util import clip, extract_json, output_relpath, short

if TYPE_CHECKING:
    from ..gateway.server import Hub

PLAN_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "clarifying_questions": {"type": "array", "maxItems": 4, "items": CLARIFYING_QUESTION_SCHEMA},
        "steps": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "properties": {"id": {"type": "string"}, "agent_id": {"type": "string"},
                           "instruction": {"type": "string"},
                           "outputs": {"type": "array", "items": {"type": "string"}},
                           "depends_on": {"type": "array", "items": {"type": "string"}}},
            "required": ["id", "agent_id", "instruction", "depends_on"]}},
        "recruit": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "properties": {"paper": {"type": "string"}, "repo": {"type": "string"},
                           "focus": {"type": "string"}, "reason": {"type": "string"}},
            "required": ["paper", "repo", "focus", "reason"]}},
        "notes": {"type": "string"},
    },
    "required": ["clarifying_questions", "steps", "recruit", "notes"],
}

REVIEW_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "verdict": {"type": "string", "enum": ["accept", "revise"]},
        "scores": {"type": "object", "additionalProperties": False,
                   "properties": {"addresses_question": {"type": "integer", "minimum": 1, "maximum": 5},
                                  "evidence": {"type": "integer", "minimum": 1, "maximum": 5},
                                  "thoroughness": {"type": "integer", "minimum": 1, "maximum": 5}},
                   "required": ["addresses_question", "evidence", "thoroughness"]},
        "issues": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "properties": {"step_id": {"type": "string"}, "problem": {"type": "string"}, "request": {"type": "string"}},
            "required": ["step_id", "problem", "request"]}},
    },
    "required": ["verdict", "scores", "issues"],
}

BRIEFING_PROMPT = """Prepare a briefing (≤300 words) for the CSO on this research request:
field context, recent developments (search the web if available), which datasets exist and how they can be
accessed (public vs controlled access, DUA constraints), and feasibility risks.

Request: {request}"""

PLAN_PROMPT = """Decompose the PI's request into steps for your team. You do not analyze anything yourself.

Team roster (use these agent ids exactly):
{roster}

Runner compute capabilities:
{capabilities}

Chief of staff briefing:
{briefing}

Rules:
- At most {max_steps} steps. Express order with depends_on; independent steps run in parallel.
- Declare each step's expected output names in outputs so dependencies can be checked.
- Use HPC jobs only when the assigned agent has labhq_hpc tools and a scheduler is available.
  Local CLI is available for light work. If a step needs unavailable compute, ask the PI in
  clarifying_questions before planning execution. Put a QC step after any data generation.
- If no roster member covers a required method, add a contract hire to `recruit` (paper + code repo +
  focus) and plan the step for whoever is closest; the PI decides whether to hire.
- {question_rule}

PI's request: {request}"""

RESEARCH_PLAN_PROMPT = """Create a frozen research contract for the PI's request. Do not execute or analyze.

Team roster (use these agent ids exactly, never an orchestration role):
{roster}

Runner compute capabilities:
{capabilities}

Chief of staff briefing:
{briefing}

Authoritative intake decision (copy it into `intake`):
{intake}

Selected domain packs (copy their exact id, version, and sha256 into `protocol.packs`):
{packs}

Contract rules:
- Return schema_version 2 and at most {max_steps} steps. Never silently drop a step to fit the limit.
- The brief states question, purpose, subject, scope, deliverables, and observable completion conditions.
- Explanatory/comparative work states a primary hypothesis, alternatives, and distinguishing observations.
  Exploratory/technical work uses its purpose and does not invent H0/H1.
- Freeze analysis unit, selection/exclusion, comparators, metrics, validation, resources, stop/approval
  conditions, data boundaries, and statistics applicability before execution. Every not_applicable item needs a reason.
- Each step declares phase, claim_ids, input_refs, outputs, checks, evidence_slots, and depends_on.
- Put QC after data generation. {question_rule}
- For every selected domain pack, fill top-level `pack_values[pack_key]` with its declared `fields`,
  a non-empty explanation for every `validators` id, and a non-empty result for every `acceptance` id.
  Domain packs may extend this contract but cannot weaken it. A missing/invalid value or conflict makes planning fail.
- PR 1 pilot stops after CP1 approval. Research steps will not run in this PR.

PI's request: {request}"""

STEP_PROMPT = """Overall request (context only): {request}

Your step ({step_id}): {instruction}

Teammates' upstream results are in the context section. Deliver: what you did, key results with file
paths, caveats and open questions. If you cannot proceed without a PI decision, return JSON with
"blocking_decision": "the specific question and choices". Do not proceed with the blocked work."""

REVIEW_PROMPT = """You are the scientific reviewer. Evaluate the team's work on the request below with three
criteria scored 1–5: addresses_question, evidence (how well conclusions are supported), thoroughness.
List concrete issues per step_id with a specific revision request. Use verdict "revise" only if fixing an
issue would materially change the conclusions.

Request: {request}

Team results:
{results}"""

SYNTH_PROMPT = """Write the final report for the PI.
Structure: 1) answer / recommendation, 2) evidence by step (with file paths), 3) reviewer concerns and how
they were addressed, 4) what would change the conclusion, 5) next steps (including any proposed contract hires).

Request: {request}

Team results:
{results}

Reviewer: {review}"""

WAKE_PROMPT = """Your HPC jobs finished:
{jobs}
Job scripts and logs are under jobs/ in your workspace ({workdir}). Check exit status and outputs, then
continue your step and report as instructed."""

WRAP_PROMPT = "Your turn limit was reached. Save any partial results under outputs/ and write outputs/PARTIAL_STATUS.md with what is done and what remains unfinished."

ASK_WAKE_PROMPT = """A blocking question from your previous turn has been answered:
Your earlier blocking question and the PI's answer:
{answers}

Continue the same step in this session. Treat another employee's answer as advice, not as PI approval.
Do not repeat the same question."""

CONSULT_PROMPT = """Answer one blocked employee's question using the request, plan and policy context below.
This is a read-only, one-answer consult. Do not call labhq_ask and do not approve installations,
destructive work, restricted-data access, budget overruns or work outside the request.

From: {sender}
Question: {question}
Why blocked: {why_blocked}
Tried: {tried}
Options: {options}

Request: {request}
Plan: {plan}"""


def continuation_prompt(task: Task, updates: str, *, resumable: bool,
                        previous_result: TaskResult | None, context_chars: int) -> str:
    """One policy for continuing a task, including engines without session resume."""
    if resumable:
        return updates
    previous = ""
    if previous_result:
        previous = previous_result.text
        if previous_result.structured is not None:
            previous += "\n" + json.dumps(previous_result.structured, ensure_ascii=False)
    return (f"Original instruction:\n{task.prompt}\n\nOriginal context:\n{task.context or '(none)'}"
            f"\n\nPrevious turn:\n{clip(previous, context_chars) or '(none)'}"
            f"\n\nContinuation updates:\n{updates}")


def format_roster(agents: list[dict]) -> str:
    lines = []
    for a in agents:
        tag = " [파견직]" if a.get("employment") == "contract" else ""
        tools = f" · tools: {', '.join(a['mcp'])}" if a.get("mcp") else ""
        allowed = a.get("tools") or []
        builtin = a.get("builtin_tools")
        if a.get("engine") == "codex":
            access = "read-only" if a.get("sandbox") == "read-only" else "write-capable"
        else:
            access = "read-only" if a.get("permission_mode") == "plan" or (
                builtin and not any(x in builtin for x in ("Write", "Edit", "Bash"))) or (
                allowed and not any(x.startswith(("Write", "Edit", "Bash")) for x in allowed)) else "write-capable"
        lines.append(f"- {a['id']}{tag}: {a['name']} – {a['role']} "
                     f"({a['engine']}/{a.get('model') or 'default'}); {access}; "
                     f"tools={allowed or builtin or 'default'}; denied={a.get('disallowed_tools') or []}; "
                     f"labhq_hpc={'yes' if a.get('hpc_tools') else 'no'}; "
                     f"max_turns={a.get('max_turns') or 'unlimited'}{tools}")
    return "\n".join(lines)


def _reaches(steps: list[dict], start: str, target: str) -> bool:
    """True when `start` already depends on `target`, directly or through other steps."""
    deps = {s["id"]: s["depends_on"] for s in steps}
    seen, stack = set(), [start]
    while stack:
        sid = stack.pop()
        if sid == target:
            return True
        if sid not in seen:
            seen.add(sid)
            stack.extend(deps.get(sid, []))
    return False


ORCHESTRATION_ROLES = frozenset({"cso", "chief_of_staff", "sci_reviewer"})


def qa_text(entry: Any) -> str:
    """A PI answer together with what it answers (older records stored the answer alone)."""
    if isinstance(entry, dict):
        qs = entry.get("questions") or ([entry["question"]] if entry.get("question") else [])
        details = entry.get("question_details") or []
        asked = "\n".join("\n".join([f"Q{i}. {q}", *(question_detail_lines(details[i - 1])
                                                    if i <= len(details) and isinstance(details[i - 1], dict)
                                                    else [])])
                          for i, q in enumerate(qs, 1))
        return f"{asked}\nPI answer: {entry.get('answer', '')}" if asked else f"PI answer: {entry.get('answer', '')}"
    return f"PI answer: {entry}"


def validate_steps(raw: list[dict], known: set[str], max_steps: int,
                   excluded: frozenset[str] | set[str] = ORCHESTRATION_ROLES) -> tuple[list[dict], list[str]]:
    warnings, steps, seen = [], [], set()
    for i, s in enumerate(raw[:max_steps]):
        if s.get("agent_id") in excluded:
            warnings.append(f"step {s.get('id') or i + 1}: orchestration role removed")
            continue
        sid = str(s.get("id") or f"s{i + 1}")
        if sid in seen:
            sid = f"{sid}_{i}"
        seen.add(sid)
        steps.append({"id": sid, "agent_id": s.get("agent_id", ""), "instruction": s.get("instruction", ""),
                      "depends_on": [str(d) for d in s.get("depends_on") or []],
                      "outputs": [str(o) for o in s.get("outputs") or []]})
    ids = {s["id"] for s in steps}
    producers: dict[str, list[str]] = {}
    for s in steps:
        for o in s["outputs"]:
            producers.setdefault(o, []).append(s["id"])
    for s in steps:
        invalid = [d for d in s["depends_on"] if d not in ids or d == s["id"]]
        if invalid:
            raise ValueError(f"step {s['id']}: invalid dependencies {invalid}")
    # Infer a dependency only from an unambiguous reference: another step's id, or an output name that exactly
    # one other step produces and this step does not produce itself. Never infer one that would close a cycle.
    for s in steps:
        for other in steps:
            if other["id"] == s["id"] or other["id"] in s["depends_on"]:
                continue
            names = [other["id"]] + [o for o in other["outputs"]
                                     if producers.get(o) == [other["id"]] and o not in s["outputs"]]
            if not any(name and re.search(r"(?<![\w])" + re.escape(name) + r"(?![\w])", s["instruction"])
                       for name in names):
                continue
            if _reaches(steps, other["id"], s["id"]):
                warnings.append(f"step {s['id']}: reference to {other['id']} not added (would create a cycle)")
                continue
            s["depends_on"].append(other["id"])
            warnings.append(f"step {s['id']}: added dependency on {other['id']} referenced in instruction")
    for s in steps:
        if s["agent_id"] not in known:
            warnings.append(f"step {s['id']}: unknown agent {s['agent_id']!r}")
    # Reject cycles before dispatch.
    indeg = {s["id"]: len(s["depends_on"]) for s in steps}
    children: dict[str, list[str]] = {s["id"]: [] for s in steps}
    for s in steps:
        for d in s["depends_on"]:
            children[d].append(s["id"])
    queue, visited = [k for k, v in indeg.items() if v == 0], 0
    while queue:
        k = queue.pop()
        visited += 1
        for c in children[k]:
            indeg[c] -= 1
            if indeg[c] == 0:
                queue.append(c)
    if visited != len(steps):
        raise ValueError("plan has a dependency cycle")
    return steps, warnings


class BudgetExceeded(RuntimeError):
    pass


def valid_review(value: Any) -> bool:
    """Check every required REVIEW_SCHEMA field without a new JSON Schema dependency."""
    def matches(item: Any, schema: dict) -> bool:
        typ = schema.get("type")
        if typ == "object":
            if not isinstance(item, dict):
                return False
            props = schema["properties"]
            if not set(schema.get("required", [])).issubset(item):
                return False
            if schema.get("additionalProperties") is False and set(item) - set(props):
                return False
            return all(matches(v, props[k]) for k, v in item.items())
        if typ == "array":
            return isinstance(item, list) and all(matches(v, schema["items"]) for v in item)
        if typ == "integer":
            return (type(item) is int and schema.get("minimum", item) <= item <= schema.get("maximum", item))
        if typ == "string":
            return isinstance(item, str) and item in schema.get("enum", [item])
        return False

    return matches(value, REVIEW_SCHEMA)


def failure_kind(outcome: TaskResult | BaseException) -> str | None:
    """Classify dispatch outcomes with this retry table.

    Outcome                                      Kind
    Successful result                            None
    Offline, timeout, empty result/CLI stream,   transient
      explicit rate-limit/overload/5xx/network signal
    Policy/approval/budget/cancel/invalid input, terminal
      ordinary nonzero exit, other error

    An exit code alone is never evidence that another run is safe.
    """
    if isinstance(outcome, asyncio.CancelledError):
        return "terminal"
    if isinstance(outcome, BudgetExceeded):
        return "terminal"
    if isinstance(outcome, (asyncio.TimeoutError, TimeoutError, ConnectionError)):
        return "transient"
    if isinstance(outcome, BaseException):
        return "terminal"
    if outcome.error_kind == "ask_rejected":
        return "terminal"
    if outcome.ok and (outcome.text.strip() or outcome.structured is not None or waiting(outcome)):
        return None
    if outcome.pending_jobs:
        # A failed CLI turn can still have submitted live jobs. Replaying the step
        # risks another qsub even when the CLI supports session resume.
        return "terminal"
    error = (outcome.error or "").lower()
    if any(word in error for word in ("policy", "permission", "denied", "approval", "auth",
                                      "budget", "cancel", "ineligibletier", "401")):
        return "terminal"
    if (("invalid model selection" in error and "is not recognized" in error)
            or any(word in error for word in ("model catalog", "model catalogue", "failed to fetch models"))):
        return "transient"
    if any(word in error for word in ("validat", "invalid", "not found")):
        return "terminal"
    if outcome.ok or (not outcome.text.strip() and
                      (not error or "empty cli stream" in error or "no result event" in error)):
        return "transient"
    if any(word in error for word in ("timeout", "timed out", "rate limit", "rate-limit", "429",
                                      "overload", "capacity", "temporar", "resource exhausted",
                                      "too many requests", "connection reset", "connection refused",
                                      "connection aborted", "broken pipe", "network unreachable",
                                      "runner restarted", "try again")):
        return "transient"
    if re.search(r"\b5\d{2}\b|\b5xx\b", error):
        return "transient"
    return "terminal"


class Orchestrator:
    def __init__(self, hub: "Hub"):
        self.hub = hub
        self.cfg = hub.s.orchestrator
        self.cost: dict[str, float] = {}
        self.attempts: dict[str, dict[str, int]] = {}
        self.budget_locks: dict[str, asyncio.Lock] = {}
        self.budget_denials: dict[str, str] = {}
        self.budget_outcomes: dict[str, list[dict]] = {}
        self.consult_locks: dict[tuple[str | None, str], tuple[asyncio.Lock, int]] = {}

    async def _emit(self, rid: str, typ: str, data: dict) -> None:
        await self.hub.publish({"type": typ, "ts": time.time(), "request_id": rid, "data": data})

    def _last_agent_session(self, request_id: str | None, agent_id: str) -> tuple[str | None, str | None]:
        candidates = []
        for entry in self.hub.store.all("task").values():
            payload = entry.get("payload") or {}
            result = entry.get("result") or {}
            if (entry.get("request_id") == request_id and payload.get("agent_id") == agent_id and
                    result.get("session_id")):
                candidates.append((entry.get("dispatched_at", 0), result["session_id"], result.get("workdir")))
        if not candidates:
            return None, None
        _, session_id, workdir = max(candidates)
        return session_id, workdir

    def _ask_scope(self, body: dict) -> tuple:
        """Recover a stable step identity from the durable task ledger, including old asks."""
        rid, tid = body.get("request_id"), body.get("task_id")
        task = self.hub.store.get("task", tid) if tid else None
        task = task or {}
        meta = (task.get("payload") or {}).get("meta") or {}
        kind = task.get("kind") or meta.get("kind")
        if kind == "direct" or (kind in {None, "wrap_up"} and
                                self.hub.requests.get(rid, {}).get("mode") == "direct"):
            return (rid, "direct")
        sid = task.get("step_id") or meta.get("step_id")
        return (rid, "step", sid) if sid else (rid, "task", tid)

    @asynccontextmanager
    async def _consult_lock(self, request_id: str | None, agent_id: str):
        # Lock the target for the request, including selection of its session/workdir.
        # A session can rotate after each consult; locking its old ID would let a
        # waiter overlap a new consult using the updated ID and the same workdir.
        key = (request_id, agent_id)
        lock, users = self.consult_locks.get(key, (asyncio.Lock(), 0))
        self.consult_locks[key] = (lock, users + 1)
        try:
            async with lock:
                yield
        finally:
            _, users = self.consult_locks[key]
            if users == 1:
                del self.consult_locks[key]
            else:
                self.consult_locks[key] = (lock, users - 1)

    def _consult_resource_busy(self, agent_id: str, session_id: str | None, workdir: str | None) -> bool:
        for entry in self.hub.store.all("task").values():
            payload = entry.get("payload") or {}
            if entry.get("completed") or payload.get("agent_id") != agent_id:
                continue
            # Include dispatched-but-not-yet-accepted tasks: delivery can be in flight.
            meta = payload.get("meta") or {}
            if meta.get("kind") == "consult":
                continue
            active_workdir = meta.get("workdir")
            if ((session_id and payload.get("resume_session_id") == session_id) or
                    (workdir and active_workdir and Path(workdir).resolve() == Path(active_workdir).resolve())):
                return True
        return False

    async def answer_ask(self, ask: AskRequest, runner_id: str) -> None:
        """Route one bounded question. Hard stops are classified before any model runs."""
        signature = hashlib.sha256(
            f"{ask.task_id}\0{ask.to}\0{ask.question.strip().casefold()}".encode("utf-8")
        ).hexdigest()
        entries = self.hub.store.all("ask")
        current = entries.get(ask.id) or {}
        scope = self._ask_scope(ask.model_dump())
        self.hub.store.put("ask", ask.id, {"ask": ask.model_dump(mode="json"), "origin": runner_id,
                                           **current, "signature": signature, "scope": scope})
        for other_id, entry in entries.items():
            if other_id != ask.id and entry.get("signature") == signature and entry.get("state") == "resolved":
                cached = ask_result(**{**entry["answer"], "cached": True})
                await self.hub.resolve_ask(ask, runner_id, cached)
                return

        accepted = [entry for other_id, entry in entries.items() if other_id != ask.id and
                    entry.get("state") != "rejected" and
                    (entry.get("answer") or {}).get("status") != "rejected"]
        same_step = [entry for entry in accepted if
                     tuple(entry.get("scope") or self._ask_scope(entry.get("ask") or {})) == scope]
        task_count = len(same_step)
        target_count = sum((entry.get("ask") or {}).get("to") == ask.to for entry in same_step)
        request_count = sum((entry.get("ask") or {}).get("request_id") == ask.request_id for entry in accepted)
        limit_reason = None
        if task_count >= 3:
            limit_reason = "이 step/task의 질의 상한(3회)에 도달했습니다"
        elif target_count >= 2:
            limit_reason = "이 step/task에서 같은 대상에게 묻는 상한(2회)에 도달했습니다"
        elif request_count >= 12:
            limit_reason = "이 요청의 질의 상한(12회)에 도달했습니다"
        if limit_reason:
            self.hub.store.put("ask", ask.id, {**(self.hub.store.get("ask", ask.id) or {}),
                                                "state": "rejected"})
            await self.hub.resolve_ask(ask, runner_id, ask_result(reason=limit_reason, **{
                "from": "labhq", "routed_to": "cso", "remaining_asks": 0}))
            return

        stop = hard_stop_kind(ask)
        requested = ask.to
        if stop:
            routed = "pi"
        elif requested == "pi":
            routed = "cso"
        elif requested == "facilities" and "facilities" not in self.hub.agents:
            routed = "cso"
        else:
            routed = requested.removeprefix("colleague:") if requested.startswith("colleague:") else requested

        entry = self.hub.store.get("ask", ask.id) or {}
        self.hub.store.put("ask", ask.id, {**entry, "hard_stop": stop, "routed_to": routed})
        if routed == "pi":
            decision = await self.hub.request_approval(
                kind="question", request_id=ask.request_id,
                summary=f"Hard stop ({stop}): {ask.question}",
                detail={"ask_id": ask.id, "from": ask.agent_id, "why_blocked": ask.why_blocked,
                        "options": ask.options},
            )
            note = str(decision.get("note") or "").strip()
            answer = note or ("PI가 진행을 허용하지 않았습니다" if not decision.get("approved") else
                              "PI가 승인했지만 답변을 남기지 않았습니다")
            await self.hub.resolve_ask(ask, runner_id, ask_result(answer=answer, decision=decision,
                **{"from": "pi", "routed_to": "pi", "hard_stop": stop}))
            return

        if routed not in self.hub.agents:
            await self.hub.resolve_ask(ask, runner_id, ask_result(
                reason=f"대상 직원 {routed!r}이 roster에 없습니다",
                **{"from": "labhq", "routed_to": routed}))
            return

        request = self.hub.requests.get(ask.request_id or "", {})
        prompt = CONSULT_PROMPT.format(
            sender=ask.agent_id, question=ask.question, why_blocked=ask.why_blocked,
            tried=json.dumps(ask.tried, ensure_ascii=False), options=json.dumps(ask.options, ensure_ascii=False),
            request=clip(request.get("text") or "", 4000), plan=clip(json.dumps(request.get("plan") or {},
                                                                                 ensure_ascii=False), 6000),
        )
        overrides = {"sandbox": "read-only", "permission_mode": "plan", "builtin_mcp": [],
                     "builtin_tools": "Read,Glob,Grep", "tools": []}
        async with self._consult_lock(ask.request_id, routed):
            session_id, workdir = self._last_agent_session(ask.request_id, routed)
            if requested.startswith("colleague:"):
                session_id, workdir = None, None
            if routed == self.cfg.cso_agent:
                session_id = request.get("cso_session_id") or session_id
                workdir = request.get("cso_workdir") or workdir
            if self._consult_resource_busy(routed, session_id, workdir):
                session_id, workdir = None, None
            consult = Task(
                agent_id=routed, request_id=ask.request_id, prompt=prompt,
                resume_session_id=session_id if self.hub.supports_resume(routed) else None,
                meta={"kind": "consult", "ask_id": ask.id, "title": f"{ask.agent_id} 질의 답변",
                      "agent_overrides": overrides, **({"workdir": workdir} if workdir else {})},
            )
            result = await self.run_step(consult)
            if routed == self.cfg.cso_agent and result.session_id:
                request["cso_session_id"], request["cso_workdir"] = result.session_id, result.workdir
                if ask.request_id in self.hub.requests:
                    self.hub.save_request(ask.request_id)
        answered = result.ok and bool(result.text.strip())
        answer = result.text.strip() if answered else f"상담 실패: {result.error or 'empty answer'}"
        await self.hub.resolve_ask(ask, runner_id, ask_result(
            answer=answer if answered else None, reason=None if answered else answer,
            **{"from": routed, "routed_to": routed, "remaining_asks": max(0, 2 - task_count)}))

    # ---------- one agent step, including HPC hibernate/wake cycles ----------
    async def run_step(self, task: Task) -> TaskResult:
        rid = task.request_id or ""
        async def dispatch_with_retry(current: Task, max_attempts: int | None = None) -> TaskResult:
            key = str(current.meta.get("step_id") or current.meta.get("kind") or current.id)
            limit = max_attempts or self.cfg.step_max_attempts
            first_attempt = min(getattr(self.hub, "recovery_attempt", lambda _task: 1)(current), limit)
            previous_workdir = current.meta.get("workdir")
            previous_session = current.resume_session_id
            previous_result = None
            retry_answers = []
            for attempt in range(first_attempt, limit + 1):
                await self._check_budget(rid)
                self.attempts.setdefault(rid, {})[key] = self.attempts.get(rid, {}).get(key, 0) + 1
                attempt_task = current.model_copy(update={"id": current.id if attempt == 1 else new_id("task"),
                                                  "resume_session_id": previous_session,
                                                  "meta": {**current.meta, "attempt": attempt,
                                                           **({"workdir": previous_workdir} if previous_workdir else {})}})
                if attempt > 1:
                    can_resume = bool(previous_session and self.hub.supports_resume(current.agent_id))
                    attempt_task = attempt_task.model_copy(update={
                        "prompt": continuation_prompt(
                            current, "\n\n".join(["Retry the same task after the transient failure.", *retry_answers]),
                            resumable=can_resume,
                            previous_result=previous_result, context_chars=self.cfg.context_chars_per_step),
                        "context": "", "resume_session_id": previous_session if can_resume else None})
                await self._emit(rid, "request.step_attempt", {"step_id": key, "attempt": attempt})
                offline = False
                try:
                    res = await self.hub.dispatch(attempt_task)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    res = TaskResult(task_id=attempt_task.id, agent_id=current.agent_id, ok=False, error=str(exc))
                    kind = failure_kind(exc)
                    offline = isinstance(exc, RunnerUnavailable)
                else:
                    self.cost[rid] = self.cost.get(rid, 0.0) + (res.cost_usd or 0.0)
                    kind = failure_kind(res)
                    previous_workdir = res.workdir or previous_workdir
                    if res.session_id and self.hub.supports_resume(current.agent_id):
                        previous_session = res.session_id
                # Audit even failed/exceptional attempts. Keep answered asks across
                # every retry, including engines without session resume.
                answers = getattr(self.hub, "ask_results_for_task", lambda _tid: [])(res.task_id)
                outcome = read_ask_results(answers)
                if outcome["status"] == "rejected":
                    res = rejected_step(res, outcome["reason"])
                    kind = "terminal"
                elif outcome["answer"]:
                    update = ASK_WAKE_PROMPT.format(answers=outcome["answer"])
                    if update not in retry_answers:
                        retry_answers.append(update)
                await self._check_budget(rid, block=False)
                previous_result = res
                if kind is None:
                    return res
                if rid in self.budget_denials:
                    if res.ok:
                        res = res.model_copy(update={"ok": False, "error": "empty result"})
                    return res  # this attempt ran; only its not-yet-started retry is blocked
                if kind != "transient" or attempt == limit:
                    if res.ok:
                        res = res.model_copy(update={"ok": False, "error": "empty result"})
                    return res
                if offline:
                    await self._emit(rid, "request.step_wait", {"step_id": key, "agent_id": current.agent_id,
                                                                 "reason": "runner offline"})
                    if not await self.hub.wait_agent_online(current.agent_id, self.cfg.runner_reconnect_timeout_s):
                        return res.model_copy(update={"error": "runner reconnect timeout"})
                else:
                    await asyncio.sleep(self.cfg.step_retry_backoff_s * 2 ** (attempt - 1))
                await self._emit(rid, "request.step_retry", {"step_id": key, "attempt": attempt,
                                                               "next_attempt": attempt + 1,
                                                               "reason": res.error or "empty result"})
            raise AssertionError("unreachable")

        res = await dispatch_with_retry(task)
        if (not res.ok and res.error_kind == "error_max_turns" and res.session_id
                and self.hub.supports_resume(task.agent_id)):
            wrap = Task(agent_id=task.agent_id, request_id=rid,
                        prompt=continuation_prompt(task, WRAP_PROMPT, resumable=True,
                                                   previous_result=res, context_chars=self.cfg.context_chars_per_step),
                        resume_session_id=res.session_id,
                        meta={**task.meta, "kind": "wrap_up", "parent_task": res.task_id,
                              "workdir": res.workdir, "agent_overrides": {"max_turns": 2},
                              "outputs": ["PARTIAL_STATUS.md"]})
            try:
                partial = await dispatch_with_retry(wrap, max_attempts=1)
                note = ("partial results saved" if partial.outputs else
                        "status note missing" if partial.ok else partial.error)
                res = res.model_copy(update={"partial_results": bool(partial.outputs),
                                             "outputs": list(dict.fromkeys([*res.outputs, *partial.outputs])),
                                             "error": f"{res.error or 'error_max_turns'}; wrap-up: {note}"})
            except BudgetExceeded:
                pass
        cycles = 0
        while res.ok and waiting(res) and cycles < self.cfg.max_wake_cycles:
            cycles += 1
            prompts = []
            if res.pending_asks:
                answers = await self.hub.wait_asks(res.pending_asks)
                outcome = read_ask_results(answers)
                if outcome["status"] == "rejected":
                    return rejected_step(res, outcome["reason"])
                prompts.append(ASK_WAKE_PROMPT.format(answers=outcome["answer"]))
            if res.pending_jobs:
                info = await self.hub.wait_jobs(res.task_id)
                jobs = "\n".join(f"- {j['job_id']} ({j.get('name') or ''}): {j['state']} exit={j.get('exit_status')}"
                                 for j in info.get("jobs", []))
                prompts.append(WAKE_PROMPT.format(jobs=jobs, workdir=res.workdir))
            title = ("HPC 결과와 질의 답변을 받고 이어서 작업" if res.pending_jobs and res.pending_asks else
                     "HPC 결과 확인 후 이어서 작업" if res.pending_jobs else "질의 답변을 받고 이어서 작업")
            meta = {**task.meta, "kind": task.meta.get("kind", "step"),
                    "parent_task": res.task_id, "workdir": res.workdir,
                    "title": title}
            can_resume = bool(res.session_id and self.hub.supports_resume(task.agent_id))
            wake = Task(
                agent_id=task.agent_id, request_id=task.request_id, output_schema=task.output_schema,
                prompt=continuation_prompt(task, "\n\n".join(prompts), resumable=can_resume,
                                           previous_result=res, context_chars=self.cfg.context_chars_per_step),
                meta=meta, resume_session_id=res.session_id if can_resume else None,
            )
            res = await dispatch_with_retry(wake)
        if res.ok and waiting(res):
            res = res.model_copy(update={
                "ok": False, "error_kind": "wake_limit",
                "error": f"wake cycle limit ({self.cfg.max_wake_cycles}) reached; "
                         f"pending asks: {res.pending_asks}; pending jobs: {res.pending_jobs}",
                "pending_asks": [], "pending_jobs": [], "blocking_decision": None,
            })
        return res

    async def _check_budget(self, rid: str, *, block: bool = True) -> None:
        # A completed attempt keeps its result; a denied budget only blocks the next attempt.
        async with self.budget_locks.setdefault(rid, asyncio.Lock()):
            if rid in self.budget_denials:
                if block:
                    raise BudgetExceeded(self.budget_denials[rid])
                return
            limit = self.hub.requests.get(rid, {}).get("budget_usd") or self.hub.s.policy.budget.per_request_usd
            spent = self.cost.get(rid, 0.0)
            if not limit or spent <= limit:
                return
            try:
                dec = await self.hub.request_approval(kind="budget", request_id=rid,
                                                      summary=f"예산 초과: ${spent:.2f} / ${limit:.2f} — 계속 진행할까요?",
                                                      detail={"spent_usd": spent, "limit_usd": limit,
                                                              "requested_budget_usd": max(limit * 2, spent)})
            except Exception as exc:
                dec = {"approved": False, "note": str(exc)}
            spent = self.cost.get(rid, 0.0)  # include concurrently completed attempts in this decision
            approved = bool(dec.get("approved"))
            outcome = {"spent_usd": round(spent, 4), "limit_usd": limit, "approved": approved}
            self.budget_outcomes.setdefault(rid, []).append(outcome)
            await self._emit(rid, "request.budget_exceeded", outcome)
            if approved:
                self.hub.requests[rid]["budget_usd"] = max(limit * 2, spent)
            else:
                reason = f"budget exceeded (${spent:.2f} > ${limit:.2f}); approval denied"
                self.budget_denials[rid] = reason
                if block:
                    raise BudgetExceeded(reason)

    # ---------- DAG ----------
    async def run_dag(self, rid: str, request: str, steps: list[dict], results: dict[str, TaskResult],
                      only: set[str] | None = None, feedback: dict[str, str] | None = None) -> None:
        by_id = {s["id"]: s for s in steps}
        todo = {s["id"] for s in steps if only is None or s["id"] in only}
        running: dict[str, asyncio.Task] = {}
        req_state = self.hub.requests.get(rid)
        # PI answers to blocking questions survive a gateway restart (the re-run step still needs them).
        decisions: dict[str, Any] = dict((req_state or {}).get("step_decisions") or {})
        sem = asyncio.Semaphore(self.cfg.max_parallel_steps)

        async def skip(sid: str, reason: str, upstream_ids: list[str] | None = None) -> None:
            previous = results.get(sid)
            if feedback and sid in feedback and previous and previous.ok:
                results[sid] = previous.model_copy(update={"revision_failed": f"skipped: {reason}"})
            else:
                results[sid] = TaskResult(task_id="", agent_id=by_id[sid]["agent_id"], ok=False,
                                          error=f"skipped: {reason}")
            await self._emit(rid, "request.step_skipped", {"step_id": sid, "status": "skipped",
                                                            "upstream": upstream_ids or [], "reason": reason})

        def upstream(step: dict) -> str:
            parts = []
            for d in step["depends_on"]:
                r = results.get(d)
                if r:
                    head = f"## {d} · {by_id[d]['agent_id']}" + ("" if r.ok else f" (FAILED: {short(r.error, 200)})")
                    artifacts = [{"workdir_id": r.workdir_id, "path": path}
                                 for path in r.outputs]
                    paths = "\n".join(str(Path(r.workdir) / path) for path in r.outputs) if r.workdir else ""
                    parts.append(f"{head}\nDeclared output artifacts: {json.dumps(artifacts)}\n"
                                 f"Readable files:\n{paths}\n{clip(r.text, self.cfg.context_chars_per_step)}")
            return "\n\n".join(parts)

        async def run_one(step: dict) -> TaskResult:
            prompt = STEP_PROMPT.format(request=request, step_id=step["id"], instruction=step["instruction"])
            decision = decisions.get(step["id"])
            ctx = upstream(step)
            updates = []
            if decision:
                outcome = read_ask_results([decision])
                if outcome["status"] == "rejected":
                    previous = (TaskResult.model_validate(decision["previous_result"])
                                if decision.get("previous_result") else
                                TaskResult(task_id="", agent_id=step["agent_id"], ok=True))
                    return rejected_step(previous, outcome["reason"])
                updates.append(ASK_WAKE_PROMPT.format(answers=qa_text(decision)))
            revising = bool(feedback and step["id"] in feedback)
            if revising:
                updates.append(f"[Scientific reviewer feedback — revise your step]\n{feedback[step['id']]}")
            previous = results.get(step["id"])
            if previous is None and decision and decision.get("previous_result"):
                previous = TaskResult.model_validate(decision["previous_result"])
            session_id = (previous.session_id if previous and revising else
                          decision.get("session_id") if decision else None)
            can_resume = bool(session_id and self.hub.supports_resume(step["agent_id"]))
            upstream_dirs = [results[d].workdir for d in step["depends_on"]
                             if d in results and results[d].workdir and results[d].outputs]
            task = Task(agent_id=step["agent_id"], request_id=rid, prompt=prompt, context=ctx,
                        resume_session_id=session_id if can_resume else None,
                        meta={"kind": "step", "step_id": step["id"], "request": request,
                              "instruction": step["instruction"],
                              "revision": self.hub.requests.get(rid, {}).get("pending_revisions", {})
                              .get(step["id"], {}).get("revision", 0),
                              "title": f"{step['id']}: {step['instruction'][:100]}" + (" (리뷰 반영 수정)" if feedback else ""),
                               "project_dirs": self.hub.requests.get(rid, {}).get("project_dirs", []),
                               "upstream_dirs": upstream_dirs, "outputs": step.get("outputs", []),
                               **({"workdir": decision["workdir"]} if decision and decision.get("workdir") else
                                  {"workdir": previous.workdir} if previous and previous.workdir and feedback
                                  and step["id"] in feedback else {})})
            if updates:
                task = task.model_copy(update={
                    "prompt": continuation_prompt(task, "\n\n".join(updates), resumable=can_resume,
                                                   previous_result=previous,
                                                   context_chars=self.cfg.context_chars_per_step),
                    "context": ""})
            async with sem:
                return await self.run_step(task)

        while todo or running:
            # ready = no dependency still pending in this run (deps outside `only` already have results)
            ready = [sid for sid in todo if not any(d in todo or d in running for d in by_id[sid]["depends_on"])]
            for sid in ready:
                todo.discard(sid)
                if rid in self.budget_denials:
                    await skip(sid, self.budget_denials[rid])
                    continue
                blocked = [d for d in by_id[sid]["depends_on"] if d not in results or not results[d].ok]
                if blocked:
                    reason = ", ".join(f"{d}: {results[d].error if d in results else 'not run'}" for d in blocked)
                    await skip(sid, f"upstream {reason}", blocked)
                    continue
                running[sid] = asyncio.create_task(run_one(by_id[sid]))
            if not running:
                if ready:
                    continue
                for sid in todo:
                    results[sid] = TaskResult(task_id="", agent_id=by_id[sid]["agent_id"], ok=False, error="unmet dependencies")
                break
            done, _ = await asyncio.wait(running.values(), return_when=asyncio.FIRST_COMPLETED)
            for sid, t in list(running.items()):
                if t in done:
                    running.pop(sid)
                    try:
                        outcome = t.result()
                    except BudgetExceeded as e:
                        await skip(sid, str(e))
                        continue
                    except asyncio.CancelledError:
                        outcome = TaskResult(task_id="", agent_id=by_id[sid]["agent_id"], ok=False, error="cancelled")
                    except Exception as e:
                        outcome = TaskResult(task_id="", agent_id=by_id[sid]["agent_id"], ok=False, error=str(e))
                    if outcome.ok and by_id[sid].get("outputs"):
                        missing = [name for name in by_id[sid]["outputs"]
                                   if output_relpath(name) not in outcome.outputs]
                        if missing:
                            outcome = outcome.model_copy(update={"ok": False, "missing_outputs": missing,
                                                                 "error": f"incomplete: missing outputs: {', '.join(missing)}"})
                    previous = results.get(sid)
                    if (feedback and sid in feedback and previous and previous.ok and not outcome.ok
                            and outcome.error_kind not in {"ask_rejected", "wake_limit"}):
                        outcome = previous.model_copy(update={"revision_failed":
                            f"{outcome.error_kind or failure_kind(outcome) or 'terminal'}: {outcome.error or 'unknown error'}"})
                    results[sid] = outcome
                    await self._emit(rid, "request.step_done", {"step_id": sid, "ok": results[sid].ok,
                                                                "agent_id": by_id[sid]["agent_id"],
                                                                "attempts": self.attempts.get(rid, {}).get(sid, 0),
                                                                "reason": results[sid].error})
                    res = results[sid]
                    structured = res.structured if isinstance(res.structured, dict) else extract_json(res.text)
                    question = res.blocking_decision or (structured.get("blocking_decision")
                                                         if isinstance(structured, dict) else None)
                    if res.ok and isinstance(question, str) and question.strip():
                        if sid in decisions:
                            if req_state is not None:
                                req_state["pending_questions"] = [question.strip()]
                                self.hub.save_request(rid)
                            raise RuntimeError(f"step {sid} still requires a PI decision after its answer")
                        # Forget the question-only result before waiting (durably): a restart during the wait must
                        # re-run the step, not treat it as done and feed dependents a question.
                        results.pop(sid, None)
                        if req_state is not None:
                            req_state["pending_questions"] = [question.strip()]
                            self.hub.save_request(rid)
                        if not hasattr(self.hub, "store"):  # lightweight unit-test hubs
                            decision = await self.hub.request_approval(
                                kind="clarify", request_id=rid,
                                summary=f"Step {sid} needs a PI decision:\n{question.strip()}")
                            answer = ask_result(decision=decision, **{"from": "pi"})
                            answers = [answer]
                        else:
                            ask = AskRequest(task_id=res.task_id, agent_id=res.agent_id, request_id=rid,
                                             to="cso", question=question.strip(),
                                             why_blocked=f"step {sid}의 묻고 멈추는 게이트", wait="hibernate")
                            origin = self.hub.agent_runner.get(res.agent_id, "")
                            self.hub.store.put("ask", ask.id, {"state": "working", "origin": origin,
                                                               "ask": ask.model_dump(mode="json")})
                            await self.hub.publish({"type": "agent.ask", "ts": time.time(),
                                                    "task_id": res.task_id, "agent_id": res.agent_id,
                                                    "request_id": rid, "data": ask.model_dump(mode="json")})
                            await self.answer_ask(ask, origin)
                            answers = await self.hub.wait_asks([ask.id])
                            answer = answers[0]
                        outcome = read_ask_results(answers)
                        if outcome["status"] == "rejected":
                            results[sid] = rejected_step(res, f"{question.strip()}: {outcome['reason']}")
                            if req_state is not None:
                                req_state["pending_questions"] = []
                                self.hub.save_request(rid)
                            await self._emit(rid, "request.step_done", {"step_id": sid, "ok": False,
                                "agent_id": res.agent_id, "reason": results[sid].error})
                            continue
                        # Keep the question with the answer: "b" or "the second option" means nothing alone.
                        decisions[sid] = {**ask_result(**answer), "question": question.strip(),
                                          "from": answer.get("from"), "session_id": res.session_id,
                                          "workdir": res.workdir, "previous_result": res.model_dump(mode="json")}
                        if req_state is not None:
                            req_state["pending_questions"] = []
                            req_state.setdefault("step_decisions", {})[sid] = decisions[sid]
                            self.hub.save_request(rid)
                        todo.add(sid)

    @staticmethod
    def format_results(steps: list[dict], results: dict[str, TaskResult], n: int) -> str:
        out = []
        by_id = {s["id"]: s for s in steps}

        def causes(sid: str, seen: set[str] | None = None) -> list[str]:
            seen = seen or set()
            if sid in seen:
                return []
            seen.add(sid)
            r = results.get(sid)
            if not r:
                return [f"{sid}: not run"]
            chain = [f"{sid} [{r.error_kind or failure_kind(r) or 'terminal'}]: {r.error or 'unknown error'}"]
            for dep in by_id[sid].get("depends_on", []):
                if dep not in results or not results[dep].ok:
                    chain.extend(causes(dep, seen))
            return chain

        for s in steps:
            r = results.get(s["id"])
            status = ("ok" if r and r.ok else "INCOMPLETE" if r and r.missing_outputs else
                      "SKIPPED" if r and (r.error or "").startswith("skipped:") else "FAILED")
            if r and not r.ok:
                status += f": {short(r.error, 200)}"
            artifacts = "\n".join(f"- {r.workdir_id or 'unknown-workdir'}/{p}" for p in r.outputs) if r else ""
            detail = f"\nOutputs:\n{artifacts or '- none'}"
            if r and r.missing_outputs:
                detail += f"\nMissing: {', '.join(r.missing_outputs)}"
            if r and r.revision_failed:
                detail += f"\nRevision failed; retained last successful result: {r.revision_failed}"
            if r and r.partial_results:
                detail += "\nFailed with partial results."
            if not r or not r.ok:
                detail += "\nCause chain: " + " <- ".join(causes(s["id"]))
            out.append(f"### {s['id']} · {s['agent_id']} ({status})\nInstruction: {s['instruction']}\n"
                       f"{clip(r.text if r else '', n)}{detail}")
        return "\n\n".join(out)

    # ---------- request entry point ----------
    async def run_request(self, rid: str, resume: bool = False) -> None:
        req = self.hub.requests[rid]
        text = req["text"] + "".join("\n\nPI clarification (questions and answer):\n" + qa_text(c)
                                     for c in req.get("clarifications") or [])
        self.cost[rid] = float(req.get("cost_usd") or 0)
        try:
            research_pilot = bool(self.hub.s.research.enabled)
            intake = (classify_intake(req["text"], req.get("work_kind", "auto"),
                                      scope_status=req.get("scope_status", "in_scope"))
                      if research_pilot else None)
            research_lane = bool(intake and intake.work_kind == "research")
            if intake:
                req["intake"] = intake.model_dump(mode="json")
                self.hub.save_request(rid)
            if req["mode"] == "direct":
                if research_lane:
                    req["outcome"] = "needs_research"
                    self._finish(rid, "Research work cannot use direct mode in the PR 1 pilot. "
                                 "Submit it as orchestrate or plan_only for a frozen, PI-approved plan.", {}, ok=False)
                    return
                res = await self.run_step(Task(agent_id=req["agent_id"], request_id=rid, prompt=text,
                                               budget_usd=req.get("budget_usd"),
                                               meta={"kind": "direct", "title": text[:100],
                                                     "project_dirs": req.get("project_dirs", [])}))
                self._finish(rid, res.text, {"direct": res.model_dump(mode="json")},
                             ok=res.ok and rid not in self.budget_denials)
                return

            all_agents = list(self.hub.agents.values())
            known = {a["id"] for a in all_agents}
            # The configured orchestration agents, not only the default ids, stay out of the worker roster.
            orchestration = set(ORCHESTRATION_ROLES) | {x for x in (self.cfg.cso_agent, self.cfg.chief_of_staff_agent,
                                                                   self.cfg.reviewer_agent) if x}
            roster = [a for a in all_agents if a["id"] not in orchestration]
            n = self.cfg.context_chars_per_step
            packs = configured_packs(self.hub.s) if research_lane else {}
            active_pack_hashes = pack_snapshot(packs)

            async def finish_research_plan(plan: dict[str, Any]) -> None:
                previous = (req.get("research_contract") or {}).get("approval")
                approval = refresh_plan_approval(plan, previous)
                req["research_contract"] = {
                    "schema_version": 1,
                    "work_kind": "research",
                    "execution_enabled": False,
                    "plan_sha256": approval.get("current_sha256") or approval.get("target_sha256"),
                    "pack_snapshot": active_pack_hashes,
                    "approval": approval,
                }
                self.hub.save_request(rid)
                if approval.get("status") != "approved":
                    plan_hash = req["research_contract"]["plan_sha256"]
                    summary = ("CP1 research plan approval: approve the frozen question, methods, completion/stop "
                               f"conditions, data boundary, and selected packs. plan_sha256={plan_hash}")
                    decision = await self.hub.request_approval(
                        kind="research_plan", request_id=rid, summary=summary[:700],
                        detail={"gate": "research_plan", "target_sha256": plan_hash,
                                "plan_canonical": canonical_plan_json(plan),
                                "protocol_revision": plan["protocol"]["revision"],
                                "packs": plan["protocol"]["packs"],
                                "scope_status": plan["intake"]["scope_status"]})
                    approval = freeze_plan(plan, decision)
                    approval.update(request_id=rid, protocol_revision=plan["protocol"]["revision"])
                    req["research_contract"]["approval"] = approval
                    self.hub.save_request(rid)
                approved = bool(approval.get("approved"))
                req["outcome"] = "plan_approved" if approved else "plan_rejected"
                report = ("Research plan frozen and approved. PR 1 pilot stops before employee dispatch."
                          if approved else "Research plan was not approved; no employee research step was dispatched.")
                self._finish(rid, report, {}, ok=approved)

            if resume and req.get("plan", {}).get("steps"):
                if research_lane:
                    validated = validate_research_plan(req["plan"], max_steps=self.cfg.max_steps,
                                                       active_packs=active_pack_hashes,
                                                       expected_intake=intake, pack_definitions=packs)
                    req["plan"] = validated.model_dump(mode="json")
                    await finish_research_plan(req["plan"])
                    return
                steps = req["plan"]["steps"]
                results: dict[str, TaskResult] = self.hub.result_map(rid)
                remaining = {s["id"] for s in steps} - set(req.get("results") or {})
                pending_revisions = req.get("pending_revisions") or {}
                for sid, entry in pending_revisions.items():
                    if sid in remaining and entry.get("previous_result"):
                        dict.__setitem__(results, sid, TaskResult.model_validate(entry["previous_result"]))
                resume_feedback = {sid: entry["feedback"] for sid, entry in pending_revisions.items()
                                   if sid in remaining and entry.get("feedback")}
            else:
                briefing = ""
                cos = self.cfg.chief_of_staff_agent
                if cos and cos in known:
                    b = await self.run_step(Task(agent_id=cos, request_id=rid, prompt=BRIEFING_PROMPT.format(request=text),
                                                 meta={"kind": "briefing", "request": text, "title": "CSO용 브리핑 준비"}))
                    if not b.ok:
                        self._finish(rid, f"브리핑 실패: {b.error}", {"briefing": b.model_dump(mode="json")}, ok=False)
                        return
                    if rid in self.budget_denials:
                        self._finish(rid, "브리핑 뒤 예산 승인 거부", {"briefing": b.model_dump(mode="json")}, ok=False)
                        return
                    briefing = b.text

                capabilities = "\n".join(
                    f"- {a['id']}: scheduler={a.get('scheduler', 'none')}, "
                    f"labhq_hpc={'yes' if a.get('hpc_tools') else 'no'}, "
                    f"other compute={', '.join(a.get('compute_backends') or ['local CLI'])}"
                    for a in roster)

                async def make_plan(plan_request: str) -> TaskResult:
                    continuation = self.hub.supports_resume(self.cfg.cso_agent)
                    if research_lane:
                        prompt = RESEARCH_PLAN_PROMPT.format(
                            request=plan_request, roster=format_roster(roster),
                            capabilities=capabilities or "No workers available",
                            briefing=clip(briefing, 4000) or "(none)", max_steps=self.cfg.max_steps,
                            intake=json.dumps(intake.model_dump(mode="json"), ensure_ascii=False, sort_keys=True),
                            packs=render_pack_catalog(packs), question_rule=QUESTION_RULE)
                        schema = RESEARCH_PLAN_SCHEMA
                    else:
                        prompt = PLAN_PROMPT.format(request=plan_request, roster=format_roster(roster),
                                                    capabilities=capabilities or "No workers available",
                                                    briefing=clip(briefing, 4000) or "(none)",
                                                    max_steps=self.cfg.max_steps, question_rule=QUESTION_RULE)
                        schema = PLAN_SCHEMA
                    planned = await self.run_step(Task(
                        agent_id=self.cfg.cso_agent, request_id=rid, output_schema=schema,
                        resume_session_id=req.get("cso_session_id") if continuation else None,
                        prompt=prompt,
                        meta={"kind": "plan", "roster": roster, "request": plan_request,
                              "title": "업무 분해·배정 계획 수립",
                              **({"workdir": req["cso_workdir"]} if continuation and req.get("cso_workdir") else {})}))
                    if planned.session_id:
                        req["cso_session_id"] = planned.session_id
                        req["cso_workdir"] = planned.workdir
                        self.hub.save_request(rid)
                    return planned

                plan_res = await make_plan(text)
                if not plan_res.ok:
                    self._finish(rid, f"계획 실패: {plan_res.error}", {"plan": plan_res.model_dump(mode="json")}, ok=False)
                    return
                if rid in self.budget_denials:
                    self._finish(rid, "계획 뒤 예산 승인 거부", {"plan": plan_res.model_dump(mode="json")}, ok=False)
                    return
                plan = plan_res.structured if isinstance(plan_res.structured, dict) else extract_json(plan_res.text) or {}
                details = normalize_questions(plan.get("clarifying_questions"))
                questions = [q["question"] for q in details]
                if questions:
                    req["pending_questions"] = questions
                    if has_structure(details):
                        req["pending_question_details"] = details
                    self.hub.save_request(rid)
                    await self._emit(rid, "request.questions", {"questions": questions, "details": details})
                    if self.cfg.wait_for_clarification:
                        # The card shows options as buttons and returns the composed answer as the note (#34).
                        dec = await self.hub.request_approval(kind="clarify", request_id=rid,
                                                              summary=questions_summary(details),
                                                              detail={"questions": details})
                        if not dec.get("approved") or not str(dec.get("note") or "").strip():
                            self._finish(rid, "PI clarification denied or unanswered.", {}, ok=False)
                            return
                        entry = {"questions": questions, "answer": str(dec["note"]).strip()}
                        if has_structure(details):
                            entry["question_details"] = details
                        req.setdefault("clarifications", []).append(entry)
                        req["pending_questions"] = []
                        req.pop("pending_question_details", None)
                        self.hub.save_request(rid)  # a restart must not lose the PI's answer
                        text += "\n\nPI clarification (questions and answer):\n" + qa_text(entry)
                        plan_res = await make_plan(text)
                        if not plan_res.ok:
                            self._finish(rid, f"Re-plan failed: {plan_res.error}", {}, ok=False)
                            return
                        plan = plan_res.structured if isinstance(plan_res.structured, dict) else extract_json(plan_res.text) or {}
                        still = normalize_questions(plan.get("clarifying_questions"))
                        if still:
                            req["pending_questions"] = [q["question"] for q in still]
                            self._finish(rid, "Re-plan still requires PI clarification.", {}, ok=False)
                            return
                if research_lane:
                    invalid = ""
                    for attempt in (1, 2):
                        try:
                            validated = validate_research_plan(plan, max_steps=self.cfg.max_steps,
                                                               active_packs=active_pack_hashes,
                                                               expected_intake=intake, pack_definitions=packs)
                            bad_agents = [step.agent_id for step in validated.steps
                                          if step.agent_id not in known or step.agent_id in orchestration]
                            if bad_agents:
                                raise ValueError(f"research plan uses unavailable or orchestration agents: {bad_agents}")
                            break
                        except (ValueError, TypeError) as error:
                            invalid = str(error)
                            if attempt == 2:
                                raise ValueError(f"research plan invalid after correction: {invalid}") from error
                            correction = (text + "\n\nThe previous research PLAN failed validation: " + invalid +
                                          "\nReturn a complete corrected PLAN. Do not remove steps by truncation.")
                            plan_res = await make_plan(correction)
                            if not plan_res.ok:
                                self._finish(rid, f"Research re-plan failed: {plan_res.error}", {}, ok=False)
                                return
                            if rid in self.budget_denials:
                                self._finish(rid, "교정 계획 뒤 예산 승인 거부",
                                             {"plan": plan_res.model_dump(mode="json")}, ok=False)
                                return
                            plan = (plan_res.structured if isinstance(plan_res.structured, dict)
                                    else extract_json(plan_res.text) or {})
                    req["plan"] = validated.model_dump(mode="json")
                    warnings: list[str] = []
                    steps = req["plan"]["steps"]
                else:
                    steps, warnings = validate_steps(plan.get("steps") or [], known, self.cfg.max_steps, orchestration)
                    req["plan"] = {**plan, "steps": steps, "warnings": warnings}
                await self._emit(rid, "request.plan", req["plan"])
                for rec in plan.get("recruit") or []:
                    if rec.get("repo") or rec.get("paper"):
                        await self._emit(rid, "recruit.suggested", rec)  # UI shows a 채용 제안 card → POST /api/recruit
                if not steps:
                    self._finish(rid, plan_res.text or "CSO returned no steps.", {}, ok=False)
                    return
                if research_lane:
                    await finish_research_plan(req["plan"])
                    return
                if req["mode"] == "plan_only":
                    req["outcome"] = "plan_only"
                    self._finish(rid, plan_res.text or "Plan completed.", {}, ok=True)
                    return
                results = self.hub.result_map(rid)
                remaining = {s["id"] for s in steps} - set(results)
                resume_feedback = {}

            if remaining:
                await self.run_dag(rid, text, steps, results, only=remaining,
                                   feedback=resume_feedback or None)
            def serialized_results() -> dict[str, dict]:
                return {k: {**v.model_dump(mode="json"),
                            "status": "skipped" if (v.error or "").startswith("skipped:") else
                                      ("done" if v.ok else "incomplete" if v.missing_outputs else "failed"),
                            "attempts": self.attempts.get(rid, {}).get(k, 0)} for k, v in results.items()}

            if rid in self.budget_denials or any(not r.ok for r in results.values()):
                self._finish(rid, self.format_results(steps, results, n), serialized_results(), ok=False)
                return

            progress = req.get("review_progress") or {}
            if progress.get("phase") == "revision":
                progress.update(phase="review", last_completed_revision=progress["next_revision"])
                req["review_progress"] = progress
                self.hub.save_request(rid)
            review: dict = progress.get("review") or {}
            reviewer = self.cfg.reviewer_agent
            start_rev = (self.cfg.max_revisions + 1 if progress.get("phase") in {"synthesis", "unresolved"}
                         else int(progress.get("next_revision") or 0))
            for rev in range(start_rev, self.cfg.max_revisions + 1):
                if not reviewer or reviewer not in known:
                    break
                prompt = REVIEW_PROMPT.format(request=text, results=self.format_results(steps, results, n))
                review = {}
                for parse_attempt in (1, 2):
                    r = await self.run_step(Task(
                        agent_id=reviewer, request_id=rid, output_schema=REVIEW_SCHEMA,
                        prompt=prompt if parse_attempt == 1 else prompt +
                        '\n\nReturn ONLY a JSON object with verdict exactly "accept" or "revise", scores, and issues. '
                        'Do not omit verdict or add prose.',
                        meta={"kind": "review", "revision": rev, "parse_attempt": parse_attempt,
                              "request": text, "title": f"과학 리뷰 #{rev}"}))
                    if rid in self.budget_denials:
                        self._finish(rid, self.format_results(steps, results, n), serialized_results(), ok=False,
                                     review={"status": "budget_denied"})
                        return
                    parsed = r.structured if valid_review(r.structured) else None
                    if parsed is None:
                        parsed = extract_json(r.text)
                    if r.ok and valid_review(parsed):
                        review = parsed
                        break
                if not review:
                    review = {"status": "review_unparsed", "reason": r.error or "missing or invalid verdict"}
                    await self._emit(rid, "request.review", {"revision": rev, **review})
                    self._finish(rid, self.format_results(steps, results, n) +
                                 f"\n\nReview: review_unparsed ({review['reason']})",
                                 serialized_results(), ok=False, review=review)
                    return
                await self._emit(rid, "request.review", {"revision": rev, **review})
                progress = {"phase": "review", "next_revision": rev + 1, "review": review,
                            "last_completed_review": rev,
                            "last_completed_revision": progress.get("last_completed_revision", 0)}
                if review.get("verdict") != "revise":
                    progress["phase"] = "synthesis"
                    req["review_progress"] = progress
                    self.hub.save_request(rid)
                    break
                if rev >= self.cfg.max_revisions:
                    progress["phase"] = "unresolved"
                    req["review_progress"] = progress
                    self.hub.save_request(rid)
                    break
                feedback: dict[str, str] = {}
                for issue in review.get("issues") or []:
                    if issue.get("step_id") in {s["id"] for s in steps}:
                        feedback.setdefault(issue["step_id"], "")
                        feedback[issue["step_id"]] += f"- {issue.get('problem')}: {issue.get('request')}\n"
                if not feedback:
                    progress["phase"] = "unresolved"
                    req["review_progress"] = progress
                    self.hub.save_request(rid)
                    break
                progress["phase"] = "revision"
                req["review_progress"] = progress
                pending = req.setdefault("pending_revisions", {})
                for sid, note in feedback.items():
                    pending[sid] = {"revision": rev + 1, "feedback": note,
                                    "previous_result": results[sid].model_dump(mode="json")}
                    req.setdefault("results", {}).pop(sid, None)
                self.hub.save_request(rid)
                await self.run_dag(rid, text, steps, results, only=set(feedback), feedback=feedback)
                if rid in self.budget_denials or any(not r.ok for r in results.values()):
                    self._finish(rid, self.format_results(steps, results, n), serialized_results(), ok=False,
                                 review=review)
                    return
                progress.update(phase="review", last_completed_revision=rev + 1)
                self.hub.save_request(rid)

            if review.get("verdict") == "revise":
                self._finish(rid, self.format_results(steps, results, n) + "\n\nReview: revisions unresolved.",
                             serialized_results(), ok=False, review=review)
                return

            final = await self.run_step(Task(
                agent_id=self.cfg.cso_agent, request_id=rid,
                resume_session_id=req.get("cso_session_id") if self.hub.supports_resume(self.cfg.cso_agent) else None,
                prompt=SYNTH_PROMPT.format(request=text, results=self.format_results(steps, results, n),
                                           review=short(review, 3000)),
                meta={"kind": "synthesis", "request": text, "title": "최종 보고서 작성",
                      **({"workdir": req["cso_workdir"]} if self.hub.supports_resume(self.cfg.cso_agent)
                         and req.get("cso_workdir") else {})}))
            self._finish(rid, final.text if final.ok else self.format_results(steps, results, n) +
                         f"\n\nSynthesis failed: {final.error}", serialized_results(),
                         ok=final.ok and rid not in self.budget_denials, review=review)
        except Exception as e:
            req.update(status="failed", error=f"{type(e).__name__}: {e}", finished_at=time.time())
            if req.get("plan", {}).get("steps"):
                saved = {k: TaskResult.model_validate(v) for k, v in (req.get("results") or {}).items()}
                req["report"] = self.format_results(req["plan"]["steps"], saved,
                                                    self.cfg.context_chars_per_step)
            else:
                req["report"] = req["error"]
            if req.get("pending_questions"):
                req["report"] += "\n\nPending PI decisions/questions:\n" + "\n".join(
                    f"- {question}" for question in req["pending_questions"])
            # The preserved partial report goes with the event so a connected (or reconnecting) office shows it.
            failed = {"error": req["error"], "report": clip(req.get("report") or "", 20000),
                      "cost_usd": float(req.get("cost_usd") or 0),
                      "cost_known": req.get("cost_known", True)}
            if hasattr(self.hub, "commit_terminal"):
                self.hub.commit_terminal(rid, "request.failed", failed)
            else:
                await self._emit(rid, "request.failed", failed)

    def _finish(self, rid: str, report: str, results: dict, ok: bool, review: dict | None = None) -> None:
        req = self.hub.requests[rid]
        if req.get("plan", {}).get("steps") and results:
            audit = []
            for step in req["plan"]["steps"]:
                entry = results.get(step["id"], {})
                outputs = entry.get("outputs") or []
                paths = ", ".join(f"{entry.get('workdir_id') or 'unknown-workdir'}/{p}" for p in outputs)
                line = f"- {step['id']}: {entry.get('status', 'not run')}; outputs: {paths or 'none'}"
                if entry.get("missing_outputs"):
                    line += f"; missing: {', '.join(entry['missing_outputs'])}"
                if entry.get("revision_failed"):
                    line += f"; revision failed: {entry['revision_failed']}"
                if entry.get("partial_results"):
                    line += "; failed with partial results"
                if entry.get("error"):
                    line += f"; cause: {entry.get('error_kind') or 'terminal'}: {entry['error']}"
                audit.append(line)
            report += "\n\nStep status and output paths:\n" + "\n".join(audit)
        if req.get("pending_questions"):
            report += "\n\nPending PI decisions/questions:\n" + "\n".join(
                f"- {question}" for question in req["pending_questions"])
        if req.get("cost_known") is False:
            known = float(req.get("cost_usd") or 0)
            report += f"\n\n비용: {f'${known:.2f} + ' if known else ''}비용 미집계"
        for outcome in self.budget_outcomes.get(rid, []):
            decision = "approved" if outcome["approved"] else "denied"
            report += (f"\n\nBudget: ${outcome['spent_usd']:.2f} > "
                       f"${outcome['limit_usd']:.2f}; {decision}.")
        req.update(status="done" if ok else "failed", report=report, results=results, review=review,
                   cost_usd=self.cost.get(rid, 0.0), finished_at=time.time())
        data = {"ok": ok, "report": clip(report, 20000), "cost_usd": req["cost_usd"],
                "cost_known": req.get("cost_known", True), "usage": req.get("usage", {}),
                "usage_known": req.get("usage_known", True)}
        if hasattr(self.hub, "commit_terminal"):
            self.hub.commit_terminal(rid, "request.completed", data)
        else:  # Lightweight orchestration test doubles do not persist state.
            self.hub.save_request(rid)
            asyncio.get_running_loop().create_task(self._emit(rid, "request.completed", data))
