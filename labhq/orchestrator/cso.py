"""Virtual-Biotech-style orchestration, adapted to a one-PI bioinformatics lab.

briefing (chief of staff) → CSO plan (JSON DAG + optional contract hires) → steps run in parallel
where dependencies allow (hibernate on HPC jobs, resume when they finish) → scientific reviewer
(3 criteria) → targeted revisions → CSO synthesis report.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any

from ..models import Task, TaskResult
from ..util import clip, extract_json, short

if TYPE_CHECKING:
    from ..gateway.server import Hub

PLAN_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "clarifying_questions": {"type": "array", "items": {"type": "string"}},
        "steps": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "properties": {"id": {"type": "string"}, "agent_id": {"type": "string"},
                           "instruction": {"type": "string"},
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
                   "properties": {"addresses_question": {"type": "integer"}, "evidence": {"type": "integer"},
                                  "thoroughness": {"type": "integer"}},
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

Chief of staff briefing:
{briefing}

Rules:
- At most {max_steps} steps. Express order with depends_on; independent steps run in parallel.
- Heavy compute runs as HPC jobs (agents with HPC tools). Put a QC step after any data generation.
- If no roster member covers a required method, add a contract hire to `recruit` (paper + code repo +
  focus) and plan the step for whoever is closest; the PI decides whether to hire.
- Ask clarifying_questions only if the ambiguity would change the plan.

PI's request: {request}"""

STEP_PROMPT = """Overall request (context only): {request}

Your step ({step_id}): {instruction}

Teammates' upstream results are in the context section. Deliver: what you did, key results with file
paths, caveats and open questions."""

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


def format_roster(agents: list[dict]) -> str:
    lines = []
    for a in agents:
        tag = " [파견직]" if a.get("employment") == "contract" else ""
        tools = f" · tools: {', '.join(a['mcp'])}" if a.get("mcp") else ""
        lines.append(f"- {a['id']}{tag}: {a['name']} — {a['role']} ({a['engine']}/{a.get('model') or 'default'}){tools}")
    return "\n".join(lines)


def validate_steps(raw: list[dict], known: set[str], max_steps: int) -> tuple[list[dict], list[str]]:
    warnings, steps, seen = [], [], set()
    for i, s in enumerate(raw[:max_steps]):
        sid = str(s.get("id") or f"s{i + 1}")
        if sid in seen:
            sid = f"{sid}_{i}"
        seen.add(sid)
        steps.append({"id": sid, "agent_id": s.get("agent_id", ""), "instruction": s.get("instruction", ""),
                      "depends_on": [str(d) for d in s.get("depends_on") or []]})
    ids = {s["id"] for s in steps}
    for s in steps:
        s["depends_on"] = [d for d in s["depends_on"] if d in ids and d != s["id"]]
        if s["agent_id"] not in known:
            warnings.append(f"step {s['id']}: unknown agent {s['agent_id']!r}")
    # cycle check (Kahn); on a cycle fall back to the listed order
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
        warnings.append("plan had a dependency cycle; running steps in listed order")
        for i, s in enumerate(steps):
            s["depends_on"] = [steps[i - 1]["id"]] if i else []
    return steps, warnings


class BudgetExceeded(RuntimeError):
    pass


class Orchestrator:
    def __init__(self, hub: "Hub"):
        self.hub = hub
        self.cfg = hub.s.orchestrator
        self.cost: dict[str, float] = {}

    async def _emit(self, rid: str, typ: str, data: dict) -> None:
        await self.hub.publish({"type": typ, "ts": time.time(), "request_id": rid, "data": data})

    # ---------- one agent step, including HPC hibernate/wake cycles ----------
    async def run_step(self, task: Task) -> TaskResult:
        rid = task.request_id or ""
        await self._check_budget(rid)
        res = await self.hub.dispatch(task)
        self.cost[rid] = self.cost.get(rid, 0.0) + (res.cost_usd or 0.0)
        cycles = 0
        while res.pending_jobs and cycles < self.cfg.max_wake_cycles:
            cycles += 1
            info = await self.hub.wait_jobs(res.task_id)
            jobs = "\n".join(f"- {j['job_id']} ({j.get('name') or ''}): {j['state']} exit={j.get('exit_status')}"
                             for j in info.get("jobs", []))
            meta = {**task.meta, "kind": task.meta.get("kind", "step"), "parent_task": res.task_id, "workdir": res.workdir}
            meta["title"] = "HPC 결과 확인 후 이어서 작업"
            wake = Task(agent_id=task.agent_id, request_id=task.request_id, output_schema=task.output_schema,
                        prompt=WAKE_PROMPT.format(jobs=jobs, workdir=res.workdir), meta=meta,
                        resume_session_id=res.session_id if self.hub.supports_resume(task.agent_id) else None,
                        context="" if self.hub.supports_resume(task.agent_id) else clip(res.text, self.cfg.context_chars_per_step))
            await self._check_budget(rid)
            res = await self.hub.dispatch(wake)
            self.cost[rid] = self.cost.get(rid, 0.0) + (res.cost_usd or 0.0)
        return res

    async def _check_budget(self, rid: str) -> None:
        limit = self.hub.requests.get(rid, {}).get("budget_usd") or self.hub.s.policy.budget.per_request_usd
        spent = self.cost.get(rid, 0.0)
        if limit and spent > limit:
            dec = await self.hub.request_approval(kind="budget", request_id=rid,
                                                  summary=f"예산 초과: ${spent:.2f} / ${limit:.2f} — 계속 진행할까요?")
            if not dec.get("approved"):
                raise BudgetExceeded(f"budget exceeded (${spent:.2f} > ${limit:.2f})")
            self.hub.requests[rid]["budget_usd"] = limit * 2

    # ---------- DAG ----------
    async def run_dag(self, rid: str, request: str, steps: list[dict], results: dict[str, TaskResult],
                      only: set[str] | None = None, feedback: dict[str, str] | None = None) -> None:
        by_id = {s["id"]: s for s in steps}
        todo = {s["id"] for s in steps if only is None or s["id"] in only}
        running: dict[str, asyncio.Task] = {}
        sem = asyncio.Semaphore(self.cfg.max_parallel_steps)

        def upstream(step: dict) -> str:
            parts = []
            for d in step["depends_on"]:
                r = results.get(d)
                if r:
                    head = f"## {d} · {by_id[d]['agent_id']}" + ("" if r.ok else f" (FAILED: {short(r.error, 200)})")
                    parts.append(f"{head}\n{clip(r.text, self.cfg.context_chars_per_step)}")
            return "\n\n".join(parts)

        async def run_one(step: dict) -> TaskResult:
            prompt = STEP_PROMPT.format(request=request, step_id=step["id"], instruction=step["instruction"])
            ctx = upstream(step)
            if feedback and step["id"] in feedback:
                prev = results.get(step["id"])
                prompt += f"\n\n[Scientific reviewer feedback — revise your step]\n{feedback[step['id']]}"
                ctx += f"\n\n## Your previous result\n{clip(prev.text if prev else '', self.cfg.context_chars_per_step)}"
            task = Task(agent_id=step["agent_id"], request_id=rid, prompt=prompt, context=ctx,
                        meta={"kind": "step", "step_id": step["id"], "request": request,
                              "title": f"{step['id']}: {step['instruction'][:100]}" + (" (리뷰 반영 수정)" if feedback else ""),
                              "project_dirs": self.hub.requests.get(rid, {}).get("project_dirs", [])})
            async with sem:
                return await self.run_step(task)

        while todo or running:
            # ready = no dependency still pending in this run (deps outside `only` already have results)
            ready = [sid for sid in todo if not any(d in todo or d in running for d in by_id[sid]["depends_on"])]
            for sid in ready:
                todo.discard(sid)
                running[sid] = asyncio.create_task(run_one(by_id[sid]))
            if not running:
                for sid in todo:
                    results[sid] = TaskResult(task_id="", agent_id=by_id[sid]["agent_id"], ok=False, error="unmet dependencies")
                break
            done, _ = await asyncio.wait(running.values(), return_when=asyncio.FIRST_COMPLETED)
            for sid, t in list(running.items()):
                if t in done:
                    running.pop(sid)
                    try:
                        results[sid] = t.result()
                    except Exception as e:
                        results[sid] = TaskResult(task_id="", agent_id=by_id[sid]["agent_id"], ok=False, error=str(e))
                    await self._emit(rid, "request.step_done", {"step_id": sid, "ok": results[sid].ok,
                                                                "agent_id": by_id[sid]["agent_id"]})

    @staticmethod
    def format_results(steps: list[dict], results: dict[str, TaskResult], n: int) -> str:
        out = []
        for s in steps:
            r = results.get(s["id"])
            status = "ok" if r and r.ok else f"FAILED: {short(r.error if r else 'not run', 200)}"
            out.append(f"### {s['id']} · {s['agent_id']} ({status})\nInstruction: {s['instruction']}\n"
                       f"{clip(r.text if r else '', n)}")
        return "\n\n".join(out)

    # ---------- request entry point ----------
    async def run_request(self, rid: str) -> None:
        req = self.hub.requests[rid]
        text = req["text"]
        try:
            if req["mode"] == "direct":
                res = await self.run_step(Task(agent_id=req["agent_id"], request_id=rid, prompt=text,
                                               budget_usd=req.get("budget_usd"),
                                               meta={"kind": "direct", "title": text[:100],
                                                     "project_dirs": req.get("project_dirs", [])}))
                self._finish(rid, res.text, {"direct": res.model_dump(mode="json")}, ok=res.ok)
                return

            roster = list(self.hub.agents.values())
            known = {a["id"] for a in roster}
            n = self.cfg.context_chars_per_step
            briefing = ""
            cos = self.cfg.chief_of_staff_agent
            if cos and cos in known:
                b = await self.run_step(Task(agent_id=cos, request_id=rid, prompt=BRIEFING_PROMPT.format(request=text),
                                             meta={"kind": "briefing", "request": text, "title": "CSO용 브리핑 준비"}))
                briefing = b.text

            plan_res = await self.run_step(Task(
                agent_id=self.cfg.cso_agent, request_id=rid, output_schema=PLAN_SCHEMA,
                prompt=PLAN_PROMPT.format(request=text, roster=format_roster(roster), briefing=clip(briefing, 4000) or "(none)",
                                          max_steps=self.cfg.max_steps),
                meta={"kind": "plan", "roster": roster, "request": text, "title": "업무 분해·배정 계획 수립"}))
            plan = plan_res.structured if isinstance(plan_res.structured, dict) else extract_json(plan_res.text) or {}
            steps, warnings = validate_steps(plan.get("steps") or [], known, self.cfg.max_steps)
            req["plan"] = {**plan, "steps": steps, "warnings": warnings}
            await self._emit(rid, "request.plan", req["plan"])
            if plan.get("clarifying_questions"):
                await self._emit(rid, "request.questions", {"questions": plan["clarifying_questions"]})
            for rec in plan.get("recruit") or []:
                if rec.get("repo") or rec.get("paper"):
                    await self._emit(rid, "recruit.suggested", rec)  # UI shows a 채용 제안 card → POST /api/recruit
            if not steps:
                self._finish(rid, plan_res.text or "CSO returned no steps.", {}, ok=False)
                return

            results: dict[str, TaskResult] = {}
            await self.run_dag(rid, text, steps, results)

            review: dict = {}
            reviewer = self.cfg.reviewer_agent
            for rev in range(self.cfg.max_revisions + 1):
                if not reviewer or reviewer not in known:
                    break
                r = await self.run_step(Task(
                    agent_id=reviewer, request_id=rid, output_schema=REVIEW_SCHEMA,
                    prompt=REVIEW_PROMPT.format(request=text, results=self.format_results(steps, results, n)),
                    meta={"kind": "review", "revision": rev, "request": text, "title": f"과학 리뷰 #{rev}"}))
                review = r.structured if isinstance(r.structured, dict) else extract_json(r.text) or {"verdict": "accept"}
                await self._emit(rid, "request.review", {"revision": rev, **review})
                if review.get("verdict") != "revise" or rev >= self.cfg.max_revisions:
                    break
                feedback: dict[str, str] = {}
                for issue in review.get("issues") or []:
                    if issue.get("step_id") in {s["id"] for s in steps}:
                        feedback.setdefault(issue["step_id"], "")
                        feedback[issue["step_id"]] += f"- {issue.get('problem')}: {issue.get('request')}\n"
                if not feedback:
                    break
                await self.run_dag(rid, text, steps, results, only=set(feedback), feedback=feedback)

            final = await self.run_step(Task(
                agent_id=self.cfg.cso_agent, request_id=rid,
                prompt=SYNTH_PROMPT.format(request=text, results=self.format_results(steps, results, n),
                                           review=short(review, 3000)),
                meta={"kind": "synthesis", "request": text, "title": "최종 보고서 작성"}))
            self._finish(rid, final.text, {k: v.model_dump(mode="json") for k, v in results.items()},
                         ok=final.ok, review=review)
        except Exception as e:
            req.update(status="failed", error=f"{type(e).__name__}: {e}", finished_at=time.time())
            await self._emit(rid, "request.failed", {"error": req["error"]})

    def _finish(self, rid: str, report: str, results: dict, ok: bool, review: dict | None = None) -> None:
        req = self.hub.requests[rid]
        req.update(status="done" if ok else "failed", report=report, results=results, review=review,
                   cost_usd=round(self.cost.get(rid, 0.0), 4), finished_at=time.time())
        asyncio.get_running_loop().create_task(self._emit(rid, "request.completed", {
            "ok": ok, "report": clip(report, 20000), "cost_usd": req["cost_usd"]}))
