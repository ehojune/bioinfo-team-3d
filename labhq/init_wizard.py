"""First-install configuration. Authentication remains a manual step."""

from __future__ import annotations

import os
import secrets
import shutil
from importlib import resources
from pathlib import Path

import yaml

from . import doctor
from .adapters.base import _resolve_command
from .settings import Settings


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


def _is_windows() -> bool:
    return os.name == "nt"


def _ask(prompt: str, default: str, yes: bool) -> str:
    if yes:
        return default
    # Paths are not echoed in prompts or reports.
    answer = input(prompt + " [Enter: 기본값] ").strip()
    return answer or default


def _login_command(settings: Settings) -> str:
    """Use shell home variables so neither the user name nor home path is printed."""
    if _is_windows():
        env = {**os.environ, **settings.engines.codex.env}
        try:
            resolved = _resolve_command([settings.engines.codex.bin], env, "codex")
        except (OSError, ValueError):
            resolved = ["codex"]  # doctor will report the missing/unsupported executable.
        executable = resolved[0]
        local = env.get("LOCALAPPDATA")
        if local:
            try:
                relative = Path(executable).relative_to(Path(local) / "OpenAI" / "Codex" / "bin")
                executable = '"$env:LOCALAPPDATA/OpenAI/Codex/bin/' + relative.as_posix() + '"'
            except ValueError:
                executable = "codex"
        else:
            executable = "codex"
        return "$env:CODEX_HOME = Join-Path $HOME '.labhq/codex-staff'; & " + executable + " login"
    return 'CODEX_HOME="$HOME/.labhq/codex-staff" codex login'


def run(config: str | None = None, *, yes: bool = False, dry_run: bool = False,
        force: bool = False) -> dict:
    target = Path(config or os.environ.get("LABHQ_CONFIG") or "config/labhq.yaml").resolve()
    staff_home: Path | None = None
    if target.exists() and not force:
        print("기존 설정과 token을 보존합니다. 교체하려면 --force를 사용하세요.")
        settings = Settings.load(str(target))
    else:
        data = yaml.safe_load(_template_text())
        agents_dir = _find_agents_dir(target, data.setdefault("runner", {}).get("agents_dir", "../agents"))
        if agents_dir is None:
            raise InitError("활성 직원 roster를 찾지 못했습니다. agents/core가 있는 checkout에서 다시 실행하세요.")
        data["runner"]["agents_dir"] = str(agents_dir)
        gateway = data.setdefault("gateway", {})
        for key in ("runner_token", "client_token"):
            gateway[key] = "<generated at write>" if dry_run else secrets.token_urlsafe(32)
        print("gateway runner/client token: 새 무작위 값 (출력 생략)")
        hpc = data.setdefault("hpc", {})
        if not all(shutil.which(tool) for tool in ("qsub", "qstat")):
            print("qsub/qstat을 모두 찾지 못했습니다. hpc.scheduler: none을 제안합니다.")
            scheduler = _ask("hpc.scheduler (none/sge/pbs/mock)", "none", yes)
            if scheduler not in ("none", "sge", "pbs", "mock"):
                raise ValueError("invalid scheduler")
            hpc["scheduler"] = scheduler
        print("hpc.scheduler: " + hpc["scheduler"])
        if _is_windows():
            policy = data.setdefault("policy", {})
            policy["data_zones"] = [z for z in policy.get("data_zones", [])
                                    if z.get("level", "restricted") != "restricted"]
            print("Windows: restricted data_zones 제외")
        engines = data.setdefault("engines", {})
        codex = engines.setdefault("codex", {})
        codex["bin"] = "auto"
        print("engines.codex.bin: auto (Windows 앱 최신 폴더, 그 외 PATH)")
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
        if staff_home:
            print("로그인은 직접 실행하세요: " + _login_command(settings))
    result = doctor.collect(settings, dry_run=dry_run, require_roster=True)
    print(doctor.render(result))
    summary = result["summary"]
    print(f"doctor: ok {summary['ok']}, warn {summary['warn']}, fail {summary['fail']}")
    return result
