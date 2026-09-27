from __future__ import annotations

import time
import uuid
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


class RunnerUnavailable(ConnectionError):
    """A runner is offline or its WebSocket could not send a message."""


class Engine(str, Enum):
    claude_code = "claude_code"
    codex = "codex"
    gemini = "gemini"
    antigravity = "antigravity"
    cli = "cli"  # any other agent program (e.g. the lab's own bioinfo-agent) behind a command template
    mock = "mock"


class Employment(str, Enum):
    core = "core"  # 정규직: defined in agents/core/*.yaml
    contract = "contract"  # 파견직: hired from a paper via Paper2Agent, has an expiry


class McpServerSpec(BaseModel):
    name: str
    type: Literal["stdio", "http"] = "stdio"
    command: str | None = None
    args: list[str] = []
    env: dict[str, str] = {}  # values may be ${VAR} placeholders; expanded at run time, never stored
    cwd: str | None = None
    url: str | None = None
    headers: dict[str, str] = {}


class CliSpec(BaseModel):
    """How to drive an external agent program.

    Placeholders in command/resume_args: {prompt_file} {prompt} {workdir} {outputs} {task_id}
    {schema_file} {mcp_config} {session_id} {model}.
    output: "text" → every stdout line is a log line and stdout is the answer;
            "jsonl" → lines follow the labhq event protocol (see adapters/cli.py).
    """

    command: list[str]
    stdin: Literal["prompt", "none"] = "none"
    output: Literal["text", "jsonl"] = "text"
    resume_args: list[str] = []
    env: dict[str, str] = {}


class ContractInfo(BaseModel):
    paper: str | None = None  # DOI / URL / local PDF
    repo: str | None = None
    kind: Literal["consultant", "technician", "full"] = "full"  # skill only / MCP only / both
    hired_at: float
    expires_at: float
    status: Literal["probation", "active", "expired", "archived"] = "probation"
    skill_dir: str | None = None
    talent_dir: str | None = None
    verification: dict[str, Any] = {}


class AgentSpec(BaseModel):
    id: str
    name: str
    role: str
    character: str | None = None
    engine: Engine = Engine.claude_code
    model: str | None = None
    system_prompt: str = ""
    tools: list[str] = []  # pre-approved tools (Claude Code --allowedTools rule syntax)
    disallowed_tools: list[str] = []
    builtin_tools: str | None = None  # Claude Code --tools (restrict the built-in tool set)
    builtin_mcp: list[str] = ["approval"]  # labhq-provided MCP servers: approval, hpc
    mcp: list[McpServerSpec] = []
    project_dirs: list[str] = []
    permission_mode: str = "default"
    sandbox: str = "workspace-write"  # Codex sandbox
    cli: CliSpec | None = None  # engine: cli
    max_turns: int | None = None
    max_budget_usd: float | None = None
    employment: Employment = Employment.core
    contract: ContractInfo | None = None
    can_orchestrate: bool = False
    tags: list[str] = []

    @model_validator(mode="after")
    def antigravity_has_no_mcp(self) -> "AgentSpec":
        if self.engine == Engine.antigravity and (self.builtin_mcp or self.mcp):
            raise ValueError("antigravity does not support builtin_mcp or MCP servers")
        return self

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "role": self.role,
            "character": self.character,
            "engine": self.engine.value,
            "model": self.model,
            "employment": self.employment.value,
            "expires_at": self.contract.expires_at if self.contract else None,
            "contract_status": self.contract.status if self.contract else None,
            "mcp": [m.name for m in self.mcp],
            "tags": self.tags,
        }


class Task(BaseModel):
    id: str = Field(default_factory=lambda: new_id("task"))
    request_id: str | None = None
    agent_id: str
    prompt: str
    context: str = ""  # upstream teammates' results
    output_schema: dict[str, Any] | None = None
    resume_session_id: str | None = None
    budget_usd: float | None = None
    created_at: float = Field(default_factory=time.time)
    meta: dict[str, Any] = {}


class TaskResult(BaseModel):
    task_id: str
    agent_id: str
    ok: bool
    text: str = ""
    structured: Any = None
    session_id: str | None = None
    cost_usd: float | None = None
    pending_jobs: list[str] = []
    workdir: str | None = None
    error: str | None = None


class Event(BaseModel):
    type: str
    ts: float = Field(default_factory=time.time)
    task_id: str | None = None
    agent_id: str | None = None
    request_id: str | None = None
    data: dict[str, Any] = {}


class ApprovalRequest(BaseModel):
    id: str = Field(default_factory=lambda: new_id("appr"))
    task_id: str | None = None
    agent_id: str | None = None
    request_id: str | None = None
    kind: str  # hpc_submit | tool_permission | budget | recruit | download
    summary: str
    detail: dict[str, Any] = {}
    created_at: float = Field(default_factory=time.time)
    timeout_s: int = 3600
