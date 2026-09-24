"""Deterministic stand-in for a real CLI agent. Exercises every platform path without API keys:
planning (DAG), reviewer revise loop, approvals, HPC hibernate/wake, and contract recruitment.

Tokens in the request text steer it: [needs-approval], [hpc], [revise], [recruit].
"""

from __future__ import annotations

import asyncio
import re

import httpx

from ..models import TaskResult
from .base import AgentAdapter, RunContext, RunState

class MockAdapter(AgentAdapter):
    engine = "mock"

    def build_command(self, ctx: RunContext) -> list[str]:  # not used
        return []

    async def handle_line(self, line: str, st: RunState, ctx: RunContext) -> None:  # not used
        return None

    async def _broker(self, ctx: RunContext, path: str, payload: dict) -> dict:
        async with httpx.AsyncClient(timeout=120) as c:
            r = await c.post(f"{ctx.env['LABHQ_BROKER_URL']}{path}", json=payload,
                             headers={"X-Labhq-Token": ctx.env["LABHQ_BROKER_TOKEN"]})
            r.raise_for_status()
            return r.json()

    async def run(self, ctx: RunContext) -> TaskResult:
        t, a = ctx.task, ctx.agent
        kind = t.meta.get("kind", "step")
        request = t.meta.get("request", "") + " " + t.prompt
        await ctx.emit("agent.status", {"state": "working", "model": "mock"})
        await asyncio.sleep(0.05)
        structured = None
        text = f"[mock:{a.id}] {kind} 완료 — {t.prompt.splitlines()[0][:80]}"

        if kind == "plan":
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
                steps.append({"id": sid[aid], "agent_id": aid, "depends_on": d,
                              "instruction": f"{aid} 파트 수행{tokens}"})
            recruit = []
            if "[recruit]" in request:
                recruit.append({"paper": "https://doi.org/10.1186/s13059-017-1382-0",
                                "repo": "https://github.com/scverse/scanpy",
                                "focus": "Preprocessing and clustering", "reason": "mock: 팀에 scRNA 전문가 없음"})
            structured = {"clarifying_questions": [], "steps": steps, "recruit": recruit, "notes": "mock plan"}
        elif kind == "review":
            revise = "[revise]" in request and t.meta.get("revision", 0) == 0
            m = re.search(r"### (\S+) · analyst", t.prompt)
            target = m.group(1) if m else "s1"
            structured = {
                "verdict": "revise" if revise else "accept",
                "scores": {"addresses_question": 4, "evidence": 3 if revise else 4, "thoroughness": 4},
                "issues": [{"step_id": target, "problem": "민감도 분석 누락", "request": "파라미터 2개로 재분석"}]
                          if revise else [],
            }
        elif kind == "recruit":
            slug = t.meta.get("slug", "paper")
            skill = ctx.workdir / "dist" / f"{slug}-agent" / "skill" / f"{slug}-paper"
            skill.mkdir(parents=True, exist_ok=True)
            (skill / "SKILL.md").write_text(f"---\nname: {slug}-paper\ndescription: mock paper skill\n---\n# {slug}\n")
            structured = {"agent_name": f"{slug} 파견연구원", "role_summary": f"{slug} 방법론 자문 (mock)",
                          "kind": "consultant", "mcp_zip": None, "mcp_dir": None, "mcp_command": None,
                          "mcp_args": [], "mcp_env_required": [], "skill_dir": str(skill), "tools": [],
                          "verification_status": "reviewed", "limitations": ["mock conversion"]}

        # steer only by this agent's own instruction, not by the overall request quoted in the prompt
        own = t.prompt.split("Your step", 1)[-1] if kind == "step" else t.prompt if kind == "direct" else ""
        if "[needs-approval]" in own and not t.resume_session_id:
            dec = await self._broker(ctx, "/approval", {"task_id": t.id, "agent_id": a.id, "kind": "tool_permission",
                                                        "summary": "Bash: rm -rf tmp/ (mock)", "timeout_s": 60})
            text += f" · 승인 결과={'허가' if dec.get('approved') else '거절'}"

        if "[hpc]" in own and not t.resume_session_id:
            await self._broker(ctx, "/jobs/track", {"task_id": t.id, "agent_id": a.id,
                                                    "job_id": f"mock-{t.id[-4:]}", "name": "mock_align"})
            text += " · HPC 작업 제출 후 대기"
        if t.resume_session_id:
            text += " · (깨어나서) 작업 결과 확인 후 마무리"

        return TaskResult(task_id=t.id, agent_id=a.id, ok=True, text=text, structured=structured,
                          session_id=f"mock-session-{t.id[-4:]}", cost_usd=0.0)
