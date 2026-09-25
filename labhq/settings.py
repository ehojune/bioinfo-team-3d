from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field


class GatewaySettings(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8787
    url: str = "ws://127.0.0.1:8787"  # what runners dial (outbound, so no inbound port on your PC/HPC)
    runner_token: str = "change-me-runner"
    client_token: str = "change-me-client"
    event_buffer: int = 2000


class RunnerSettings(BaseModel):
    id: str = "local"
    max_parallel: int = 4
    workspace_root: str = "~/.labhq/runs"
    agents_dir: str = "./agents"
    talent_dir: str = "~/.labhq/talent"  # 인재풀: every contract ever hired, kept for rehire
    broker_port: int = 8788
    task_timeout_s: int = 6 * 3600
    job_poll_s: int = 60
    force_engine: str | None = None  # "mock" runs every agent with the mock engine (demo/tests)


class EngineBin(BaseModel):
    bin: str
    extra_args: list[str] = []
    env: dict[str, str] = {}


class EnginesSettings(BaseModel):
    claude_code: EngineBin = EngineBin(bin="claude")
    codex: EngineBin = EngineBin(bin="codex")
    gemini: EngineBin = EngineBin(bin="gemini")
    antigravity: EngineBin = EngineBin(bin="agy")


class SgeSettings(BaseModel):
    pe: str = "smp"  # parallel environment name differs per cluster (smp, threads, openmp, make…)
    mem_resource: str = "h_vmem"  # or mem_free
    mem_per_slot: bool = True  # h_vmem is usually per slot → total mem is divided by cores
    runtime_resource: str = "h_rt"  # set "" if your cluster rejects h_rt


class PbsSettings(BaseModel):
    resource_template: str = "nodes=1:ppn={cores},mem={mem},walltime={walltime}"  # Torque
    pro: bool = False  # PBS Pro: finished jobs need `qstat -x`; use "select=1:ncpus={cores}:mem={mem}" template


class HpcSettings(BaseModel):
    scheduler: Literal["sge", "pbs", "mock", "none"] = "sge"
    ssh_host: str | None = None  # run qsub/qstat on a login node via ssh (workspace must be on shared FS)
    user: str | None = None  # None → current account
    default_queue: str | None = None
    sge: SgeSettings = SgeSettings()
    pbs: PbsSettings = PbsSettings()
    command_timeout_s: int = 60


class DataZone(BaseModel):
    path: str
    level: Literal["restricted", "internal", "public"] = "restricted"
    note: str = ""


def _default_bash_ask() -> list[str]:
    return [
        r"\brm\s+-[a-zA-Z]*r[a-zA-Z]*f|\brm\s+-[a-zA-Z]*f[a-zA-Z]*r",  # rm -rf / -fr
        r"\bsudo\b",
        r"curl[^|]*\|\s*(ba|z)?sh",
        r"wget[^|]*\|\s*(ba|z)?sh",
        r"\bqsub\b|\bsbatch\b|\bqdel\b",  # use the approval-gated hpc_* tools instead
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


class OrchestratorSettings(BaseModel):
    cso_agent: str = "cso"
    chief_of_staff_agent: str | None = "chief_of_staff"
    reviewer_agent: str | None = "sci_reviewer"
    max_revisions: int = 1
    max_steps: int = 12
    max_parallel_steps: int = 4
    max_wake_cycles: int = 5
    context_chars_per_step: int = 12000


class GitHubSettings(BaseModel):
    token_env: str = "GITHUB_TOKEN"  # token is read from this env var on the gateway host, never from YAML
    api_url: str = "https://api.github.com"
    dashboard_url: str | None = None  # link back to the web office in issue comments
    codex_mention: str = "@codex"  # always used when a comment addresses Codex in a PR, even in replies


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
    gateway: GatewaySettings = GatewaySettings()
    runner: RunnerSettings = RunnerSettings()
    engines: EnginesSettings = EnginesSettings()
    hpc: HpcSettings = HpcSettings()
    policy: PolicySettings = PolicySettings()
    recruit: RecruitSettings = RecruitSettings()
    orchestrator: OrchestratorSettings = OrchestratorSettings()
    github: GitHubSettings = GitHubSettings()
    projects: list[ProjectSettings] = []
    config_path: str | None = None

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
            data = yaml.safe_load(Path(path).read_text()) or {}
        s = cls.model_validate(data)
        if path and Path(path).exists():
            s.config_path = str(Path(path).resolve())
        return s
