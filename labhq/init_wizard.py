"""First-install configuration. Authentication remains a manual step."""

from __future__ import annotations

import os
import secrets
import shutil
from importlib import resources
from pathlib import Path

import yaml

from . import doctor, hpc_consult
from .adapters.base import _resolve_command
from .runner.codex_sandbox import codex_command, powershell_executable, setup_hint
from .settings import HpcSettings, Settings
from .tools.scheduler import COMMANDS as SCHEDULER_COMMANDS, Scheduler
from .util import free_port


class InitError(ValueError):
    """A safe, user-facing initialization error."""


def _template_text() -> str:
    template = resources.files("labhq").joinpath("config").joinpath("labhq.example.yaml")
    return template.read_text(encoding="utf-8")


def _find_agents_dir(target: Path, configured: str) -> Path | None:
    relative = Path(os.path.expandvars(os.path.expanduser(configured)))
    candidates = [
        target.parent / relative,
        Path(__file__).resolve().parents[1] / "agents",
        Path.cwd() / "agents",
    ]
    seen: set[Path] = set()
    for candidate in candidates:
        candidate = candidate.resolve()
        if candidate in seen:
            continue
        seen.add(candidate)
        try:
            probe = Settings.model_validate({"runner": {"agents_dir": str(candidate)}})
            if doctor._roster(probe):
                return candidate
        except (OSError, ValueError, yaml.YAMLError):
            continue
    return None


def _has(*tools: str) -> bool:
    return all(shutil.which(tool) for tool in tools)


def _detect_scheduler() -> str | None:
    """Scheduler family from its own tools or environment; None when absent or ambiguous."""
    slurm = _has("sbatch", "sinfo")
    qsub = _has("qsub", "qstat")
    found = []
    if slurm:
        found.append("slurm")
    if qsub and (os.environ.get("SGE_ROOT") or shutil.which("qconf")):
        found.append("sge")
    # Slurm's Torque wrappers also install qsub/qstat/pbsnodes: pbsnodes alone is not PBS there.
    if qsub and (os.environ.get("PBS_HOME") or os.environ.get("PBS_EXEC") or (shutil.which("pbsnodes") and not slurm)):
        found.append("pbs")
    return found[0] if len(found) == 1 else None


def _is_windows() -> bool:
    return os.name == "nt"


def _ask(prompt: str, default: str, yes: bool) -> str:
    if yes:
        return default
    # Paths are not echoed in prompts or reports.
    answer = input(prompt + " [Enter: 기본값] ").strip()
    return answer or default


def _hpc_query(argv: list[str]):
    """One read-only cluster query for the HPC consult (tests replace it with fixture output)."""
    return hpc_consult.run_query(argv)


def _hpc_backend(hpc: HpcSettings) -> Scheduler:
    return Scheduler(hpc)


def _hpc_consult(hpc: dict, scheduler: str, dry_run: bool) -> bool:
    """Show the `hpc:` draft from read-only queries; True when a trial job may follow."""
    if scheduler == "none":
        print(hpc_consult.NO_CLUSTER)
        return False
    if scheduler not in SCHEDULER_COMMANDS:
        return False
    if not _has(*SCHEDULER_COMMANDS[scheduler]):
        print(f"{'/'.join(SCHEDULER_COMMANDS[scheduler])}가 이 PC에 없어 HPC 상담을 건너뜁니다. "
              "제출하는 노드에서 init을 돌리거나 hpc:를 직접 채우세요.")
        return False
    if dry_run:
        print("dry-run: HPC 상담 조회와 시험 잡을 건너뜁니다.")
        return False
    draft, notes = hpc_consult.draft(scheduler, hpc_consult.survey(scheduler, _hpc_query))
    for key, value in draft.items():
        if isinstance(value, dict):
            hpc.setdefault(key, {}).update(value)
        else:
            hpc[key] = value
    print("HPC 설정 초안(읽기 전용 조회 결과, 설정에 넣음, 고쳐 써도 됨):")
    print(yaml.safe_dump({"hpc": draft}, allow_unicode=True, sort_keys=False).rstrip())
    for note in notes:
        print("- " + note)
    return True


def _hpc_trial(settings: Settings, yes: bool) -> None:
    if yes:
        print("시험 잡은 PI 승인이 필요해 --yes에서는 묻지 않고 건너뜁니다.")
        return

    def confirm(command: str) -> bool:
        return _ask(command + "\n시험 잡을 제출할까요? (y/N)", "n", False).strip().lower() in ("y", "yes")

    result = hpc_consult.trial_job(settings.hpc, settings.path(settings.runner.workspace_root) / "_hpc_trial",
                                   confirm, policy=settings.policy, backend=_hpc_backend(settings.hpc))
    job = f" (job {result['job_id']})" if result.get("job_id") else ""
    print(f"시험 잡: {result['message']}{job}")


def _login_command(settings: Settings) -> str:
    """Use shell home variables so neither the user name nor home path is printed."""
    if _is_windows():
        env = {**os.environ, **settings.engines.codex.env}
        try:
            resolved = _resolve_command([settings.engines.codex.bin], env, "codex")
        except (OSError, ValueError):
            resolved = ["codex"]  # doctor will report the missing/unsupported executable.
        executable = powershell_executable(resolved[0], env)
        return "$env:CODEX_HOME = Join-Path $HOME '.labhq/codex-staff'; & " + executable + " login"
    return 'CODEX_HOME="$HOME/.labhq/codex-staff" codex login'


def _used_instance_ports() -> set[int]:
    used: set[int] = set()
    for path in (Path.home() / ".labhq").glob("*/labhq.yaml"):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            used.update(int(value) for value in (
                data.get("gateway", {}).get("port"), data.get("runner", {}).get("broker_port")) if value)
        except (OSError, ValueError, TypeError, yaml.YAMLError):
            continue
    return used


def _unused_port(used: set[int]) -> int:
    while True:
        port = free_port()
        if port not in used:
            used.add(port)
            return port


def run(config: str | None = None, *, yes: bool = False, dry_run: bool = False,
        force: bool = False, instance: str | None = None) -> dict:
    target = Path(config or os.environ.get("LABHQ_CONFIG") or "config/labhq.yaml").resolve()
    if instance:
        print("주의: 엔진 로그인과 사용량 한도는 모든 labhq 인스턴스가 공유합니다.")
        if force:
            raise InitError("인스턴스 설정은 덮어쓰지 않습니다. --force 없이 기존 설정을 사용하세요.")
    staff_home: Path | None = None
    if target.exists() and not force:
        print("기존 설정과 token을 보존합니다. 교체하려면 --force를 사용하세요.")
        settings = Settings.load(str(target))
    else:
        data = yaml.safe_load(_template_text())
        if instance:
            root = f"~/.labhq/{instance}"
            used = _used_instance_ports()
            gateway = data.setdefault("gateway", {})
            runner = data.setdefault("runner", {})
            gateway_port = _unused_port(used)
            gateway.update({"port": gateway_port, "url": f"ws://127.0.0.1:{gateway_port}",
                            "state_dir": f"{root}/state/gateway"})
            runner.update({"id": instance, "broker_port": _unused_port(used),
                           "state_dir": f"{root}/state/runner", "workspace_root": f"{root}/runs",
                           "talent_dir": f"{root}/talent", "contract_dir": f"{root}/contract"})
            data["instance"] = instance
        agents_dir = _find_agents_dir(target, data.setdefault("runner", {}).get("agents_dir", "../agents"))
        if agents_dir is None:
            raise InitError("활성 직원 roster를 찾지 못했습니다. agents/core가 있는 checkout에서 다시 실행하세요.")
        data["runner"]["agents_dir"] = str(agents_dir)
        gateway = data.setdefault("gateway", {})
        for key in ("runner_token", "client_token"):
            gateway[key] = "<generated at write>" if dry_run else secrets.token_urlsafe(32)
        print("gateway runner/client token: 새 무작위 값 (출력 생략)")
        hpc = data.setdefault("hpc", {})
        if not (_has("qsub", "qstat") or _has("sbatch", "sinfo")):
            print("qsub/qstat도 sbatch/sinfo도 찾지 못했습니다. hpc.scheduler: none을 제안합니다.")
            default = "none"
        else:
            # SGE and PBS both ship qsub/qstat, Slurm may too; a wrong family fails only at the first job.
            default = _detect_scheduler()
            if default is None and yes:
                raise InitError("스케줄러 도구는 있지만 SGE·PBS·Slurm 중 하나로 정할 수 없습니다. "
                                "--yes 없이 실행해 hpc.scheduler를 고르세요.")
            print(f"스케줄러 도구 발견: {default or '종류 미확인'}")
        scheduler = _ask("hpc.scheduler (none/sge/pbs/slurm/mock)", default or "", yes)
        if scheduler not in ("none", "sge", "pbs", "slurm", "mock"):
            raise InitError("hpc.scheduler는 none, sge, pbs, slurm, mock 중 하나여야 합니다.")
        hpc["scheduler"] = scheduler
        if scheduler == "pbs":
            print("PBS Pro면 hpc.pbs.pro: true로 바꾸세요(Torque는 그대로).")
        if scheduler == "slurm":
            print("partition은 hpc.default_queue, 계정·QOS는 hpc.slurm.sbatch_args에 더하세요.")
        print("hpc.scheduler: " + hpc["scheduler"])
        trial = _hpc_consult(hpc, scheduler, dry_run)
        if _is_windows():
            policy = data.setdefault("policy", {})
            policy["data_zones"] = [z for z in policy.get("data_zones", [])
                                    if z.get("level", "restricted") != "restricted"]
            print("Windows: restricted data_zones 제외")
        engines = data.setdefault("engines", {})
        codex = engines.setdefault("codex", {})
        codex["bin"] = "auto"
        print("engines.codex.bin: auto (Windows 앱 폴더 중 codex.exe가 있고 판본이 가장 높은 곳, 그 외 PATH)")
        homes = {Path.home(), *(Path(h) for h in (os.environ.get("HOME"), os.environ.get("USERPROFILE")) if h)}
        if any((home / ".codex" / name).is_file() for home in homes
               for name in ("AGENTS.md", "AGENTS.override.md")):
            staff_home = Path.home() / ".labhq" / "codex-staff"
            home_var = "USERPROFILE" if _is_windows() else "HOME"
            codex.setdefault("env", {})["CODEX_HOME"] = "${" + home_var + "}/.labhq/codex-staff"
            print("개인 Codex 지침 발견: 직원 전용 ~/.labhq/codex-staff 사용")
        plugin = _ask("bioinfo-agent checkout 경로 (기본값: BIOINFO_AGENT_DIR, 없으면 생략)",
                      os.environ.get("BIOINFO_AGENT_DIR", ""), yes)
        if plugin:
            engines.setdefault("claude_code", {}).setdefault("env", {})["BIOINFO_AGENT_DIR"] = plugin
            print("engines.claude_code.env.BIOINFO_AGENT_DIR: 설정 (경로 출력 생략)")
        else:
            print("BIOINFO_AGENT_DIR: 생략 (필요할 때 설정)")
        # Validate the complete plan before creating anything. Relative paths follow the target config.
        settings = Settings.model_validate(data)
        settings.config_path = str(target)
        if dry_run:
            print("dry-run: 설정 파일과 직원 홈을 쓰지 않습니다.")
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            # Exclusive creation preserves a config that appeared while the user answered prompts.
            with target.open("w" if force else "x", encoding="utf-8") as out:
                if os.name != "nt":
                    os.chmod(target, 0o600)
                yaml.safe_dump(data, out, allow_unicode=True, sort_keys=False)
            if staff_home:
                staff_home.mkdir(parents=True, exist_ok=True)
            print("설정 저장 완료 (token과 로컬 경로 출력 생략)")
            if trial:  # after the save, so the draft survives an interrupted or failed trial
                _hpc_trial(settings, yes)
        if staff_home:
            print("로그인은 직접 실행하세요: " + _login_command(settings))
            if _is_windows() and settings.engines.codex.windows_sandbox == "elevated":
                env = {**os.environ, **settings.engines.codex.env}
                command = codex_command(settings, env)
                print("로그인 뒤 elevated sandbox 준비: " + setup_hint(staff_home, command[0] if command else None, env))
    result = doctor.collect(settings, dry_run=dry_run, require_roster=True)
    print(doctor.render(result))
    summary = result["summary"]
    print(f"doctor: ok {summary['ok']}, warn {summary['warn']}, fail {summary['fail']}")
    return result
