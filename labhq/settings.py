from __future__ import annotations

import math
import os
import ntpath
import re
import stat
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any  # semantics-hook
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator

from .intake import Reference
from .test_guard import guard_test_path


class _ConfigLoader(yaml.SafeLoader):
    def construct_mapping(self, node, deep=False):
        result = super().construct_mapping(node, deep=deep)
        for key_node, value_node in node.value:
            if (key_node.value == "precedent_lookup_share"
                    and value_node.tag == "tag:yaml.org,2002:float"):
                result[key_node.value] = value_node.value.replace("_", "")
        return result


def _load_config(text: str):
    return yaml.load(text, Loader=_ConfigLoader)


class GatewaySettings(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8787
    url: str = "ws://127.0.0.1:8787"  # what runners dial (outbound, so no inbound port on your PC/HPC)
    runner_token: str = "change-me-runner"
    client_token: str = "change-me-client"
    event_buffer: int = 2000
    resume_wait_s: int = 300
    # A request whose task has no connected runner waits this long as waiting_for_runner, then fails (PI 점검 R15).
    runner_wait_s: float = Field(default=86400, gt=0)
    state_dir: str = Field(default_factory=lambda: os.environ.get("LABHQ_STATE_DIR", "~/.labhq/state"))
    # Edge or Chrome that prints the PDF report (#584). None: look on PATH and in the standard install folders.
    report_browser: str | None = None


class WslSettings(BaseModel):
    enabled: bool = False
    distro: str = "Ubuntu-22.04"
    user: str | None = None
    id: str = "lab-wsl"
    # Empty: an instance-specific directory on the Linux filesystem, never the Windows mount.
    root: str | None = None
    bioinfo_agent: str = "/mnt/d/bioinfo-agent"
    gateway_url: str | None = None


def _default_step_memory_limit_gb() -> float:
    from .runner.memory_limit import default_step_memory_limit_gb
    return default_step_memory_limit_gb()


class RunnerSettings(BaseModel):
    id: str = "local"
    wsl: WslSettings = Field(default_factory=WslSettings)
    environment: Literal["native", "wsl"] = "native"
    os_account: str | None = None  # the dedicated OS account the runner must run as (docs/runner-account.md)
    max_parallel: int = 4
    consult_parallel: int = Field(default=2, ge=1)
    # Runaway breaker (#59): a signal that stays tripped moves warn -> constrain -> stop, one stage per new observation
    # and at most once per beat. The stop stage halts the task only when breaker_hard_stop is on.
    breaker_hard_stop: bool = False
    breaker_beat_s: float = Field(default=10, gt=0, allow_inf_nan=False)
    workspace_root: str = "~/.labhq/runs"
    disk_warning_free_gb: float = Field(default=10, ge=0, allow_inf_nan=False)
    disk_warning_free_percent: float = Field(default=5, ge=0, le=100, allow_inf_nan=False)
    # Below this much free space on the work drive the runner starts no task until the PI answers a card (#678);
    # 0 turns the card off (the warning above stays).
    disk_fail_free_gb: float = Field(default=2, ge=0, allow_inf_nan=False)
    # While a task runs the runner reads the free space of its drives every this many seconds and halts the task when one
    # falls below disk_fail_free_gb, keeping its session for the PI to resume (#678). 0 turns the watch off.
    disk_watch_s: float = Field(default=30, ge=0, allow_inf_nan=False)
    # Off: the watch only writes a warning to the task log and never halts.
    disk_watch_halt: bool = True
    # Optional folder for multi-GB raw data, outside every step folder: the task briefing names it (#678).
    bulk_data_root: str | None = None
    bundle_max_file_mb: float = Field(default=50, gt=0)
    bundle_max_total_mb: float = Field(default=2048, gt=0)
    bundle_max_files: int = Field(default=5000, ge=1)
    agents_dir: str = "./agents"
    talent_dir: str = "~/.labhq/talent"  # 인재풀: every contract ever hired, kept for rehire
    contract_dir: str | None = None  # active contract roster; default agents_dir/contract, per instance (#303)
    broker_port: int = 8788
    task_timeout_s: int = 6 * 3600
    # Longest owner receipt lifetime the contained-runtime check accepts (issued_at to deadline_at); the owner
    # that issues the receipt must use the same value. 24 h is the hard ceiling so a typo cannot mean forever (#938).
    contained_max_s: int = Field(default=5400, ge=60, le=86400)
    # 0 disables the cap. Default: 60% with a tree cap, 40% for per-process POSIX RLIMIT_AS fallback.
    step_memory_limit_gb: float = Field(
        default_factory=_default_step_memory_limit_gb,
        ge=0,
        allow_inf_nan=False,
    )
    structured_output_retries: int = Field(default=2, ge=0)  # JSON transport repair, separate from result_corrections
    # After the CLI's final turn event, how long its process may take to exit before labhq ends its tree and
    # finishes the step with the result already received (#330).
    exit_grace_s: float = Field(default=45, ge=0)
    # A staff CLI quiet this long gets one warning. On Windows it names pending UAC prompts, which hold a sandboxed
    # Codex shell indefinitely (#382). 0 turns the warning off.
    stall_warn_s: float = Field(default=600, ge=0)
    # After this much silence, end the CLI only when its process tree proves no command is running, so orchestration
    # retries a transient failure (#473). Values below stall_warn_s use stall_warn_s. 0 turns the retry off.
    stall_retry_s: float = Field(default=1200, ge=0)
    job_poll_s: int = 60
    state_dir: str = Field(default_factory=lambda: os.environ.get("LABHQ_STATE_DIR", "~/.labhq/state"))
    outbox_limit: int = 20000
    force_engine: str | None = None  # "mock" runs every agent with the mock engine (demo/tests)
    facilities_support: Literal["unspecified", "required", "optional"] = "unspecified"
    mock_delay_s: float = Field(default=0.05, ge=0)  # only the web demo increases this
    # Read-only roots for `path` references (#36); each project's local_dir also counts.
    reference_roots: list[str] = []
    # A reference folder is listed for links and mounts before it is exposed; past these caps it is refused.
    reference_scan_max_entries: int = Field(default=20000, ge=1)
    reference_scan_max_depth: int = Field(default=16, ge=0)
    output_hash_max_bytes: int = Field(default=512 * 1024 * 1024, ge=0)
    # Input provenance uses the reference walker caps above. Cached hashes do not consume the per-step byte cap.
    input_hash_max_file_bytes: int = Field(default=2 * 1024 * 1024 * 1024, ge=0)
    input_hash_max_total_bytes: int = Field(default=20 * 1024 * 1024 * 1024, ge=0)
    # Windows: staff Python trusts the OS store too, so an institution's TLS inspection root works (9th mock trial).
    system_ca_bundle: bool = True
    # A consult or follow-up lists every entry it could write to (workspace, writable project/upstream/reference
    # folders) before and after the run; past this many it is refused rather than run unchecked (#36).
    read_only_check_max_entries: int = Field(default=50000, ge=1)

    @model_validator(mode="after")
    def validate_wsl(self) -> "RunnerSettings":
        if self.wsl.enabled and self.id == self.wsl.id:
            raise ValueError("Windows and WSL runner.id must differ")
        if self.environment == "wsl":
            from .wsl_paths import shared_path
            self.workspace_root = shared_path(self.workspace_root, "wsl")
        return self


class EngineBin(BaseModel):
    model_config = ConfigDict(extra="forbid")  # a misspelled or unsupported option must not pass silently

    bin: str
    prefix_args: list[str] = []
    extra_args: list[str] = []
    env: dict[str, str] = {}


class IsolatedEngineBin(EngineBin):
    # Staff sessions must not inherit the PI's own CLI setup (hooks, skills, plugins, global instructions).
    # Only engines whose adapter implements it; gemini/antigravity have no flag for it (docs/manual.md '알려진 한계').
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
    r"|qrsub|qrdel|pbs_rsub|pbs_rdel"
    r"|sbatch|srun|salloc|scancel|scrontab|strigger)\b"
    r"|\bscontrol\b(?!(?:[ \t]+-[\w-]+)*[ \t]+(?:show|ping|listpids|version|help)\b)"
)


BASH_RECURSIVE_DELETE_PATTERN = r"\brm\s+-[a-zA-Z]*r[a-zA-Z]*\b"


def _default_bash_ask() -> list[str]:
    return [
        BASH_RECURSIVE_DELETE_PATTERN,  # rm -rf / -fr
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
    # Known non-delegating tools the approval gate allows without a shell, file or MCP rule. File tools are repeated
    # here so an exported policy is self-explanatory; StructuredOutput and ToolSearch were seen in trial records.
    # Claude Code's basic reading/planning tools supplied the rest. BashOutput/KillShell (older Claude Code) and
    # TaskOutput/TaskStop (newer) only read or stop a background shell the gated Bash call already started.
    # Unknown tools ask; native delegation always denies.
    return ["Read", "Glob", "Grep", "LS", "WebSearch", "WebFetch", "TodoWrite", "Skill",
            "StructuredOutput", "ToolSearch", "BashOutput", "KillShell", "TaskOutput", "TaskStop"]


class ApprovalRules(BaseModel):
    hpc_core_hours_threshold: float = 0.0  # 0 → every HPC submission asks the PI
    bash_ask_patterns: list[str] = Field(default_factory=_default_bash_ask)
    auto_allow_tools: list[str] = Field(default_factory=_default_auto_allow)
    timeout_s: int = 3600  # gate cards: tool permission, HPC submit, download
    # Cards that decide the request itself (clarify, question, scope, budget, CP1, CP2, continue, resume). The request
    # waits as waiting_pi meanwhile; an unanswered card ends as *_timed_out, never as a deny (PI 점검 R5).
    pi_decision_timeout_s: int = Field(default=7 * 86400, ge=1)

    @field_validator("auto_allow_tools")
    @classmethod
    def _upgrade_template_list(cls, value: list[str]) -> list[str]:
        """A config written by `labhq init` from the #518 template keeps that exact list; it gets the background-shell
        tools added since (#520 review). A list the PI edited is left as written."""
        if set(value) == _AUTO_ALLOW_518:
            return [*value, *(tool for tool in _BACKGROUND_SHELL_TOOLS if tool not in value)]
        return value


# The auto_allow_tools template #518 published, and what #520 added to the default for configs that still carry it.
_AUTO_ALLOW_518 = frozenset({"Read", "Glob", "Grep", "LS", "WebSearch", "WebFetch", "TodoWrite", "Skill",
                             "StructuredOutput", "ToolSearch"})
_BACKGROUND_SHELL_TOOLS = ("BashOutput", "KillShell", "TaskOutput", "TaskStop")


class BudgetSettings(BaseModel):
    per_task_usd: float = 5.0
    per_request_usd: float = 30.0
    # On by default since #1139 (#862): hold each charged turn's cap before it is sent, so in-flight turns and the
    # last turn cannot pass the request cap on the product's own cost accounting. The hold is cut to what the request
    # still has, so a request budget below per_task_usd is not asked about at its first turn. Not a provider billing
    # guarantee. `false` restores the older check, which only looks at what is already spent.
    reserve_before_dispatch: bool = True

    @model_validator(mode="after")
    def reserved_budgets_are_amounts(self) -> "BudgetSettings":
        if self.reserve_before_dispatch:
            for name in ("per_task_usd", "per_request_usd"):
                value = getattr(self, name)
                if not math.isfinite(value) or value < 0:
                    raise ValueError(f"policy.budget.{name} must be a finite amount >= 0 with reserve_before_dispatch")
            if 0 < self.per_task_usd < 0.01:
                # The Claude CLI writes a call cap in cents: a positive cap below one cent cannot be held (0 is no cap).
                raise ValueError("policy.budget.per_task_usd must be 0 or at least $0.01 with reserve_before_dispatch")
        return self


class BioinfoAgentPolicy(BaseModel):
    """Unattended gates and the public upstream for bioinfo-agent pipeline contributions."""

    hard_stops: list[Literal["data_zone", "budget_cap", "installation", "out_of_scope", "destructive"]] = [
        "data_zone", "budget_cap", "installation",
    ]
    # Off by default (#300): another lab running labhq may not want to contribute upstream. When off the
    # runner sends no pipeline files and the gateway makes no GitHub call for them.
    pipeline_pr: bool = False
    pipeline_repo: str = "ehojune/bioinfo-agent"
    pipeline_base_branch: str = "main"

    @model_validator(mode="after")
    def safe_github_target(self) -> "BioinfoAgentPolicy":
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", self.pipeline_repo):
            raise ValueError("policy.bioinfo_agent.pipeline_repo must be owner/name")
        branch = self.pipeline_base_branch
        if (not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,199}", branch)
                or ".." in branch or "//" in branch or branch.endswith(("/", "."))):
            raise ValueError("policy.bioinfo_agent.pipeline_base_branch is invalid")
        if len(self.hard_stops) != len(set(self.hard_stops)):
            raise ValueError("policy.bioinfo_agent.hard_stops must be unique")
        return self


class PolicySettings(BaseModel):
    data_zones: list[DataZone] = []
    allow_runner_read_restricted: bool = False
    # PI personal files staff must not open while they run under the PI's account (PI decision 2026-10-03).
    # None (key absent) → labhq.private_paths.DEFAULT_HOME_ENTRIES plus labhq's own config and gateway state;
    # [] turns it off. `~` expands on the runner host; paths that do not exist are skipped.
    private_paths: list[str] | None = None
    approvals: ApprovalRules = ApprovalRules()
    budget: BudgetSettings = BudgetSettings()
    bioinfo_agent: BioinfoAgentPolicy = BioinfoAgentPolicy()


class RecruitSettings(BaseModel):
    enabled: StrictBool = True  # explicit False disables hiring and Paper2Agent preparation, not ordinary staff
    agent_id: str = "recruiter"
    permission_mode: str = "auto"  # unattended conversion; see docs/manual.md '파견직 제도' (run inside a container if you can)
    max_budget_usd: float = 25.0
    default_ttl_days: int = 30
    contract_engine: str = "claude_code"
    contract_model: str | None = "sonnet"
    contract_model_tier: Literal["fast", "balanced", "reasoning"] = "balanced"
    skill_source: str = "https://github.com/jmiao24/Paper2Agent"
    # setup-paper2agent installs exactly this commit (40 hex); None installs the latest HEAD and records it (#424).
    skill_commit: str | None = None

    @field_validator("skill_commit")
    @classmethod
    def full_skill_commit(cls, value: str | None) -> str | None:
        if value is not None and not re.fullmatch(r"[0-9a-f]{40}", value):
            raise ValueError("recruit.skill_commit must be a full 40-character lowercase hex commit")
        return value


# A model name labhq passes to an engine CLI as one argument: no leading '-', so it can never read as a flag.
MODEL_NAME_PATTERN = r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}"


class PiLanguageSettings(BaseModel):
    enabled: bool = True
    model: str | None = Field(default="haiku", pattern="^" + MODEL_NAME_PATTERN + "$")
    per_request_usd: float = Field(default=0.50, ge=0, allow_inf_nan=False)
    per_message_usd: float = Field(default=0.05, ge=0.01, multiple_of=0.01, allow_inf_nan=False)
    timeout_s: float = Field(default=30, gt=0, le=120)


class OrchestratorSettings(BaseModel):
    wait_for_clarification: bool = True
    cso_agent: str = "cso"
    chief_of_staff_agent: str | None = "chief_of_staff"
    precedent_agent: str | None = "lit_scout"
    reviewer_agent: str | None = "sci_reviewer"
    solo_agent: str | None = None
    solo_review: bool = False
    # Per-request CSO overrides may select only one of these models. Fable is excluded because its
    # professional-biology policy makes it unsuitable for this role (#272).
    cso_models: list[str] = ["opus", "gpt-6-astra"]
    max_revisions: int = 1
    # A review `revise` may re-plan the unfinished DAG this many times. Failure re-plans use their own cap.
    max_replans: int = Field(default=0, ge=0)
    max_failure_replans: int = Field(default=1, ge=0)
    max_steps: int = 12
    max_branches: int = Field(default=5, ge=1)
    max_branch_steps: int = Field(default=8, ge=1)
    max_parallel_steps: int = 4
    max_wake_cycles: int = 5
    context_chars_per_step: int = 12000
    plan_max_chars: int = Field(default=50_000, ge=1)  # warn; do not clip a valid plan
    step_max_attempts: int = Field(default=2, ge=1)
    step_retry_backoff_s: float = Field(default=0.2, ge=0)
    runner_reconnect_timeout_s: float = Field(default=30, ge=0)
    quota_default_wait_s: float = Field(default=3600, ge=1)
    quota_max_wait_s: float = Field(default=7 * 86400, ge=1)
    login_retry_s: float = Field(default=600, gt=0)
    login_wait_max_s: float = Field(default=86400, gt=0)
    # Wall-clock limit for a whole request, fixed when the request is accepted (#877). 0 turns it off. The clock
    # runs while staff work; time in waiting_pi (PI cards) is added to the deadline, and a request in review_pending
    # or later is never cut. A value below one minute is a typo (minutes written as seconds), so it is refused.
    request_wall_clock_s: int = Field(default=0, ge=0, le=7 * 86400)
    # A plan step may carry its own ``model_tier`` (#625, step 2): carried, capped by the staff member's declared tier
    # and run on that tier's model. Off by default; off drops the field everywhere and changes nothing, so plan
    # hashes and prompts stay what they were. The CSO is not yet told it may choose one (that is a later step).
    step_model_tiers: bool = False

    @field_validator("request_wall_clock_s")
    @classmethod
    def wall_clock_is_off_or_a_real_limit(cls, value: int) -> int:
        if 0 < value < 60:
            raise ValueError("request_wall_clock_s is in seconds: use 0 (off) or at least 60")
        return value

    @model_validator(mode="before")
    @classmethod
    def legacy_replan_cap_applies_to_failures(cls, value: Any) -> Any:
        """A config written before #373 used max_replans for both triggers; preserve that meaning."""
        if isinstance(value, dict) and "max_replans" in value and "max_failure_replans" not in value:
            value = {**value, "max_failure_replans": value["max_replans"]}
        return value

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


class ModelTierRate(BaseModel):
    """Operator-supplied USD range for one model call; blank means unknown."""

    model_config = ConfigDict(extra="forbid")

    min_usd: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    max_usd: float | None = Field(default=None, ge=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def complete_range(self) -> "ModelTierRate":
        if (self.min_usd is None) != (self.max_usd is None):
            raise ValueError("model tier price needs both min_usd and max_usd, or neither")
        if self.min_usd is not None and self.max_usd is not None and self.max_usd < self.min_usd:
            raise ValueError("model tier max_usd must be at least min_usd")
        return self


class ModelTierPrices(BaseModel):
    """One configurable table for dry-run call ranges. No source means every range stays unknown."""

    model_config = ConfigDict(extra="forbid")

    source: str | None = None
    checked_on: date | None = None
    fast: ModelTierRate = Field(default_factory=ModelTierRate)
    balanced: ModelTierRate = Field(default_factory=ModelTierRate)
    reasoning: ModelTierRate = Field(default_factory=ModelTierRate)

    @model_validator(mode="after")
    def sourced_prices(self) -> "ModelTierPrices":
        priced = any(rate.min_usd is not None for rate in (self.fast, self.balanced, self.reasoning))
        if priced and (not self.source or self.checked_on is None):
            raise ValueError("priced model tiers require source and checked_on")
        return self


class ModelTierModels(BaseModel):
    """Which model each tier means on each engine (#625). Empty by default: a tier then names no model, nothing is
    invented, and every agent runs the model its own YAML gives. An agent's explicit ``model`` always wins."""

    model_config = ConfigDict(extra="forbid")

    fast: dict[str, str] = Field(default_factory=dict)
    balanced: dict[str, str] = Field(default_factory=dict)
    reasoning: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def valid_engines_and_models(self) -> "ModelTierModels":
        engines = {"claude_code", "codex", "gemini", "antigravity"}
        for tier in ("fast", "balanced", "reasoning"):
            for engine, model in getattr(self, tier).items():
                if engine not in engines:
                    raise ValueError(f"model_tiers.{tier}: unknown engine {engine!r}; allowed: {', '.join(sorted(engines))}")
                if not re.fullmatch(MODEL_NAME_PATTERN, model):
                    raise ValueError(f"model_tiers.{tier}.{engine}: {model!r} is not a valid model name")
        return self

    def model_for(self, tier: str | None, engine: str | None) -> str | None:
        """The configured model of `tier` on `engine`, or None when the table does not say."""
        row = getattr(self, tier, None) if tier in ("fast", "balanced", "reasoning") else None
        return row.get(engine) if row and engine else None


def models_match(declared: str | None, actual: str | None) -> bool:
    """One model, named by an alias or a full name: ``opus`` is ``claude-opus-5-5``, ``gpt-6-luna`` is not ``gpt-6``.

    Compared case-insensitively without a leading ``claude-``; a name also matches the same name plus a version
    suffix (``-5-5``, ``-4.1``). Anything else, including a prefix that is another model, does not match."""
    if not declared or not actual:
        return False
    one, other = (re.sub(r"^claude-", "", value.strip().casefold()) for value in (declared, actual))
    if one == other:
        return True
    short, long = sorted((one, other), key=len)
    return bool(re.fullmatch(re.escape(short) + r"(?:-\d+(?:\.\d+)?)+", long))


class PiProfileSettings(BaseModel):
    """The PI's default reference pointers, added to every request unless the request turns them off.

    Private pointers (a local knowledge base, unpublished notes) belong only in the uncommitted config.
    """

    references: list[Reference] = Field(default_factory=list, max_length=20)


class LabSettings(BaseModel):
    """The lab itself. ``scope`` is what the CSO judges each general request against (#36); empty uses the
    one-PI bioinformatics default in ``labhq.orchestrator.cso.DEFAULT_LAB_SCOPE``."""

    scope: str | None = None


class ResearchSettings(BaseModel):
    """Opt-in research contract pilot."""

    # A misspelled key (`evidence_checkpiont`) used to load silently and leave the lane half on (readiness R1).
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    semantic_report: bool = True  # request bundle + published run/ device audit (#737)
    evidence_checkpoint: bool = False  # execute approved research steps and stop at CP2
    final_checkpoint: bool = False  # PI report review accepts a frozen final draft (CP4)
    # The science reviewer first answers the original request alone, sealed before the first step (#889).
    blind_draft: bool = False
    live_source_check: bool = False  # Crossref/NCBI verification before CP2 and in labhq verify
    live_source_contact: str = ""  # optional maintainer email for API User-Agent/mailto etiquette
    live_source_timeout_s: float = Field(default=10.0, gt=0)
    live_source_deadline_s: float = Field(default=60.0, gt=0)
    result_corrections: int = Field(default=2, ge=0)  # retry only invalid result JSON; never rerun the step
    finish_turns: int = Field(default=1, ge=0)  # a step past its turn limit continues once with half the limit
    # After a review "revise" or a CP2 revise, a request continues through a new CP1 at most this many times in all;
    # 0 ends at revise.
    revise_continuations: int = Field(default=2, ge=0)
    pack_dirs: list[str] = []
    active_packs: list[str] = []  # exact ``id@version`` keys, fixed into the approved plan


class LiteratureSettings(BaseModel):
    """Opt-in access to a PI-prepared institution session and the bounded PDF request fallback.

    The browser profile is passed only to Edge/Chrome. labhq never reads or copies its cookies or credentials.
    """

    model_config = ConfigDict(extra="forbid")

    institution_enabled: bool = False
    institution_profile: str | None = None
    institution_proxy: str | None = None
    institution_request_interval_s: float = Field(default=30.0, ge=0)
    institution_daily_limit: int = Field(default=5, ge=1, le=100)
    institution_tdm_allowed: bool = False
    external_model_allowed: bool = False
    pdf_request_timeout_s: int = Field(default=2 * 3600, ge=1, le=7 * 86400)
    # Base64 relay stays below the runner WebSocket's 64 MiB frame cap even at the configured maximum.
    pdf_max_mb: float = Field(default=40.0, gt=0, le=45)

    @model_validator(mode="after")
    def institution_route_is_explicit(self) -> "LiteratureSettings":
        if self.institution_enabled and not (self.institution_profile or self.institution_proxy):
            raise ValueError("literature.institution_enabled needs institution_profile or institution_proxy")
        if self.institution_proxy:
            from urllib.parse import urlsplit
            check = self.institution_proxy.replace("{url}", "https://doi.org/10.1000/example")
            parsed = urlsplit(check)
            if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
                raise ValueError("literature.institution_proxy must be an HTTPS prefix without credentials")
        return self


class WikiSettings(BaseModel):
    """A user LLM-wiki folder linked as a local reference whose notes keep their provenance (#827). Off by default.

    The root must be inside ``runner.reference_roots``; it is only read, never changed. ``external_model_allowed``
    must be true before a later phase attaches any tool that hands note text to a staff model.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    root: str | None = None
    external_model_allowed: bool = False
    extensions: list[Literal[".md", ".txt"]] = [".md", ".txt"]
    stale_days: int = Field(default=365, ge=1)
    max_files: int = Field(default=5000, ge=1, le=100000)
    max_file_bytes: int = Field(default=2 * 1024 * 1024, ge=1, le=16 * 1024 * 1024)

    @model_validator(mode="after")
    def enabled_needs_a_root(self) -> "WikiSettings":
        if self.enabled and not (self.root and self.root.strip()):
            raise ValueError("wiki.enabled needs wiki.root")
        return self


class AnnotSettings(BaseModel):
    """labhq_annot lookups (#435). The AlphaGenome API key lives only in this file, which `labhq init` writes for the
    runner account alone (POSIX 0600, Windows without inherited access); the config holds the location, never the
    key. No key file: the alphagenome tool is not registered."""

    alphagenome_key_file: str = "~/.labhq/secrets/alphagenome_api_key"
    # Unpaywall requires a contact email. It is used only as the API query parameter and is never put in results,
    # query logs or cache keys. Empty keeps the tool visible but explicitly disabled (#744).
    unpaywall_email: str = ""
    # Opt-in request-wide cap on the outbound HTTP requests labhq_annot sends (retries and redirects included),
    # each admitted by the runner before it is sent (#858). None keeps lookups uncounted; 0 refuses every lookup.
    # See runner/lookup_admission.py for what one attempt is.
    request_lookup_limit: int | None = Field(default=None, ge=0, le=10000)
    precedent_lookup_share: Decimal | None = Field(default=Decimal("0.5"), ge=0, le=1, allow_inf_nan=False)


class ResultStorageSettings(BaseModel):
    """What the published result repository records about its authors (#1104)."""
    # Names for CITATION.cff. Empty by default: no author is invented, and without a name no CITATION.cff is written.
    authors: list[str] = []


class GitHubSettings(BaseModel):
    ask_storage: bool = False  # general requests opt in; research requests always ask
    auto_publish_results: bool = False  # otherwise review the editor draft in the web office
    progress_branch: str = "labhq/progress"  # optional per-step edition, separate from the approved final branch
    # Optional warning threshold only. Report figures and PDFs are never shrunk to meet it (#690).
    edition_pdf_max_mb: float | None = Field(default=None, gt=0)
    token_env: str = "GITHUB_TOKEN"  # token is read from this env var on the gateway host, never from YAML
    # Without the variable, the gateway reads the token from this owner-only file, which `labhq init` writes when the PI
    # pastes one (#687). Its own folder: an owner-only folder holds one secret (tools/annot_keys.py).
    token_file: str = "~/.labhq/secrets-github/github_token"
    api_url: str = "https://api.github.com"
    dashboard_url: str | None = None  # link back to the web office in issue comments
    codex_mention: str = "@codex"  # only in the one top-level review request; each mention starts a Codex session

    @field_validator("progress_branch")
    @classmethod
    def valid_progress_branch(cls, value: str) -> str:
        if (not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,199}", value)
                or ".." in value or "//" in value or value.endswith(("/", "."))):
            raise ValueError("github.progress_branch is invalid")
        return value


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


class SupervisorSettings(BaseModel):
    """The per-role supervisor `labhq up` starts (#589). It always records how its gateway or runner ended."""

    # On: a gateway or runner that ended on its own with a non-zero code is started again. Off by default, so a crash
    # stays visible instead of being papered over. Stopping it with `down` or the web shutdown never restarts it.
    restart: bool = False
    max_restarts: int = Field(default=5, ge=1)  # within restart_window_s; the next end is final and the supervisor exits
    restart_window_s: float = Field(default=600, gt=0)
    backoff_s: float = Field(default=5, ge=0)  # wait before the first restart; doubles for each one still in the window
    backoff_cap_s: float = Field(default=120, ge=0)


class Settings(BaseModel):
    instance: str | None = None
    gateway: GatewaySettings = Field(default_factory=GatewaySettings)
    runner: RunnerSettings = Field(default_factory=RunnerSettings)
    supervisor: SupervisorSettings = Field(default_factory=SupervisorSettings)
    engines: EnginesSettings = EnginesSettings()
    bench: BenchSettings = BenchSettings()
    hpc: HpcSettings = HpcSettings()
    policy: PolicySettings = PolicySettings()
    recruit: RecruitSettings = RecruitSettings()
    orchestrator: OrchestratorSettings = OrchestratorSettings()
    pi_language: PiLanguageSettings = Field(default_factory=PiLanguageSettings)
    plan: PlanSettings = PlanSettings()
    model_tier_prices: ModelTierPrices = Field(default_factory=ModelTierPrices)
    model_tiers: ModelTierModels = Field(default_factory=ModelTierModels)
    lab: LabSettings = LabSettings()
    research: ResearchSettings = ResearchSettings()
    literature: LiteratureSettings = LiteratureSettings()
    wiki: WikiSettings = WikiSettings()
    pi_profile: PiProfileSettings = PiProfileSettings()
    github: GitHubSettings = GitHubSettings()
    result_storage: ResultStorageSettings = Field(default_factory=ResultStorageSettings)
    annot: AnnotSettings = AnnotSettings()
    dev_log: DevLogSettings = DevLogSettings()
    projects: list[ProjectSettings] = []
    semantics: Any = None  # semantics-hook: off | shadow | ab, read only by labhq.research.semantics_shadow (#150)
    config_path: str | None = None
    # Set only in the staff copy (write_staff_config): the folder its relative paths still resolve from.
    config_base: str | None = None

    @field_validator("instance")
    @classmethod
    def safe_instance_name(cls, value: str | None) -> str | None:
        import re

        if value is not None and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", value):
            raise ValueError("instance must use 1-64 letters, numbers, underscores or hyphens")
        return value

    def project(self, project_id: str | None) -> ProjectSettings | None:
        return next((p for p in self.projects if p.id == project_id), None) if project_id else None

    def base_dir(self) -> Path:
        if self.config_base:
            return Path(self.config_base)
        return Path(self.config_path).parent if self.config_path else Path.cwd()

    def path(self, p: str) -> Path:
        """Expand ~ and $VARS; relative paths are relative to the config file (or cwd)."""
        q = Path(os.path.expandvars(os.path.expanduser(p)))
        guard_test_path(q if q.is_absolute() else self.base_dir() / q)
        if q.is_absolute():
            return q
        return (self.base_dir() / q).resolve()

    @classmethod
    def load(cls, path: str | None = None) -> "Settings":
        path = path or os.environ.get("LABHQ_CONFIG")
        data: dict = {}
        if path:
            guard_test_path(Path(path))
        if path and not Path(path).is_file():
            raise FileNotFoundError(f"설정 파일이 없습니다: {path}")
        if path:
            data = _load_config(Path(path).read_text(encoding="utf-8")) or {}
        s = cls.model_validate(data)
        if path:
            s.config_path = str(Path(path).resolve())
        return s


STAFF_REDACTED = ("client_token", "runner_token")


def _is_link(info: os.stat_result) -> bool:
    """A symlink, or on Windows any reparse point (a junction is not a symlink there)."""
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & reparse)


def write_staff_config(settings: Settings, directory: Path) -> str | None:
    """The config a staff process gets as LABHQ_CONFIG: the runner's file with the gateway tokens blanked.

    The MCP tools read policy, HPC and broker settings from it and no gateway credential. A client token in a file
    staff can read lets them call every PI REST action, the follow-up the A2 action layer runs included (#149 결정
    16). ``config_base`` keeps relative paths resolving from the original folder.

    Every call writes a fresh file under a name the runner draws (O_EXCL), in a folder that must not be a link or
    junction. No existing file is trusted, so a staff process that swapped an earlier copy for a link to the PI's
    file hands nothing to the next task. The caller deletes the copy when the task ends. Raises when the file cannot
    be read or written, or the folder is a link; the caller then refuses the task rather than hand over the
    original."""
    if not settings.config_path:
        return None
    import tempfile

    guard_test_path(Path(settings.config_path))
    guard_test_path(directory)
    data = _load_config(Path(settings.config_path).read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError("config is not a mapping")
    gateway = data.get("gateway") if isinstance(data.get("gateway"), dict) else {}
    data["gateway"] = {**gateway, **{key: "" for key in STAFF_REDACTED}}
    data["config_base"] = str(settings.base_dir())
    text = yaml.safe_dump(data, allow_unicode=True, sort_keys=True)
    directory.mkdir(parents=True, exist_ok=True)
    info = os.lstat(directory)
    if _is_link(info) or not stat.S_ISDIR(info.st_mode):
        raise OSError(f"staff config folder is a link or not a folder: {directory}")
    fd, name = tempfile.mkstemp(prefix="staff-config-", suffix=".yaml", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            out.write(text)
    except BaseException:
        Path(name).unlink(missing_ok=True)
        raise
    return name
