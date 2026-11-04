"""Deterministic stand-in for a real CLI agent. Exercises every platform path without API keys:
planning (DAG), reviewer revise loop, approvals, HPC hibernate/wake, and contract recruitment.

Tokens in the request text steer it: [needs-approval], [hpc], [revise], [recruit], [question], [block],
[revision-fail], [max-turns], [artifact]. In a research plan, [multistep] gives two dependent steps and
[branched] two branches plus a synthesis step; [step-fail] and [branch-fail] fail step s2 of those plans and
[all-branch-fail] fails both branches (#1207). In a team (non-research) plan, [step-fail] fails the bioinfo-agent step.
Without a token the plan has one step.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re

import httpx

from ..models import TaskResult
from .base import AgentAdapter, RunContext, RunState

def _is_schema(task, *fields: str) -> bool:
    """Whether the task's output schema has all of `fields`: how the mock tells a research plan or step from a generic one."""
    properties = (task.output_schema or {}).get("properties") or {}
    return all(name in properties for name in fields)


def _research_plan(t, a) -> dict:
    """A valid research-contract plan (#1207): one step on a roster worker, no domain pack (topics left empty)."""
    roster = [r["id"] for r in t.meta.get("roster", [])]
    worker = next((name for name in ("analyst", "biologist", "data_steward") if name in roster),
                  next((name for name in sorted(roster) if name != a.id), a.id))
    marker = "(copy it into `intake`):"
    intake = {}
    if marker in t.prompt:
        try:
            intake = json.loads(t.prompt.split(marker, 1)[1].strip().split("\n\n", 1)[0])
        except ValueError:
            intake = {}
    asked = (t.meta.get("request") or t.prompt.rsplit("PI's request:", 1)[-1]).strip().splitlines()[0][:300] or "mock request"
    return {
        "schema_version": 2, "topics": [], "checklist": {}, "suggested_next": [], "intake": intake,
        "brief": {"question": asked, "purpose": "Exercise the research lane with a stand-in agent",
                  "subject": "mock public data", "scope": "mock data only", "deliverables": ["one result table"],
                  "completion_conditions": ["the result table is written"], "study_type": "technical",
                  "primary_hypothesis": "", "null_or_alternatives": [], "distinguishing_observations": []},
        "protocol": {"revision": 1, "analysis_unit": "mock row", "selection_criteria": ["every mock row"],
                     "exclusion_criteria": ["none"], "comparators": ["none"], "primary_metrics": ["row count"],
                     "validation_methods": ["file written"], "resource_limits": ["mock engine, no cost"],
                     "stop_conditions": ["file missing"], "approval_conditions": ["CP1 before execution"],
                     "not_applicable": {}, "data_boundaries": ["mock data only"],
                     "statistics": {"applicable": False, "reason": "a stand-in run draws no inference"},
                     "packs": []},
        "pack_values": {}, "clarifying_questions": [],
        **_research_shape(worker, asked + " " + t.prompt),
        "recruit": [], "notes": "mock research plan",
    }


def _research_step(sid: str, worker: str, depends_on: list[str], branch: str | None = None) -> dict:
    n = sid[1:]
    step = {"id": sid, "agent_id": worker, "instruction": f"Write outputs/result{n}.tsv with the mock row count.",
            "phase": "analysis", "claim_ids": [f"c{n}"], "input_refs": ["mock-input"],
            "outputs": [f"outputs/result{n}.tsv"], "checks": ["file written"],
            "evidence_slots": [{"id": f"e{n}", "required": True, "description": f"mock result table {n}"}],
            "depends_on": depends_on}
    if branch:
        step["branch"] = branch
    return step


def _research_shape(worker: str, request: str) -> dict:
    """Steps (and branches) of the mock research plan: one step, or `[multistep]` (s2 reads s1), or `[branched]`
    (two independent branches and a synthesis step that reads both). `[step-fail]` makes s2 of a multistep plan
    fail, `[branch-fail]` makes the second branch of a branched plan fail (each implies its plan shape)."""
    if "[branched]" in request or "[branch-fail]" in request or "[all-branch-fail]" in request:
        shape = {"branches": [{"id": "b1", "question": "First mock question", "evidence_type": "mock_table",
                               "data_boundary": "mock data only"},
                              {"id": "b2", "question": "Second mock question", "evidence_type": "mock_table",
                               "data_boundary": "mock data only"}],
                 "steps": [_research_step("s1", worker, [], "b1"), _research_step("s2", worker, [], "b2"),
                           _research_step("s3", worker, ["s1", "s2"], "synthesis")]}
        failing = "[branch-fail]" in request or "[all-branch-fail]" in request
    elif "[multistep]" in request or "[step-fail]" in request:
        shape = {"steps": [_research_step("s1", worker, []), _research_step("s2", worker, ["s1"])]}
        failing = "[step-fail]" in request
    else:
        return {"steps": [_research_step("s1", worker, [])]}
    if failing:
        failed = ("s1", "s2") if "[all-branch-fail]" in request else ("s2",)
        for step in shape["steps"]:
            if step["id"] in failed:
                step["instruction"] += " [step-fail]"
    return shape


_RESULT_COPY = re.compile(r"plan_sha256=([0-9a-f]{64}), step_id=(\S+?), claim_ids=(\[.*?\]), and fill evidence_slots="
                          r"(\[.*?\])\. ", re.S)


def _research_result(t) -> dict:
    """The result ledger a research step returns: the plan hash, step and slots are copied from the prompt it was given."""
    found = _RESULT_COPY.search(t.prompt)
    plan_hash, step_id, claim_ids, slots = (found.group(1), found.group(2), json.loads(found.group(3)),
                                            json.loads(found.group(4))) if found else ("0" * 64, "s1", ["c1"], [])
    outputs = t.meta.get("required_outputs") or t.meta.get("outputs") or ["outputs/result.tsv"]
    return {
        "schema_version": 2, "plan_sha256": plan_hash, "step_id": step_id,
        "claims": [{"id": cid, "revision": 1, "statement": "The mock result table was written.",
                    "scope": "the mock input", "kind": "finding", "status": "supported", "importance": "major",
                    "status_reason": "the file exists", "limitations": ["a stand-in agent"]} for cid in claim_ids],
        "evidence": [{"id": "ev1", "kind": "experimental", "observation": "the mock result table",
                      "status": "observed", "source": {"artifact_id": "a1", "locator": "row 1"},
                      "directness": "direct", "source_level": "primary", "independence_group": "mock",
                      "assessment_reason": "written by the planned step",
                      "slots": [slot["id"] for slot in slots if isinstance(slot, dict) and "id" in slot]}],
        "links": [{"claim_id": cid, "claim_revision": 1, "evidence_id": "ev1", "relation": "supports",
                   "rationale": "the table is the planned result"} for cid in claim_ids],
        "artifact_refs": [{"artifact_id": "a1", "path": outputs[0]}],
        "not_established": [], "failures": [], "method_changes": [],
    }


class MockAdapter(AgentAdapter):
    engine = "mock"
    enforces_read_only = True  # runs no commands and writes only labhq's own result files

    def build_command(self, ctx: RunContext) -> list[str]:  # not used
        return []

    async def handle_line(self, line: str, st: RunState, ctx: RunContext) -> None:  # not used
        return None

    async def _broker(self, ctx: RunContext, path: str, payload: dict) -> dict:
        async with httpx.AsyncClient(timeout=self.settings.policy.approvals.timeout_s + 30) as c:
            r = await c.post(f"{ctx.env['LABHQ_BROKER_URL']}{path}", json=payload,
                             headers={"X-Labhq-Token": ctx.env["LABHQ_BROKER_TOKEN"]})
            r.raise_for_status()
            return r.json()

    async def run(self, ctx: RunContext) -> TaskResult:
        t, a = ctx.task, ctx.agent
        kind = t.meta.get("kind", "step")
        request = t.meta.get("request", "") + " " + t.prompt
        if ctx.before_spawn:  # the runner takes its output baseline here; without it no output of a mock step is hashed
            refused = ctx.before_spawn()
            if refused:  # a read-only baseline that cannot be taken fails the step before it runs
                return TaskResult(task_id=t.id, agent_id=a.id, ok=False, error=refused)
        await ctx.emit("agent.status", {"state": "working", "model": "mock"})
        await asyncio.sleep(self.settings.runner.mock_delay_s)
        structured = None
        text = f"[mock:{a.id}] {kind} 완료 — {t.prompt.splitlines()[0][:80]}"
        anchors = re.findall(r"\[\[claim:[^\]\s]+\]\]", t.prompt.split("Citable claims:", 1)[-1].split("Claims that cannot", 1)[0]) \
            if kind == "synthesis" and "Citable claims:" in t.prompt else []
        if anchors:  # the research report: every conclusion carries the anchor of the claim it rests on
            anchors = list(dict.fromkeys(anchors))  # one sentence per claim, so no step or branch is left out
            text = ("## 요약\n\n모의 결과 표가 작성되었습니다 " + anchors[0] + ".\n\n## 결론\n\n"
                    + "".join(f"모의 단계의 결과 표가 작성되었습니다 {anchor}.\n\n" for anchor in anchors).rstrip("\n") + "\n")

        if kind == "plan" and _is_schema(t, "pack_values", "protocol"):
            structured = _research_plan(t, a)
        elif kind == "step" and _is_schema(t, "plan_sha256", "claims"):
            structured = _research_result(t)
            for name in t.meta.get("required_outputs") or t.meta.get("outputs") or []:
                target = ctx.workdir / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("row\tcount\nmock\t1\n", encoding="utf-8")
        elif kind == "plan":
            roster = {r["id"] for r in t.meta.get("roster", [])}
            # biologist ∥ data_steward → bioinfo-agent (routine preprocessing) → analyst (HPC) → qc_reviewer
            layout = [("biologist", []), ("data_steward", []), ("bioinfo-agent", ["data_steward"]),
                      ("analyst", ["biologist", "bioinfo-agent", "data_steward"]), ("qc_reviewer", ["analyst"])]
            layout = [(aid, deps) for aid, deps in layout if aid in roster] or \
                     [(aid, []) for aid in sorted(roster - {a.id})[:2]]
            sid = {aid: f"s{i + 1}" for i, (aid, _) in enumerate(layout)}
            steps = []
            for aid, deps in layout:
                d = [sid[x] for x in deps if x in sid]
                if aid == "analyst" and "bioinfo-agent" in sid:
                    d = [x for x in d if x != sid.get("data_steward")]  # reached through bioinfo-agent
                tokens = ""
                if aid == "analyst" and "[hpc]" in request:
                    tokens += " [hpc]"
                if aid == "data_steward" and "[needs-approval]" in request:
                    tokens += " [needs-approval]"
                if aid == "biologist" and "[block]" in request:
                    tokens += " [block]"
                if aid == "analyst" and "[revision-fail]" in request:
                    tokens += " [revision-fail]"
                if aid == "biologist" and "[max-turns]" in request:
                    tokens += " [max-turns]"
                if aid == "data_steward" and "[artifact]" in request:
                    tokens += " [artifact]"
                if aid == "bioinfo-agent" and "[step-fail]" in request:
                    tokens += " [step-fail]"
                steps.append({"id": sid[aid], "agent_id": aid, "depends_on": d,
                              "instruction": f"{aid} 파트 수행{tokens}",
                              "outputs": ["artifact.txt"] if "[artifact]" in tokens else []})
            recruit = []
            if "[recruit]" in request:
                recruit.append({"paper": "https://doi.org/10.1186/s13059-017-1382-0",
                                "repo": "https://github.com/scverse/scanpy",
                                "focus": "Preprocessing and clustering", "reason": "mock: 팀에 scRNA 전문가 없음",
                                "step_ids": [steps[0]["id"]]})
            questions = [{"question": "Which sample group should be analyzed?",
                          "options": ["cases", "controls", "both"], "allow_free_text": True, "depth": 60}] if (
                "[question]" in request and "PI clarification (questions and answer):" not in request) else []
            verdict = "out" if "[out-of-scope]" in request else "borderline" if "[borderline]" in request else "in"
            structured = {"scope": {"verdict": verdict, "reason": f"mock: {verdict} of lab scope"},
                          "clarifying_questions": questions, "steps": steps, "recruit": recruit, "notes": "mock plan"}
        elif kind == "review" and _is_schema(t, "verdict", "issues") and not _is_schema(t, "scores"):
            structured = {"verdict": "accept", "issues": []}  # the research lane's one review after CP2 (no scores)
        elif kind == "review":
            revise = "[revise]" in request and t.meta.get("revision", 0) == 0
            m = re.search(r"### (\S+) · analyst", t.prompt)
            target = m.group(1) if m else "s1"
            structured = {
                "verdict": "revise" if revise else "accept",
                "scores": {"addresses_question": 4, "evidence": 3 if revise else 4, "thoroughness": 4},
                "issues": [{"step_id": target, "priority": "P1", "problem": "민감도 분석 누락",
                            "request": "파라미터 2개로 재분석"}]
                          if revise else [],
            }
        elif kind == "recruit":
            slug = t.meta.get("slug", "paper")
            skill = ctx.workdir / "dist" / f"{slug}-agent" / "skill" / f"{slug}-paper"
            skill.mkdir(parents=True, exist_ok=True)
            (skill / "SKILL.md").write_text(f"---\nname: {slug}-paper\ndescription: mock paper skill\n---\n# {slug}\n", encoding="utf-8")
            structured = {"agent_name": f"{slug} 파견연구원", "role_summary": f"{slug} 방법론 자문 (mock)",
                          "kind": "consultant", "mcp_zip": None, "mcp_dir": None, "mcp_command": None,
                          "mcp_args": [], "mcp_env_required": [], "skill_dir": str(skill), "tools": [],
                          "verification_status": "reviewed", "limitations": ["mock conversion"],
                          "source_commit": hashlib.sha1(slug.encode()).hexdigest(), "code_license": "BSD-3-Clause",
                          "weights_license": None, "external_services": []}

        # steer only by this agent's own instruction, not by the overall request quoted in the prompt
        # Real engines retain the instruction in their session; mock uses the step metadata.
        own = (t.meta.get("instruction", t.prompt.split("Your step", 1)[-1]) if kind == "step" else
               t.prompt if kind == "direct" else "")
        ask_target = None
        match = re.search(r"\[ask:(cso|facilities|colleague:[A-Za-z0-9_.-]+)\]", own)
        if match:
            ask_target = match.group(1)
        elif "[ask:pi-install]" in own:
            ask_target = "pi"
        if ask_target and not t.resume_session_id:
            question = ("새 package를 설치해도 되나요?" if ask_target == "pi" else
                        "이 막힘을 풀기 위한 결정을 알려 주세요")
            answer = await self._broker(ctx, "/ask", {
                "task_id": t.id, "agent_id": a.id, "to": ask_target, "question": question,
                "why_blocked": "mock 질의 회귀 테스트", "tried": ["mock preflight"],
                "options": [], "refs": [], "wait": "short",
            })
            if answer.get("status") == "answered":
                label = "CSO 답변" if answer.get("from") == "cso" else f"{answer.get('from')} 답변"
                text += f" · {label}: {answer.get('answer')}"
            else:
                text += f" · 질문 {answer.get('ask_id')} 대기 후 hibernate"
        if kind == "wrap_up":
            (ctx.workdir / "outputs" / "PARTIAL_STATUS.md").write_text(
                "Partial results saved; main step unfinished.\n", encoding="utf-8")
        if kind in ("step", "direct") and "[artifact]" in own:  # a direct run declares none; the runner lists it
            (ctx.workdir / "outputs" / "artifact.txt").write_text("mock artifact\n", encoding="utf-8")
        if kind == "step" and "[revision-fail]" in own and t.meta.get("revision", 0):
            return TaskResult(task_id=t.id, agent_id=a.id, ok=False, error="revision failed",
                              session_id=f"mock-session-{t.id[-4:]}", cost_usd=0.0)
        if kind == "step" and "[step-fail]" in own:
            return TaskResult(task_id=t.id, agent_id=a.id, ok=False, error="mock step failure",
                              session_id=f"mock-session-{t.id[-4:]}", cost_usd=0.0)
        if kind == "step" and "[max-turns]" in own:
            return TaskResult(task_id=t.id, agent_id=a.id, ok=False, error="error_max_turns",
                              error_kind="error_max_turns", session_id=f"mock-session-{t.id[-4:]}", cost_usd=0.0)
        if (kind == "step" and "[block]" in own and not t.resume_session_id
                and "Your earlier blocking question and its answer:" not in t.prompt):
            structured = {"blocking_decision": "Choose sample group (a) cases or (b) controls."}
        if "[needs-approval]" in own and not t.resume_session_id:
            dec = await self._broker(ctx, "/approval", {"task_id": t.id, "agent_id": a.id, "kind": "tool_permission",
                                                        "summary": "Bash: Rscript scripts/qc_plots.R (mock)",
                                                        "timeout_s": self.settings.policy.approvals.timeout_s})
            text += f" · 승인 결과={'허가' if dec.get('approved') else '거절'}"

        if "[hpc]" in own and not t.resume_session_id:
            if self.settings.hpc.scheduler == "mock":  # a stand-in agent never submits to a real cluster
                # Like hpc_submit: the runner submits the script and records the id (no caller-reported job ids).
                script = ctx.workdir / "jobs" / f"mock_align_{t.id[-4:]}.sh"
                script.parent.mkdir(parents=True, exist_ok=True)
                script.write_text("#!/bin/bash\necho mock\n", encoding="utf-8")
                out = await self._broker(ctx, "/jobs/submit", {
                    "task_id": t.id, "agent_id": a.id, "script": str(script), "name": "mock_align",
                    "cores": 1, "mem": "1G", "walltime": "00:05:00", "reason": "mock"})
                text += " · HPC 작업 제출 후 대기" if out.get("submitted") else " · HPC 제출 안 됨"
            else:
                text += " · HPC 생략(mock 스케줄러가 아님)"
        if t.resume_session_id and not anchors:
            text += " · (깨어나서) 작업 결과 확인 후 마무리"

        if kind == "review" and structured is not None:
            (ctx.workdir / "outputs" / "review.json").write_text(json.dumps(structured), encoding="utf-8")
        return TaskResult(task_id=t.id, agent_id=a.id, ok=True, text=text, structured=structured,
                          session_id=f"mock-session-{t.id[-4:]}", cost_usd=0.0)
