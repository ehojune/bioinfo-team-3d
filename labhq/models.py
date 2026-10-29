from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Annotated, Any, Iterable, Literal

from pydantic import BaseModel, Field, field_validator, model_serializer, model_validator


ASK_WAIT_SECONDS = {"cso": 300, "facilities": 1200, "colleague": 900, "pi": 0}
ASK_MAX_WAIT_S = max(ASK_WAIT_SECONDS.values())


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


class RunnerUnavailable(ConnectionError):
    """A runner is offline or its WebSocket could not send a message."""


class RunnerNotSent(RunnerUnavailable):
    """The message provably never left: the runner was offline, or its connection was replaced before sending."""


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


# A bare hostname or IP, optionally :port. Never a URL: a scheme, user:password@, path, query or fragment could carry
# a credential into the contract YAML, roster events and web cards (#424, PR #522 review).
SERVICE_HOST = re.compile(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*"
                          r"|\[[0-9a-f:.]+\])(?::[0-9]{1,5})?")


class ExternalService(BaseModel):
    host: str
    what_is_sent: str

    @field_validator("host")
    @classmethod
    def bare_host(cls, value: str) -> str:
        host = value.strip().lower()
        port = host.rsplit(":", 1)[1] if re.search(r":[0-9]+$", host) else None
        if not SERVICE_HOST.fullmatch(host) or (port is not None and not 0 < int(port) < 65536):
            raise ValueError("external_services host must be a bare hostname or host:port, not a URL")
        return host


SOURCE_COMMIT = re.compile(r"[0-9a-f]{40}")


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
    # What the hire was built from and where its tools send data (#424). A contract saved before these fields
    # loads with the defaults. The two hashes are labhq's own (recruit/provenance.py), never the offer's.
    source_commit: str | None = None  # full commit of the converted code repository
    code_license: str | None = None
    weights_license: str | None = None
    external_services: list[ExternalService] = []
    skill_sha256: str | None = None
    mcp_sha256: str | None = None
    mcp_path: str | None = None  # the MCP folder or ZIP mcp_sha256 was taken from; a rehire hashes it again
    # The server tree mcp_args actually runs when it lies outside mcp_path (an extracted ZIP, PR #522 review).
    mcp_entry_path: str | None = None
    mcp_entry_sha256: str | None = None

    @field_validator("source_commit")
    @classmethod
    def full_commit(cls, value: str | None) -> str | None:
        if value is not None and not SOURCE_COMMIT.fullmatch(value):
            raise ValueError("source_commit must be a full 40-character lowercase hex commit")
        return value

    def provenance(self) -> dict[str, Any]:
        return {"source_commit": self.source_commit, "code_license": self.code_license,
                "weights_license": self.weights_license,
                "external_services": [s.model_dump() for s in self.external_services],
                "skill_sha256": self.skill_sha256, "mcp_sha256": self.mcp_sha256,
                "rehire_changed": list(self.verification.get("rehire_changed") or [])}


class AgentSpec(BaseModel):
    id: str
    name: str
    role: str
    character: str | None = None
    engine: Engine = Engine.claude_code
    model: str | None = None
    model_tier: Literal["fast", "balanced", "reasoning"] | None = None
    effort: str | None = None
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
    def valid_engine_options(self) -> "AgentSpec":
        from .staff_skills import is_bundled_skill

        efforts = {
            Engine.claude_code: {"low", "medium", "high", "xhigh", "max"},
            Engine.codex: {"low", "medium", "high", "xhigh", "max", "ultra"},
        }
        if self.effort is not None:
            allowed = efforts.get(self.engine)
            if allowed is None:
                raise ValueError(f"effort is not supported for engine: {self.engine.value}")
            if self.effort not in allowed:
                values = ", ".join(sorted(allowed))
                raise ValueError(f"unknown {self.engine.value} effort {self.effort!r}; allowed: {values}")
        bundled, plugin = [], []
        for skill in self.required_skills:
            if ":" not in skill and is_bundled_skill(skill):
                bundled.append(skill)
            elif skill.count(":") == 1 and all(skill.split(":")):
                plugin.append(skill)
            else:
                raise ValueError(f"required_skills entries are a bundled skill or 'plugin:skill', got {skill!r}")
        if self.engine != Engine.claude_code and (self.plugin_dirs or self.allow_skills or plugin):
            raise ValueError("plugin_dirs, allow_skills and plugin required_skills require engine: claude_code")
        if bundled and self.engine not in (Engine.claude_code, Engine.codex):
            raise ValueError("bundled required_skills require engine: claude_code or codex")
        if bundled and self.engine == Engine.claude_code and not self.allow_skills:
            raise ValueError("bundled required_skills need allow_skills: true for Claude Code")
        if plugin and not (self.plugin_dirs and self.allow_skills):
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
            "model_tier": self.model_tier,
            "employment": self.employment.value,
            "expires_at": self.contract.expires_at if self.contract else None,
            "contract_status": self.contract.status if self.contract else None,
            "contract_provenance": self.contract.provenance() if self.contract else None,
            "mcp": [m.name for m in self.mcp],
            "builtin_mcp": self.builtin_mcp,
            "sandbox": self.sandbox,
            "tools": self.tools,
            "disallowed_tools": self.disallowed_tools,
            "builtin_tools": self.builtin_tools,
            "permission_mode": self.permission_mode,
            "max_turns": self.max_turns,
            "effort": self.effort,
            "cli_resume": bool(self.cli and self.cli.resume_args),
            "tags": self.tags,
        }


@dataclass(frozen=True)
class PathShape:
    """Which part of a wire field contains paths (not arbitrary user text)."""

    kind: Literal["scalar", "items", "keys", "values", "opaque"]
    when: tuple[str, str] | None = None


WirePath = Annotated[str, PathShape("scalar")]
WirePaths = Annotated[list[str], PathShape("items")]
WirePathKeys = Annotated[dict[str, Any], PathShape("keys")]
WirePathValues = Annotated[dict[str, str], PathShape("values")]
WireReference = Annotated[str, PathShape("scalar", ("kind", "path"))]
WireOpaque = Annotated[Any, PathShape("opaque")]


@dataclass
class RunnerPathFields:
    """Path contract for Task.meta, TaskResult, Event.data and their manifests.

    The boundary derives its field table from these annotations. Keep structured
    user content opaque; new path fields must be declared here (checked in tests).
    Relative paths stay relative. Mapping keys such as step IDs stay unchanged.
    """

    workdir: WirePath
    source_workdir: WirePath
    cwd: WirePath
    workspace_root: WirePath
    project_dirs: WirePaths
    plugin_dirs: WirePaths
    reference_dirs: WirePaths
    reference_roots: WirePaths
    upstream_dirs: WirePaths
    upstream_steps: WirePathValues
    outputs: WirePaths
    required_outputs: WirePaths
    optional_outputs: WirePaths
    missing_outputs: WirePaths
    missing_optional_outputs: WirePaths
    unreported_outputs: WirePaths
    output_sha256: WirePathKeys
    output_types: WirePathKeys
    output_tool_use_ids: WirePathKeys
    consult_refs: WirePaths
    path: WirePath
    file_path: WirePath
    local_dir: WirePath
    skill_dir: WirePath
    talent_dir: WirePath
    mcp_path: WirePath
    mcp_entry_path: WirePath
    value: WireReference
    manifest: WirePath
    manifest_path: WirePath
    bundle: WirePath
    bundle_path: WirePath
    read_only_roots: WirePathValues
    # These are content, not transport fields, even when they contain a "path" key.
    prompt: WireOpaque
    context: WireOpaque
    text: WireOpaque
    structured: WireOpaque
    output_schema: WireOpaque
    workspace_files: WireOpaque
    workdir_id: WireOpaque
    evidence_path_warnings: WireOpaque


class Task(BaseModel):
    id: str = Field(default_factory=lambda: new_id("task"))
    request_id: str | None = None
    agent_id: str
    prompt: str
    context: str = ""  # upstream teammates' results
    output_schema: dict[str, Any] | None = None
    resume_session_id: str | None = None
    budget_usd: float | None = None
    request_budget_remaining_usd: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    created_at: float = Field(default_factory=time.time)
    meta: Annotated[dict[str, Any], RunnerPathFields] = {}


class ToolCallCount(BaseModel):
    calls: int = Field(ge=0)
    failures: int = Field(ge=0)

    @model_validator(mode="after")
    def failures_do_not_exceed_calls(self) -> "ToolCallCount":
        if self.failures > self.calls:
            raise ValueError("tool call failures cannot exceed calls")
        return self


EXTERNAL_LOOKUP_TOOL = r"^[a-z][a-z0-9_]{0,47}$"
EXTERNAL_LOOKUP_MAX_S = 366 * 86400.0  # a sanity bound; a workspace reused for weeks keeps its step budget
EXTERNAL_LOOKUP_MAX_TOOLS = 50


class ExternalLookupTool(BaseModel):
    """Outcome counts of one lookup tool in a step's outputs/external_lookup_status.jsonl (#676)."""
    tool: str = Field(pattern=EXTERNAL_LOOKUP_TOOL)
    ok: int = Field(default=0, ge=0)
    timeout: int = Field(default=0, ge=0)
    failed: int = Field(default=0, ge=0)
    cancelled: int = Field(default=0, ge=0)
    # The tool's last record is "waiting": a lookup started and no outcome was written.
    unfinished: bool = False
    elapsed_s: float = Field(ge=0, le=EXTERNAL_LOOKUP_MAX_S, allow_inf_nan=False)
    timeout_s: float = Field(gt=0, le=EXTERNAL_LOOKUP_MAX_S, allow_inf_nan=False)

    @property
    def missing(self) -> bool:
        return bool(self.timeout or self.failed or self.cancelled or self.unfinished)


class ExternalLookupRecord(BaseModel):
    """The workspace's own lookup status file read back by the runner. It is the step workspace's record, not proof
    that a tool was called or a paper was read; it never fills an evidence slot."""
    tools: list[ExternalLookupTool] = Field(default_factory=list, max_length=EXTERNAL_LOOKUP_MAX_TOOLS)
    invalid_rows: int = Field(default=0, ge=0)
    duplicate_rows: int = Field(default=0, ge=0)
    dropped_tools: int = Field(default=0, ge=0)
    truncated: bool = False
    # The file is there but is a link, not a plain file, or failed to read: not the same as no file.
    unreadable: bool = False


class TaskResult(BaseModel):
    task_id: str
    agent_id: str
    ok: bool
    text: str = ""
    structured: Any = None
    blocking_decision: str | None = None
    session_id: str | None = None
    # "plain" or "compact" when the turn ended with a StructuredOutput format error that a resumed turn may repair; the
    # orchestrator sends that repair as a new dispatch (#673). None: no repair is offered.
    format_retry: str | None = None
    cost_usd: float | None = None
    cost_known: bool | None = None
    usage: dict[str, int] = {}
    usage_known: bool = True
    pending_jobs: list[str] = []
    pending_asks: list[str] = []
    workdir: WirePath | None = None
    workdir_id: str | None = None
    outputs: WirePaths = []  # paths relative to workdir
    missing_outputs: WirePaths = []
    missing_optional_outputs: WirePaths = []
    step_warnings: list[str] = []
    # output relpath -> type record (labhq.vocab.declare.runner_records); only for collected outputs (#221)
    output_types: WirePathKeys = {}
    output_types_stats: dict[str, Any] = {}
    output_sha256: Annotated[dict[str, str], PathShape("keys")] = {}
    request_input_sha256: str | None = None  # runner-observed stable input manifest (#959)
    output_tool_use_ids: Annotated[dict[str, str], PathShape("keys")] = {}
    unreported_outputs: WirePaths = []
    # Parsed only for ordinary orchestration steps. Research steps keep their result v2 ledger in ``structured``.
    general_sections: dict[str, str] = {}
    evidence_path_warnings: list[str] = []
    # One bounded first line per failed tool call, collected for every employee task.
    tool_errors: list[str] = []
    # Tool names and aggregate counts only; arguments, commands and paths never enter this record (#701).
    tool_calls: dict[str, ToolCallCount] = {}
    # Per-turn source ledger keyed by task id. The gateway unions these keys before deriving ``tool_calls`` so a
    # result that was already merged cannot make an earlier turn count twice.
    tool_call_turns: dict[str, dict[str, ToolCallCount]] = {}
    # "<output>: <problem>" for a collected .tsv/.tab/.csv that does not read as a table (#710).
    table_warnings: list[str] = []
    # Reader Markdown marker locations and repair instructions only (#757).
    markdown_warnings: list[str] = []
    # {paper id: best access_level} from this task's labhq_annot.fulltext lookups (#697).
    literature_access: dict[str, str] = {}
    # Metadata-only failures from outputs/literature_access.jsonl; paper text stays under .tmp/literature/ (#745).
    literature_access_failures: dict[str, str] = {}
    # Public paper IDs only: discovery candidates, selected fulltext inputs and unread reasons (#744).
    literature_ledger: dict[str, Any] = {}
    # Counts and seconds only from outputs/external_lookup_status.jsonl; None when the step left no such file (#676).
    external_lookups: ExternalLookupRecord | None = None
    provenance: dict[str, Any] = {}  # manifest summary; the gateway may not share the runner's disk
    pipeline_submission: dict[str, Any] | None = None  # gateway-only handoff; stripped before web publication
    partial_results: bool = False
    revision_failed: str | None = None
    exit_code: int | None = None
    shutdown_evidence: dict[str, Any] = {}
    # What the runner itself observed of the staff CLI wrapper's end (#863): Job activation, wrapper identity, wait and
    # exit. Kept apart from shutdown_evidence, whose presence means an interruption; merged into it only then.
    cleanup_evidence: dict[str, Any] = {}
    error_kind: str | None = None
    error: str | None = None
    # Epoch seconds of a subscription-quota reset. The runner reads the CLI's clock time in its own zone (#37).
    quota_reset_at: float | None = None
    # The environment signature behind a failure (#35): {id, cause, hint, source[, fix, package]}. The runner sets it from the
    # CLI's stderr tail or a failed shell command; the gateway from the error. Read through cso.environment_problem.
    environment: dict[str, str] | None = None
    # One PI-approved facilities repair attempted before this step's single rerun (#35 stage 3).
    facilities_fix: dict[str, Any] | None = None
    # Which of the step's steer messages this turn delivered and which it did not, and whether it ended paused (#59 ①).
    # The runner reports it; the gateway puts the undelivered ones back on the step for its next turn.
    control: dict[str, Any] | None = None

    @field_validator("environment", mode="before")
    @classmethod
    def bounded_environment(cls, value: Any) -> dict[str, str] | None:
        from .facilities.signatures import clean_record
        return clean_record(value)

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

    @field_validator("output_types_stats", mode="before")
    @classmethod
    def bounded_output_type_stats(cls, value: Any) -> dict[str, Any]:
        from .vocab.declare import bounded_stats
        return bounded_stats(value)

    @field_validator("external_lookups", mode="before")
    @classmethod
    def bounded_external_lookups(cls, value: Any) -> ExternalLookupRecord | None:
        # A malformed record from another runner version drops the summary, never the result.
        if value is None or isinstance(value, ExternalLookupRecord):
            return value
        try:
            return ExternalLookupRecord.model_validate(value)
        except ValueError:
            return None

    @model_serializer(mode="wrap")
    def _drop_empty_internal_fields(self, handler: Any) -> dict[str, Any]:
        # Results without declarations, a gateway handoff or a quota reset serialize exactly as before those
        # fields existed.
        data = handler(self)
        if isinstance(data, dict):
            if not data.get("output_types"):
                data.pop("output_types", None)
            if not data.get("output_types_stats"):
                data.pop("output_types_stats", None)
            if not data.get("output_sha256"):
                data.pop("output_sha256", None)
            if not data.get("output_tool_use_ids"):
                data.pop("output_tool_use_ids", None)
            if not data.get("unreported_outputs"):
                data.pop("unreported_outputs", None)
            if not data.get("general_sections"):
                data.pop("general_sections", None)
            if not data.get("evidence_path_warnings"):
                data.pop("evidence_path_warnings", None)
            if not data.get("tool_errors"):
                data.pop("tool_errors", None)
            if not data.get("tool_calls"):
                data.pop("tool_calls", None)
            if not data.get("tool_call_turns"):
                data.pop("tool_call_turns", None)
            if not data.get("table_warnings"):
                data.pop("table_warnings", None)
            if not data.get("markdown_warnings"):
                data.pop("markdown_warnings", None)
            if not data.get("literature_access"):
                data.pop("literature_access", None)
            if not data.get("literature_access_failures"):
                data.pop("literature_access_failures", None)
            if not data.get("literature_ledger"):
                data.pop("literature_ledger", None)
            if data.get("external_lookups") is None:
                data.pop("external_lookups", None)
            if data.get("pipeline_submission") is None:
                data.pop("pipeline_submission", None)
            if data.get("quota_reset_at") is None:
                data.pop("quota_reset_at", None)
            if data.get("environment") is None:
                data.pop("environment", None)
            if data.get("facilities_fix") is None:
                data.pop("facilities_fix", None)
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
    data: Annotated[dict[str, Any], RunnerPathFields] = {}


class ApprovalRequest(BaseModel):
    id: str = Field(default_factory=lambda: new_id("appr"))
    task_id: str | None = None
    agent_id: str | None = None
    request_id: str | None = None
    kind: str  # hpc_submit | tool_permission | budget | recruit | download | facilities_fix | wsl_setup
    summary: str
    detail: dict[str, Any] = {}
    created_at: float = Field(default_factory=time.time)
    timeout_s: int = 3600
    pi_summary: str = ''
    pi_reason: str = ''
    pi_outcome: str = ''
    pi_explainer: str = ''
    pi_needs_interpretation: bool = False

    @model_validator(mode='after')
    def explain_for_pi(self) -> 'ApprovalRequest':
        from .pi_text import approval_copy
        missing = not self.pi_summary
        for key, value in approval_copy(self.kind, self.summary, self.detail).items():
            if not getattr(self, key):
                setattr(self, key, value)
        if missing:
            self.pi_explainer = 'chief_of_staff'
            if self.kind == 'tool_permission':
                from .tool_explanation import needs_interpretation
                self.pi_needs_interpretation = needs_interpretation(self.detail)
        return self


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
    # Also from the token: the asking task's inputs touch a restricted zone, so no staff that send data out may
    # answer it (#424). The question and its refs come from that task.
    source_restricted: bool = False
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
    "destructive": (r"(재귀 )?삭제", r"파괴", r"\brm\s+-[a-z]*r[a-z]*f\b",
                    r"\b(?:destroy|delete)\b", r"\bdrop\s+(?:table|database)\b",
                    r"\bformat\s+(?:disk|drive|volume)\b"),
}

DEFAULT_HARD_STOPS = ("data_zone", "budget_cap", "installation")


def hard_stop_kind(ask: AskRequest, allowed: Iterable[str] | None = None) -> str | None:
    text = "\n".join([ask.question, ask.why_blocked, *ask.tried, *ask.options]).casefold()
    enabled = set(DEFAULT_HARD_STOPS if allowed is None else allowed)
    # Deletion and similarly destructive operations are an intrinsic PI gate, not an optional routing policy.
    enabled.add("destructive")
    for kind, patterns in HARD_STOP_PATTERNS.items():
        if kind == "destructive" and re.search(r"\boverwr(?:ite|ites|iting|itten)\b|덮어[쓰쓸]|덮어쓰기", text):
            # Only explicitly named task outputs may be regenerated without PI.
            # Mentioning an output alongside raw/source inputs is not an exception.
            outputs = re.findall(r"(?<![\w/\\])(?:\./)?outputs[/\\][^\s?;]+", text)
            source = re.search(r"\b(?:raw|source|inputs?|original)\b|원본|입력", text)
            if source or not outputs or any(".." in re.split(r"[/\\]", path) for path in outputs):
                return kind
        if kind in enabled and any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns):
            return kind
    return None
