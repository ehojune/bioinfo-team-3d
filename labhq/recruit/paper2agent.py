"""파견직 채용: turn a paper (+ its code) into a contract agent with the Paper2Agent skill.

1. 인사팀 (recruiter agent, Claude Code) runs the paper2agent skill in talent/<slug>/build.
2. It returns a structured "offer letter" (JSON schema below): tools, MCP entry point, skill dir.
3. Probation: labhq starts the delivered MCP server and lists its tools.
4. A contract AgentSpec (with expiry) is written to agents/contract/ and mirrored to the talent pool.

Paper2Agent output (see its SKILL.md): dist/<project>-agent/{skill/<paper>-paper, mcp/<repo>-mcp}
or dist/<repo>-mcp.zip with a USAGE.md.
"""

from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..models import AgentSpec, ContractInfo, Employment, Engine, Event, McpServerSpec, Task
from ..tools._mcpcompat import list_tools
from ..util import extract_json, short, slugify

if TYPE_CHECKING:
    from ..runner.daemon import Runner

_STR_OR_NULL = {"type": ["string", "null"]}
OFFER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "agent_name": {"type": "string"},
        "role_summary": {"type": "string"},
        "kind": {"type": "string", "enum": ["consultant", "technician", "full"]},
        "mcp_zip": _STR_OR_NULL,
        "mcp_dir": _STR_OR_NULL,
        "mcp_command": _STR_OR_NULL,
        "mcp_args": {"type": "array", "items": {"type": "string"}},
        "mcp_env_required": {"type": "array", "items": {"type": "string"}},
        "skill_dir": _STR_OR_NULL,
        "tools": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "properties": {"name": {"type": "string"}, "description": {"type": "string"}},
            "required": ["name", "description"]}},
        "verification_status": {"type": "string"},
        "limitations": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["agent_name", "role_summary", "kind", "mcp_zip", "mcp_dir", "mcp_command", "mcp_args",
                 "mcp_env_required", "skill_dir", "tools", "verification_status", "limitations"],
}

CONTRACT_PROMPT = """You are {name}, a contract specialist (파견직) hired into the lab from a published method.
Source paper: {paper}
Code: {repo}
What you bring: {role}

Your paper-derived tools (MCP server `{server}`):
{tools}

Rules:
- Solve the assigned sub-task by applying this paper's method through its tools; do not reimplement the algorithm.
- If the request is outside the method's validated scope (organism, assay, data type), say so and hand it back
  to the CSO instead of improvising.
- Log every tool call's parameters and versions so the result is reproducible; cite output paths.
- Known limitations from onboarding:
{limits}
"""


def skill_path(engine: str) -> Path:
    base = "~/.claude/skills/paper2agent" if engine == "claude_code" else "~/.agents/skills/paper2agent"
    return Path(base).expanduser()


def skill_installed(engine: str = "claude_code") -> bool:
    return (skill_path(engine) / "SKILL.md").exists()


def install_skill(source: str, cache: Path) -> list[Path]:
    """git clone Paper2Agent and copy skills/paper2agent to Claude Code and Codex skill dirs."""
    repo = cache / "Paper2Agent"
    if (repo / ".git").exists():
        subprocess.run(["git", "-C", str(repo), "pull", "--ff-only"], check=True)
    else:
        cache.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", "--depth", "1", source, str(repo)], check=True)
    out = []
    for engine in ("claude_code", "codex"):
        dst = skill_path(engine)
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(repo / "skills" / "paper2agent", dst)
        out.append(dst)
    return out


def build_prompt(paper: str | None, repo: str | None, focus: str | None, out_dir: Path) -> str:
    lines = [
        "Use the paper2agent skill to agentify this paper and its associated files, alongside its code "
        "repository if available. Follow the skill instructions for the workflow, verification, and final delivery.",
        "",
        f"Paper and associated files: {paper or 'none (code-only conversion)'}",
        f"Code repository (if available): {repo or 'none (paper-only conversion)'}",
        f"Output directory: {out_dir}",
    ]
    if focus:
        lines.append(f"Focus on: {focus}")
    lines += [
        "",
        "Do not register the server with any client. After delivery, extract the delivered MCP ZIP to "
        f"{out_dir}/labhq_mcp, install it exactly as its USAGE.md says, and confirm it starts over stdio.",
        "Then answer with the offer-letter JSON: absolute paths for mcp_zip, mcp_dir and skill_dir; the exact "
        "tested interpreter as mcp_command and the server entry point as mcp_args (absolute paths); names "
        "(never values) of required environment variables; the delivered tools; and verification_status and "
        "limitations exactly as the skill's final report states them. Use null for parts that were not built.",
        "Never write credentials into files or into the offer.",
    ]
    return "\n".join(lines)


class Recruitment:
    def __init__(self, runner: "Runner", msg: dict):
        self.r, self.s = runner, runner.s
        self.paper, self.repo, self.focus = msg.get("paper"), msg.get("repo"), msg.get("focus")
        self.ttl_days = float(msg.get("ttl_days") or self.s.recruit.default_ttl_days)
        self.request_id = msg.get("request_id")
        self.slug = slugify(msg.get("name") or self.repo or self.paper or "contract")

    async def _emit(self, typ: str, data: dict) -> None:
        await self.r.emit(Event(type=typ, request_id=self.request_id, agent_id=self.s.recruit.agent_id,
                                data={"slug": self.slug, **data}))

    async def run(self) -> AgentSpec | None:
        talent = self.r.registry.talent_dir / self.slug
        build = talent / "build"
        build.mkdir(parents=True, exist_ok=True)
        real = not self.s.runner.force_engine
        if real and not skill_installed(self.s.recruit.contract_engine):
            await self._emit("recruit.failed", {"error": "paper2agent skill not installed — run `labhq setup-paper2agent`"})
            return None

        await self._emit("recruit.status", {"stage": "converting", "paper": self.paper, "repo": self.repo})
        task = Task(
            agent_id=self.s.recruit.agent_id, request_id=self.request_id, output_schema=OFFER_SCHEMA,
            prompt=build_prompt(self.paper, self.repo, self.focus, build), budget_usd=self.s.recruit.max_budget_usd,
            meta={"kind": "recruit", "slug": self.slug, "title": f"파견직 채용 변환: {self.slug}",
                  "agent_overrides": {"permission_mode": self.s.recruit.permission_mode}},
        )
        result = await self.r.run_task(task, workdir_override=build)
        offer = result.structured if isinstance(result.structured, dict) else extract_json(result.text)
        if not result.ok or not isinstance(offer, dict):
            await self._emit("recruit.failed", {"error": result.error or "no offer letter", "text": short(result.text, 500)})
            return None

        spec = self._to_spec(offer, talent)
        await self._emit("recruit.status", {"stage": "probation", "agent_id": spec.id})
        tools: list[str] = []
        probe_error = None
        if spec.mcp:
            try:
                tools = await list_tools(spec.mcp[0])
            except Exception as e:
                probe_error = f"{type(e).__name__}: {e}"
        assert spec.contract
        spec.contract.verification = {"offer_status": offer.get("verification_status"), "probe_tools": tools,
                                      "probe_error": probe_error, "limitations": offer.get("limitations", [])}
        passed = bool(tools) if spec.contract.kind != "consultant" else bool(spec.contract.skill_dir)
        spec.contract.status = "active" if passed else "probation"
        path = self.r.registry.save_contract(spec)
        await self._emit("recruit.done", {"agent": spec.summary(), "path": str(path), "passed_probation": passed})
        return spec

    def _to_spec(self, offer: dict, talent: Path) -> AgentSpec:
        skill_dir = None
        src = offer.get("skill_dir")
        if src and Path(src).exists():
            dst = talent / "skill" / Path(src).name
            shutil.copytree(src, dst, dirs_exist_ok=True)
            skill_dir = str(dst)
        server = f"paper_{self.slug.replace('-', '_')}"[:40]
        mcp = []
        if offer.get("mcp_command"):
            mcp.append(McpServerSpec(
                name=server, command=offer["mcp_command"], args=list(offer.get("mcp_args") or []),
                cwd=offer.get("mcp_dir"),
                env={k: "${" + k + "}" for k in offer.get("mcp_env_required") or []},  # values from runner env
            ))
        kind = offer.get("kind") or ("full" if mcp and skill_dir else "technician" if mcp else "consultant")
        tools_txt = "\n".join(f"- {t.get('name')}: {t.get('description', '')}" for t in offer.get("tools") or []) or "- (skill only)"
        limits = "\n".join(f"- {x}" for x in offer.get("limitations") or []) or "- (none reported)"
        now = time.time()
        return AgentSpec(
            id=f"c_{self.slug}"[:48], name=offer.get("agent_name") or self.slug,
            role=f"[파견] {offer.get('role_summary', '')}"[:200], character="paper-hat-chick",
            engine=Engine(self.s.recruit.contract_engine), model=self.s.recruit.contract_model,
            system_prompt=CONTRACT_PROMPT.format(name=offer.get("agent_name"), paper=self.paper, repo=self.repo,
                                                 role=offer.get("role_summary"), server=server, tools=tools_txt,
                                                 limits=limits),
            builtin_mcp=["approval", "hpc"], mcp=mcp, employment=Employment.contract,
            contract=ContractInfo(paper=self.paper, repo=self.repo, kind=kind, hired_at=now,
                                  expires_at=now + self.ttl_days * 86400, skill_dir=skill_dir, talent_dir=str(talent)),
            tags=["contract", "paper2agent"],
        )
