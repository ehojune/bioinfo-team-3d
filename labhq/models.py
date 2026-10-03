from __future__ import annotations

import re
import time
import uuid
from enum import Enum
from typing import Any, Iterable, Literal

from pydantic import BaseModel, Field, field_validator, model_serializer, model_validator


ASK_WAIT_SECONDS = {"cso": 300, "facilities": 1200, "colleague": 900, "pi": 0}
ASK_MAX_WAIT_S = max(ASK_WAIT_SECONDS.values())


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


class RunnerUnavailable(ConnectionError):
    """A runner is offline or its WebSocket could not send a message."""


class Engine(str, Enum):
    claude_code = "claude_code"
    codex = "codex"
    gemini = "gemini"
    antigravity = "antigravity"
    cli = "cli"  # any other agent program behind a command template
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
    # Read-only public server (e.g. PubMed): its tools run without a per-call approval. Non-interactive
    # `codex exec` otherwise refuses them ("approval policy is never") and the step fails.
    auto_approve: bool = False
    timeout_s: int | None = None


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
    plugin_dirs: list[str] = []  # Claude Code --plugin-dir; ${VAR} expands at run time
    allow_skills: bool = False  # per-agent exception to --disable-slash-commands
    required_skills: list[str] = []  # "plugin:skill" that must exist in plugin_dirs before spawn
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
        if self.engine != Engine.claude_code and (self.plugin_dirs or self.allow_skills or self.required_skills):
            raise ValueError("plugin_dirs, allow_skills and required_skills require engine: claude_code")
        for skill in self.required_skills:
            if skill.count(":") != 1 or not all(skill.split(":")):
                raise ValueError(f"required_skills entries are 'plugin:skill', got {skill!r}")
        if self.required_skills and not (self.plugin_dirs and self.allow_skills):
            raise ValueError("required_skills needs plugin_dirs and allow_skills: true")
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
            "builtin_mcp": self.builtin_mcp,
            "sandbox": self.sandbox,
            "tools": self.tools,
            "disallowed_tools": self.disallowed_tools,
            "builtin_tools": self.builtin_tools,
            "permission_mode": self.permission_mode,
            "max_turns": self.max_turns,
            "cli_resume": bool(self.cli and self.cli.resume_args),
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
    blocking_decision: str | None = None
    session_id: str | None = None
    cost_usd: float | None = None
    cost_known: bool | None = None
    usage: dict[str, int] = {}
    usage_known: bool = True
    pending_jobs: list[str] = []
    pending_asks: list[str] = []
    workdir: str | None = None
    workdir_id: str | None = None
    outputs: list[str] = []  # paths relative to workdir
    missing_outputs: list[str] = []
    # output relpath -> type record (labhq.vocab.declare.runner_records); only for collected outputs (#221)
    output_types: dict[str, Any] = {}
    output_sha256: dict[str, str] = {}
    unreported_outputs: list[str] = []
    # Parsed only for ordinary orchestration steps. Research steps keep their result v2 ledger in ``structured``.
    general_sections: dict[str, str] = {}
    evidence_path_warnings: list[str] = []
    # One bounded first line per failed tool call, collected by the runner for ordinary steps only.
    tool_errors: list[str] = []
    provenance: dict[str, Any] = {}  # manifest summary; the gateway may not share the runner's disk
    pipeline_submission: dict[str, Any] | None = None  # gateway-only handoff; stripped before web publication
    partial_results: bool = False
    revision_failed: str | None = None
    error_kind: str | None = None
    error: str | None = None
    # Epoch seconds of a subscription-quota reset. The runner reads the CLI's clock time in its own zone (#37).
    quota_reset_at: float | None = None

    @model_validator(mode="after")
    def infer_cost_known(self) -> "TaskResult":
        if self.cost_known is None:
            self.cost_known = self.cost_usd is not None
        return self

    @field_validator("output_types", mode="before")
    @classmethod
    def bounded_output_types(cls, value: Any) -> dict[str, Any]:
        # A malformed or oversized value from another runner version drops the declarations, never the result.
        from .vocab.declare import bounded_records
        return bounded_records(value) if value is not None else {}

    @model_serializer(mode="wrap")
    def _drop_empty_internal_fields(self, handler: Any) -> dict[str, Any]:
        # Results without declarations, a gateway handoff or a quota reset serialize exactly as before those
        # fields existed.
        data = handler(self)
        if isinstance(data, dict):
            if not data.get("output_types"):
                data.pop("output_types", None)
            if not data.get("output_sha256"):
                data.pop("output_sha256", None)
            if not data.get("unreported_outputs"):
                data.pop("unreported_outputs", None)
            if not data.get("general_sections"):
                data.pop("general_sections", None)
            if not data.get("evidence_path_warnings"):
                data.pop("evidence_path_warnings", None)
            if not data.get("tool_errors"):
                data.pop("tool_errors", None)
            if data.get("pipeline_submission") is None:
                data.pop("pipeline_submission", None)
            if data.get("quota_reset_at") is None:
                data.pop("quota_reset_at", None)
        return data


def waiting(result: TaskResult | dict[str, Any], *, jobs_finished: bool = False) -> bool:
    """Whether a live or persisted result still waits for jobs or ask answers."""
    if isinstance(result, TaskResult):
        jobs, asks = result.pending_jobs, result.pending_asks
    else:
        jobs, asks = result.get("pending_jobs"), result.get("pending_asks")
    return bool(asks or (not jobs_finished and jobs))


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


class AskRequest(BaseModel):
    id: str = Field(default_factory=lambda: new_id("ask"))
    task_id: str | None = None
    agent_id: str | None = None
    request_id: str | None = None
    to: str
    question: str = Field(min_length=1, max_length=700)
    why_blocked: str = Field(min_length=1, max_length=1200)
    tried: list[str] = Field(default_factory=list, max_length=12)
    options: list[str] = Field(default_factory=list, max_length=12)
    refs: list[str] = Field(default_factory=list, max_length=20)
    # Set by the runner's capability token, never trusted from the MCP caller (#86).
    source_workdir: str | None = None
    wait: Literal["short", "hibernate"] = "short"
    created_at: float = Field(default_factory=time.time)

    @model_validator(mode="after")
    def valid_target_and_refs(self) -> "AskRequest":
        if self.to not in {"cso", "facilities", "pi"} and not re.fullmatch(
            r"colleague:[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", self.to
        ):
            raise ValueError("to must be cso, facilities, pi, or colleague:<agent_id>")
        for ref in self.refs:
            normalized = ref.replace("\\", "/")
            if (not normalized or normalized.startswith(("/", "../")) or "/../" in normalized
                    or normalized.endswith("/..") or re.match(r"^[A-Za-z]:", normalized)):
                raise ValueError("refs must be relative paths inside the task workspace")
        self.question = self.question.strip()
        self.why_blocked = self.why_blocked.strip()
        if not self.question or not self.why_blocked:
            raise ValueError("question and why_blocked cannot be blank")
        return self


HARD_STOP_PATTERNS = {
    "data_zone": (r"통제.{0,12}(데이터|구역|원본)", r"데이터.{0,12}구역",
                  r"\b(?:data.{0,8}(?:zone|boundary)|restricted.{0,8}(?:data|zone))\b",
                  r"controlled.?access", r"\bdua\b"),
    "budget_cap": (r"(비용|예산).{0,12}(상한|초과)",
                   r"(?:budget|cost).{0,12}(cap|limit|exceed|overrun)"),
    "out_of_scope": (r"범위.{0,6}(밖|외|초과)", r"요청.{0,8}(밖|외)", r"out.of.scope", r"scope.{0,8}(expand|outside)"),
    "installation": (r"설치", r"\binstall(?:ation|ing)?\b", r"\b(?:pip|conda|npm|apt)\s+install\b"),
    "destructive": (r"(재귀 )?삭제", r"파괴", r"\brm\s+-[a-z]*r[a-z]*f\b", r"\b(drop|format|overwrite|destroy|delete)\b"),
}


def hard_stop_kind(ask: AskRequest, allowed: Iterable[str] | None = None) -> str | None:
    text = "\n".join([ask.question, ask.why_blocked, *ask.tried, *ask.options]).casefold()
    enabled = set(HARD_STOP_PATTERNS) if allowed is None else set(allowed)
    for kind, patterns in HARD_STOP_PATTERNS.items():
        if kind in enabled and any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns):
            return kind
    return None
