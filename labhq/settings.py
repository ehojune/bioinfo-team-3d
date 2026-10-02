from __future__ import annotations

import os
import ntpath
from pathlib import Path
from typing import Any  # semantics-hook
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .intake import Reference


class GatewaySettings(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8787
    url: str = "ws://127.0.0.1:8787"  # what runners dial (outbound, so no inbound port on your PC/HPC)
    runner_token: str = "change-me-runner"
    client_token: str = "change-me-client"
    event_buffer: int = 2000
    resume_wait_s: int = 300
    state_dir: str = Field(default_factory=lambda: os.environ.get("LABHQ_STATE_DIR", "~/.labhq/state"))


class RunnerSettings(BaseModel):
    id: str = "local"
    max_parallel: int = 4
    consult_parallel: int = Field(default=2, ge=1)
    workspace_root: str = "~/.labhq/runs"
    agents_dir: str = "./agents"
    talent_dir: str = "~/.labhq/talent"  # 인재풀: every contract ever hired, kept for rehire
    broker_port: int = 8788
    task_timeout_s: int = 6 * 3600
    job_poll_s: int = 60
    state_dir: str = Field(default_factory=lambda: os.environ.get("LABHQ_STATE_DIR", "~/.labhq/state"))
    outbox_limit: int = 20000
    force_engine: str | None = None  # "mock" runs every agent with the mock engine (demo/tests)
    # Read-only roots for `path` references (#36); each project's local_dir also counts.
    reference_roots: list[str] = []
    # A reference folder is listed for links and mounts before it is exposed; past these caps it is refused.
    reference_scan_max_entries: int = Field(default=20000, ge=1)
    reference_scan_max_depth: int = Field(default=16, ge=0)
    # A consult or follow-up lists every entry it could write to (workspace, writable project/upstream/reference
    # folders) before and after the run; past this many it is refused rather than run unchecked (#36).
    read_only_check_max_entries: int = Field(default=50000, ge=1)


class EngineBin(BaseModel):
    model_config = ConfigDict(extra="forbid")  # a misspelled or unsupported option must not pass silently

    bin: str
    prefix_args: list[str] = []
    extra_args: list[str] = []
    env: dict[str, str] = {}


class IsolatedEngineBin(EngineBin):
    # Staff sessions must not inherit the PI's own CLI setup (hooks, skills, plugins, global instructions).
    # Only engines whose adapter implements it; gemini/antigravity have no flag for it (README §10).
    isolate_user_config: bool = True


class CodexBin(IsolatedEngineBin):
    @field_validator("bin", mode="before")
    @classmethod
    def empty_bin_is_auto(cls, value):
        return "" if value is None else value

    # --ignore-user-config also drops `[windows] sandbox`; without it Codex refuses workspace writes on Windows.
    windows_sandbox: str = "elevated"
    # $CODEX_HOME/AGENTS.md (the PI's global instructions) cannot be switched off by flags; refuse unless allowed.
    allow_global_agents_md: bool = False


class EnginesSettings(BaseModel):
    claude_code: IsolatedEngineBin = IsolatedEngineBin(bin="claude")
    codex: CodexBin = CodexBin(bin="codex")
    gemini: EngineBin = EngineBin(bin="gemini")
    antigravity: EngineBin = EngineBin(bin="agy")


class BenchArm(BaseModel):
    model_config = ConfigDict(extra="forbid")

    engine: Literal["claude_code", "codex"]
    model: str = Field(min_length=1)
    effort: str = Field(min_length=1)


class BenchSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    arms: dict[str, BenchArm] = Field(default_factory=lambda: {
        "sonnet-max": BenchArm(engine="claude_code", model="sonnet", effort="max"),
        "sol-ultra": BenchArm(engine="codex", model="gpt-5.6-sol", effort="ultra"),
        "astra-ultra": BenchArm(engine="codex", model="gpt-6-astra", effort="ultra"),
    })
    staff_model: dict[str, str] = Field(default_factory=lambda: {"opus": "sonnet"})

    @field_validator("arms")
    @classmethod
    def safe_arm_names(cls, value: dict[str, BenchArm]) -> dict[str, BenchArm]:
        import re

        if any(name == "labhq" or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", name) for name in value):
            raise ValueError("bench arm names must be safe slugs; labhq is reserved")
        return value

    @field_validator("staff_model")
    @classmethod
    def nonempty_models(cls, value: dict[str, str]) -> dict[str, str]:
        if any(not source.strip() or not target.strip() for source, target in value.items()):
            raise ValueError("staff models must not be empty")
        return value


class SgeSettings(BaseModel):
    pe: str = "smp"  # parallel environment name differs per cluster (smp, threads, openmp, make…)
    mem_resource: str = "h_vmem"  # or mem_free
    mem_per_slot: bool = True  # h_vmem is usually per slot → total mem is divided by cores
    runtime_resource: str = "h_rt"  # set "" if your cluster rejects h_rt


class PbsSettings(BaseModel):
    resource_template: str = "nodes=1:ppn={cores},mem={mem},walltime={walltime}"  # Torque
    pro: bool = False  # PBS Pro: finished jobs need `qstat -x`; use "select=1:ncpus={cores}:mem={mem}" template


# sbatch options that send the job to another cluster; squeue/sacct/scancel would then find the wrong job.
# The config load check and the job script check share it so the two never disagree (#185).
SLURM_CLUSTER_LONG = r"--clusters?(?:=|$)"
# sbatch short options that take a value: in a bundle ("-vJM") the rest of the token is that value. Every
# other letter counts as a flag, so an unlisted one cannot hide a following M (#172).
SBATCH_VALUE_SHORT = frozenset("aAbBcCdDeFGiJLmnNopqStwx")


def slurm_cluster_option(tokens: list[str]) -> str | None:
    """First sbatch option among `tokens` that picks another cluster (-M, bundled -vM, --cluster(s)), or None."""
    import re

    for token in tokens:
        if re.match(SLURM_CLUSTER_LONG, token):
            return token
        if token.startswith("-") and not token.startswith("--"):
            for letter in token[1:]:
                if letter == "M":
                    return token
                if letter in SBATCH_VALUE_SHORT:
                    break
    return None


def _default_sbatch_args() -> list[str]:
    # --export=NONE: like SGE/PBS without -V, the runner's environment (tokens) stays out of the job.
    return ["--nodes=1", "--ntasks=1", "--cpus-per-task={cores}", "--mem={mem}", "--time={walltime}",
            "--export=NONE"]


class SlurmSettings(BaseModel):
    # sbatch options; {cores} {mem} {walltime} are filled per job. Partition comes from queue/default_queue.
    sbatch_args: list[str] = Field(default_factory=_default_sbatch_args)

    @field_validator("sbatch_args")
    @classmethod
    def options_with_known_fields(cls, value: list[str]) -> list[str]:
        for arg in value:
            if not arg.startswith("-"):
                raise ValueError("hpc.slurm.sbatch_args entries must be sbatch options")
            if slurm_cluster_option([arg]):
                # squeue/sacct/scancel would look the bare id up on the local cluster: wrong or no job.
                raise ValueError("hpc.slurm.sbatch_args must not submit to another cluster (-M/--clusters)")
            try:
                arg.format(cores=1, mem="1G", walltime="01:00:00")
            except (KeyError, IndexError, ValueError) as e:
                raise ValueError("hpc.slurm.sbatch_args may only use {cores}, {mem} and {walltime}") from e
        return value


class HpcSettings(BaseModel):
    scheduler: Literal["sge", "pbs", "slurm", "mock", "none"] = "sge"
    ssh_host: str | None = None  # run qsub/qstat on a login node via ssh (workspace must be on shared FS)
    user: str | None = None  # None → current account
    default_queue: str | None = None
    sge: SgeSettings = SgeSettings()
    pbs: PbsSettings = PbsSettings()
    slurm: SlurmSettings = SlurmSettings()
    command_timeout_s: int = 60
    submit_prefix: list[str] = Field(default_factory=list)  # argv before qsub/sbatch and qdel/scancel only
    job_group: str | None = None  # shared POSIX group for scripts and scheduler logs

    @model_validator(mode="after")
    def require_job_group_for_submit_prefix(self) -> "HpcSettings":
        if self.submit_prefix and not (self.job_group and self.job_group.strip()):
            raise ValueError("hpc.job_group is required when hpc.submit_prefix is set")
        if self.submit_prefix and not (self.user and self.user.strip()):
            raise ValueError("hpc.user is required when hpc.submit_prefix is set")
        return self


class DataZone(BaseModel):
    path: str
    level: Literal["restricted", "internal", "public"] = "restricted"
    note: str = ""

    @field_validator("path")
    @classmethod
    def absolute_path(cls, value: str) -> str:
        expanded = os.path.expandvars(os.path.expanduser(value))
        if not (expanded.startswith("/") or ntpath.isabs(expanded) and bool(ntpath.splitdrive(expanded)[0])):
            raise ValueError("data zone path must be absolute (POSIX, Windows drive, or UNC)")
        return expanded


# Commands that create, change or cancel scheduler jobs outside the approval-gated hpc_* tools: SGE/PBS
# submit, interactive, resubmit, alter, hold/release, signal, move and rerun; Slurm batch, step, allocation,
# crontab and triggers; and every scontrol call but the read-only ones (#172). Bash and PowerShell share it.
SCHEDULER_JOB_COMMANDS = (
    r"\b(?:qsub|qrsh|qsh|qlogin|qmake|qtcsh|qdel|qresub|qalter|qhold|qrls|qsig|qmod|qmove|qrerun|qorder|qrun"
    r"|sbatch|srun|salloc|scancel|scrontab|strigger)\b"
    r"|\bscontrol\b(?!(?:\s+-[\w-]+)*\s+(?:show|ping|listpids|version|help)\b)"
)


def _default_bash_ask() -> list[str]:
    return [
        r"\brm\s+-[a-zA-Z]*r[a-zA-Z]*f|\brm\s+-[a-zA-Z]*f[a-zA-Z]*r",  # rm -rf / -fr
        r"\bsudo\b",
        r"curl[^|]*\|\s*(ba|z)?sh",
        r"wget[^|]*\|\s*(ba|z)?sh",
        SCHEDULER_JOB_COMMANDS,  # use the approval-gated hpc_* tools instead
        r"\bmkfs|\bdd\s+if=",
        r"\bchmod\s+-R\s+777",
        r"git\s+push\s+.*--force",
        r"\bprefetch\b.*--max-size\s+\d{3,}",
    ]


def _default_auto_allow() -> list[str]:
    return ["Read", "Glob", "Grep", "LS", "WebSearch", "WebFetch", "TodoWrite", "Task", "Agent", "Skill"]


class ApprovalRules(BaseModel):
    hpc_core_hours_threshold: float = 0.0  # 0 → every HPC submission asks the PI
    bash_ask_patterns: list[str] = Field(default_factory=_default_bash_ask)
    auto_allow_tools: list[str] = Field(default_factory=_default_auto_allow)
    timeout_s: int = 3600


class BudgetSettings(BaseModel):
    per_task_usd: float = 5.0
    per_request_usd: float = 30.0


class PolicySettings(BaseModel):
    data_zones: list[DataZone] = []
    allow_runner_read_restricted: bool = False
    approvals: ApprovalRules = ApprovalRules()
    budget: BudgetSettings = BudgetSettings()


class RecruitSettings(BaseModel):
    agent_id: str = "recruiter"
    permission_mode: str = "auto"  # unattended conversion; see README (run inside a container if you can)
    max_budget_usd: float = 25.0
    default_ttl_days: int = 30
    contract_engine: str = "claude_code"
    contract_model: str | None = "sonnet"
    skill_source: str = "https://github.com/jmiao24/Paper2Agent"


# A model name labhq passes to an engine CLI as one argument: no leading '-', so it can never read as a flag.
MODEL_NAME_PATTERN = r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}"


class OrchestratorSettings(BaseModel):
    wait_for_clarification: bool = True
    cso_agent: str = "cso"
    chief_of_staff_agent: str | None = "chief_of_staff"
    reviewer_agent: str | None = "sci_reviewer"
    # Per-request CSO overrides may select only one of these models. Fable is excluded because its
    # professional-biology policy makes it unsuitable for this role (#272).
    cso_models: list[str] = ["opus", "gpt-6-astra"]
    max_revisions: int = 1
    # Opt-in (#271): after a step failure or a review `revise`, the CSO may re-plan the unfinished part of
    # the DAG, at most this many times per request. 0 keeps the old behavior (fail, or revise in place).
    max_replans: int = Field(default=0, ge=0)
    max_steps: int = 12
    max_parallel_steps: int = 4
    max_wake_cycles: int = 5
    context_chars_per_step: int = 12000
    step_max_attempts: int = Field(default=2, ge=1)
    step_retry_backoff_s: float = Field(default=0.2, ge=0)
    runner_reconnect_timeout_s: float = Field(default=30, ge=0)

    @field_validator("cso_models")
    @classmethod
    def safe_cso_models(cls, value: list[str]) -> list[str]:
        import re

        models = [model.strip() for model in value]
        if any("fable" in model.casefold() for model in models):
            raise ValueError("Fable models are not allowed for the CSO")
        if any(not re.fullmatch(MODEL_NAME_PATTERN, model) for model in models):
            raise ValueError("CSO model names must use only letters, digits, '.', '_', ':', '/', or '-'")
        if len(models) != len(set(models)):
            raise ValueError("CSO model names must be unique")
        return models


class PlanSettings(BaseModel):
    """CSO plan options. ``declare_output_types`` asks the CSO to declare each output's data type and format
    from ``labhq/vocab/output_types.yaml`` (#221 todo 2). Off by default until a schema probe on both engines
    and a limited real CLI run pass; off sends today's prompt and schema unchanged, and stored declarations
    stay as they were (nothing is deleted or rewritten)."""

    declare_output_types: bool = False


class PiProfileSettings(BaseModel):
    """The PI's default reference pointers, added to every request unless the request turns them off.

    Private pointers (a local knowledge base, unpublished notes) belong only in the uncommitted config.
    """

    references: list[Reference] = Field(default_factory=list, max_length=20)


class ResearchSettings(BaseModel):
    """Opt-in research contract pilot. Research execution is intentionally disabled in PR 1."""

    enabled: bool = False
    pack_dirs: list[str] = []
    active_packs: list[str] = []  # exact ``id@version`` keys, fixed into the approved plan


class GitHubSettings(BaseModel):
    token_env: str = "GITHUB_TOKEN"  # token is read from this env var on the gateway host, never from YAML
    api_url: str = "https://api.github.com"
    dashboard_url: str | None = None  # link back to the web office in issue comments
    codex_mention: str = "@codex"  # only in the one top-level review request; each mention starts a Codex session


class DevLogSettings(BaseModel):
    enabled: bool = True
    repo: str | None = None
    source_repo: str | None = None  # owner/name used to link the labhq commit in each round
    allow_public: bool = False
    labels: list[str] = Field(default_factory=lambda: ["labhq-round"])
    improvement_notes: bool = False


class ProjectSettings(BaseModel):
    """One research project = one GitHub repo where the lab posts its updates."""

    id: str
    name: str = ""
    repo: str | None = None  # "owner/name"
    branch: str = "main"
    reports_dir: str = "labhq/reports"
    visibility: Literal["private", "public"] = "private"
    allow_public_reports: bool = False  # public repos stay silent unless explicitly allowed
    local_dir: str | None = None  # the project's clone on the runner → agents work there
    issues: bool = True  # one issue per request, with plan / review / report comments
    commit_reports: bool = True  # final report committed to reports_dir
    codex_review: bool = False  # ask Codex to review PRs the team opens
    labels: list[str] = ["labhq"]


class Settings(BaseModel):
    instance: str | None = None
    gateway: GatewaySettings = GatewaySettings()
    runner: RunnerSettings = RunnerSettings()
    engines: EnginesSettings = EnginesSettings()
    bench: BenchSettings = BenchSettings()
    hpc: HpcSettings = HpcSettings()
    policy: PolicySettings = PolicySettings()
    recruit: RecruitSettings = RecruitSettings()
    orchestrator: OrchestratorSettings = OrchestratorSettings()
    plan: PlanSettings = PlanSettings()
    research: ResearchSettings = ResearchSettings()
    pi_profile: PiProfileSettings = PiProfileSettings()
    github: GitHubSettings = GitHubSettings()
    dev_log: DevLogSettings = DevLogSettings()
    projects: list[ProjectSettings] = []
    semantics: Any = None  # semantics-hook: off | shadow, read only by labhq.research.semantics_shadow (#150)
    config_path: str | None = None

    @field_validator("instance")
    @classmethod
    def safe_instance_name(cls, value: str | None) -> str | None:
        import re

        if value is not None and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", value):
            raise ValueError("instance must use 1-64 letters, numbers, underscores or hyphens")
        return value

    def project(self, project_id: str | None) -> ProjectSettings | None:
        return next((p for p in self.projects if p.id == project_id), None) if project_id else None

    def path(self, p: str) -> Path:
        """Expand ~ and $VARS; relative paths are relative to the config file (or cwd)."""
        q = Path(os.path.expandvars(os.path.expanduser(p)))
        if q.is_absolute():
            return q
        base = Path(self.config_path).parent if self.config_path else Path.cwd()
        return (base / q).resolve()

    @classmethod
    def load(cls, path: str | None = None) -> "Settings":
        path = path or os.environ.get("LABHQ_CONFIG")
        data: dict = {}
        if path and Path(path).exists():
            data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        s = cls.model_validate(data)
        if path and Path(path).exists():
            s.config_path = str(Path(path).resolve())
        return s
