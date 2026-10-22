"""First-install configuration. Authentication remains a manual step."""

from __future__ import annotations

import getpass
import os
import re
import secrets
import shutil
import sys
from importlib import resources
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

import yaml

from . import bioinfo_link, disk_space, doctor, github_token, hpc_consult, init_defaults
from .adapters.base import _resolve_command
from .runner.codex_sandbox import codex_command, powershell_executable, setup_hint
from .settings import HpcSettings, LiteratureSettings, Settings
from .test_guard import guard_test_path
from .tools import annot_keys
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


def _interactive() -> bool:
    """A person at a terminal: the key prompt never reads from a pipe or a test's captured stdin."""
    try:
        return bool(sys.stdin) and sys.stdin.isatty()
    except (AttributeError, ValueError, OSError):
        return False


def _alphagenome_key(settings: Settings) -> None:
    """Ask once per `labhq init` for the AlphaGenome API key when none is stored (PI decision 2026-10-06).
    Enter skips: the alphagenome tool is then not registered, and the next `labhq init` asks again. The key is read
    without echo and never printed; neither is the file's location."""
    if not _interactive() or annot_keys.has_key(settings):
        return
    key = getpass.getpass("AlphaGenome API 키가 있으면 붙여 넣으세요, 없으면 Enter (입력은 화면에 보이지 않음): ").strip()
    if not key:
        print("AlphaGenome: 건너뜀 (도구 미등록, 나중에 labhq init을 다시 실행하면 다시 묻습니다)")
        return
    if not annot_keys.valid_key(key):
        print("AlphaGenome: 키 형식이 아니라 저장하지 않았습니다 (공백 없는 한 덩어리여야 합니다)")
        return
    try:
        annot_keys.write_key(annot_keys.key_path(settings), key, _key_keep_out(settings))
    except annot_keys.KeyFileError as exc:
        raise InitError(f"AlphaGenome 키를 저장하지 못했습니다: {exc}") from None
    print("AlphaGenome: 키 저장 (이 계정만 읽을 수 있는 파일, 값과 경로 출력 생략)")


def _key_keep_out(settings: Settings) -> list[Path]:
    keep_out = [settings.path(settings.runner.state_dir), settings.path(settings.runner.workspace_root),
                settings.path(settings.gateway.state_dir)]
    if settings.config_path:
        keep_out.append(Path(settings.config_path).parent)
    return keep_out


def _github_token(settings: Settings) -> None:
    """Ask once per `labhq init` for a GitHub token when none is set (PI request 2026-10-16, #687). Enter skips: results
    then stay local. The screen lists the permissions a result repository needs before asking; the doctor run right
    after checks what the token may do. The token is read without echo and kept in an owner-only file, never in the
    config, and neither the value nor the file's location is printed."""
    if not _interactive():
        return
    source = github_token.token_source(settings)
    if source == "env":  # the variable wins over any stored file, so asking would change nothing
        print(f"GitHub: 환경 변수 {settings.github.token_env}의 토큰을 씁니다(값 출력 생략). 권한은 아래 doctor가 확인합니다")
        return
    if source == "file":  # an expired or revoked token is replaced here, as doctor's hint says (PR #700 review)
        print("GitHub: 저장된 토큰이 있습니다(값 출력 생략). 만료·취소됐거나 권한을 바꾼 새 토큰이면 붙여 넣으세요.")
    else:
        print("GitHub 토큰(선택): 요청 결과를 GitHub 저장소로 정리하고 이슈로 알릴 때 씁니다. 없으면 결과는 로컬에만 저장합니다.")
    print("  필요한 권한 — " + github_token.NEEDED_TEXT)
    print("  만드는 곳 — " + github_token.TOKEN_SETTINGS_URL)
    question = ("새 GitHub 토큰을 붙여 넣으세요, 그대로 두려면 Enter (입력은 화면에 보이지 않음): " if source == "file"
                else "GitHub 토큰을 붙여 넣으세요, 없으면 Enter (입력은 화면에 보이지 않음): ")
    token = getpass.getpass(question).strip()
    if not token:
        if source == "file":
            print("GitHub: 저장된 토큰을 그대로 씁니다. 권한은 아래 doctor가 확인합니다")
        else:
            print(f"GitHub: 건너뜀 (결과는 로컬에 저장. 나중에 labhq init을 다시 실행하거나 {settings.github.token_env} "
                  "환경 변수를 설정하세요)")
        return
    if not github_token.valid_token(token):
        print("GitHub: 토큰 형식이 아니라 저장하지 않았습니다 (github_pat_ 또는 ghp_로 시작하는 한 덩어리여야 합니다)")
        return
    try:
        github_token.save_token(settings, token, _key_keep_out(settings))
    except annot_keys.KeyFileError as exc:
        raise InitError(f"GitHub 토큰을 저장하지 못했습니다: {exc}") from None
    print("GitHub: 토큰 저장 (이 계정만 읽을 수 있는 파일, 값과 경로 출력 생략). 권한은 아래 doctor가 확인합니다")


# Institution literature access (#826). Only what the existing settings already hold is kept: a library proxy prefix
# (literature.institution_proxy) and the Unpaywall contact email (annot.unpaywall_email). The institution session
# stays off, and init neither connects, logs in nor fetches anything. Passwords, cookies and keys are never asked.
_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s.]+")
_PROXY_QUERY_KEYS = {"url", "qurl"}
# Known session/ticket shapes in a path segment: CAS tickets (ST-…) and segments named after a session or ticket.
_SESSION_SEGMENT = re.compile(r"^(?:ST|PT|PGT|TGT)-\w|(?:^|[-_.])(?:jsessionid|phpsessid|sessionid|session|ticket|token)"
                              r"(?:$|[-_.=])", re.IGNORECASE)
_WOS = re.compile(r"web\s*of\s*science|wos|clarivate|nexus", re.IGNORECASE)


def _proxy_prefix(value: str) -> str | None:
    """A library proxy prefix the institution route can use, else None: `…?url=` (the article URL is appended) or one
    `{url}` slot as the `url`/`qurl` value or the last path segment. HTTPS with a plain host (no userinfo, no `{url}`
    there, a valid port), no fragment, no query but that article slot. Known session shapes are refused: any `;`
    matrix parameter (`;jsessionid=…`), CAS-style `ST-…` segments and segments named session/ticket/token. An
    arbitrary token inside a path cannot be recognised, so the prompt asks for the prefix the library publishes."""
    value = value.strip()
    if not value:
        return None
    try:
        LiteratureSettings(institution_proxy=value)
        parts = urlsplit(value)
        parts.port  # noqa: B018 - raises ValueError on an out-of-range port
    except ValueError:
        return None
    if (parts.scheme != "https" or not parts.hostname or any(mark in parts.netloc for mark in "@{}")
            or parts.fragment or ";" in value or any(_SESSION_SEGMENT.search(s) for s in parts.path.split("/"))):
        return None
    pairs = parse_qsl(parts.query, keep_blank_values=True)
    if any(key.lower() not in _PROXY_QUERY_KEYS or text not in ("", "{url}") for key, text in pairs):
        return None
    slots = value.count("{url}")
    if slots == 1:
        in_query = any(text == "{url}" for _key, text in pairs)
        return value if in_query or (parts.path.endswith("/{url}") and not pairs) else None
    return value if slots == 0 and pairs and value.endswith("=") else None


def _contact_email(value: str) -> str | None:
    value = value.strip()
    return value if len(value) <= 254 and _EMAIL.fullmatch(value) else None


def _institution_answers(new_config: bool = True) -> dict:
    """Ask a person at the terminal how they reach subscription papers. Every question takes Enter for 모름·없음·나중에.
    Returns the values the existing settings can hold; the institution name and services only shape the advice.
    Neither the address nor the email is printed back. Only a new config saves them (`new_config`)."""
    print("기관 문헌 접근(선택): 구독 논문을 어떻게 보는지 묻습니다. 모르거나 없거나 나중에 하려면 Enter로 넘어가세요.")
    print("  비밀번호·cookie·API 키는 묻지 않고, 지금 기관 사이트에 접속하거나 로그인하지 않습니다.")
    answers: dict = {"proxy": None, "email": None}
    name = input("소속 기관 이름 (없거나 모르면 Enter): ").strip()
    if not name:
        print("기관 문헌 접근: 건너뜀. 공개·합법 OA 문헌 조회로 시작하며, 나중에 labhq init이나 설정에서 추가할 수 있습니다.")
    else:
        print("기관 이름은 설정에 저장하지 않습니다(아래 안내에만 씁니다).")
        raw = input("기관 도서관 프록시 주소 — 도서관이 안내하는 'https://…login?url=' 형태의 접두만 붙여 넣으세요 "
                    "(로그인 뒤 주소창 주소·도서관 홈페이지 주소이거나 모르면 Enter): ").strip()
        if raw:
            answers["proxy"] = _proxy_prefix(raw)
            if answers["proxy"] is None:
                print("프록시 주소: 받지 않았습니다. https, 계정 정보 없음, 기사 주소 자리('url=' 또는 '{url}') 하나, "
                      "알려진 세션·ticket 형식(';jsessionid', 'ST-…' 등) 없음일 때만 받습니다.")
        services = input("이용 중인 문헌 서비스 (예: Web of Science, Scopus, 도서관 검색; 없으면 Enter): ").strip()
        if services and _WOS.search(services):
            print("Web of Science: labhq에는 Web of Science Research Assistant·Nexus Connect 전용 연결이 없어 "
                  "자동으로 연결하지 않습니다. 이 기관의 Clarivate 계약·연결 주소·권한은 확인하지 않았고, "
                  "기관이 제공하는 접속 조건은 기관 도서관 안내로 확인하세요.")
        elif services:
            print("문헌 서비스: 적은 내용은 저장하지 않습니다. 연결 방법은 기관 도서관 안내를 확인하세요.")
    raw = input("Unpaywall 연락용 email (선택, 합법 무료 사본 위치를 찾을 때 Unpaywall API에만 보냄; 없으면 Enter): ")
    if raw.strip():
        answers["email"] = _contact_email(raw)
        if answers["email"] is None:
            print("Unpaywall email: 형식이 아니라 받지 않았습니다.")
    if answers["proxy"] and new_config:
        print("기관 프록시: 새 설정에 저장하되 기관 세션은 꺼 둡니다. 기관·출판사의 TDM과 외부 모델 전송 조건을 확인한 뒤 "
              "설정의 literature.institution_enabled·institution_tdm_allowed·external_model_allowed를 직접 true로 "
              "바꾸세요. 프록시 로그인은 사용자가 브라우저에서 직접 합니다. 연결은 아직 시험하지 않았습니다.")
    return answers


def _apply_institution(data: dict, answers: dict) -> None:
    """Put the accepted answers into a new config's data; the institution session stays as the template has it."""
    if answers.get("proxy"):
        data.setdefault("literature", {})["institution_proxy"] = answers["proxy"]
    if answers.get("email"):
        data.setdefault("annot", {})["unpaywall_email"] = answers["email"]


def _institution_existing(settings: Settings) -> None:
    """A re-run keeps the config untouched and saves nothing: show what is set and, if something is missing, name the
    settings to fill by hand. The values typed here are not printed back."""
    literature, email = settings.literature, settings.annot.unpaywall_email
    session = "켜짐" if literature.institution_enabled else "꺼짐"
    print(f"기관 문헌 접근(현재 설정): 기관 프록시 {'있음' if literature.institution_proxy else '없음'}, "
          f"기관 세션 {session}, Unpaywall email {'있음' if email else '없음'}")
    if literature.institution_proxy and email:
        return
    answers = _institution_answers(new_config=False)
    keys = [name for key, name in (("proxy", "literature.institution_proxy"), ("email", "annot.unpaywall_email"))
            if answers.get(key)]
    if keys:
        print("기존 설정 파일은 바꾸지 않았고 아무것도 저장하지 않았습니다. 쓰려면 방금 입력한 값을 설정 파일의 "
              + "·".join(keys) + f"에 직접 넣으세요. 넣어도 기관 세션 설정은 그대로이며, 현재 기관 세션은 {session}입니다.")


def _absolute(path: str) -> str:
    """The folder as an absolute path. The claude_code adapter reads a relative plugin folder from each task's own
    work folder, so a path saved as typed (`../bioinfo-agent`) would stop connecting as soon as init's cwd is gone."""
    return os.path.abspath(os.path.expandvars(os.path.expanduser(path)))


def _workspace_root(current: str, yes: bool, instance: str | None) -> str:
    """Suggest another volume without silently changing --yes or existing settings."""
    limits = Settings().runner
    try:
        system = disk_space.usage(Path(os.environ.get("SystemDrive", Path.home().anchor) + "/"))
        if disk_space.low(system, limits.disk_warning_free_gb, limits.disk_warning_free_percent) or system.total < 128 * disk_space.GIB:
            print("시스템 드라이브의 용량 또는 여유 공간이 작습니다. 작업 폴더는 여유 있는 데이터 드라이브를 권합니다.")
            candidates = []
            for drive in disk_space.data_drives():
                try:
                    space = disk_space.usage(drive)
                    if space.free > system.free and not disk_space.low(
                            space, limits.disk_warning_free_gb, limits.disk_warning_free_percent):
                        candidates.append((space.free, drive))
                except (OSError, ValueError):
                    continue
            for free, drive in sorted(candidates, key=lambda item: item[0], reverse=True):
                folder = drive / ".labhq" / (instance or "") / "runs"
                print(f"작업 폴더 후보: {folder.as_posix()} (여유 {free / disk_space.GIB:.1f} GiB)")
    except (OSError, ValueError):
        pass
    answer = _ask("작업 폴더 runner.workspace_root (드라이브를 포함한 경로)", current, yes).strip().strip('"')
    return current if answer == current else _absolute(answer)


def _bioinfo_agent(explicit: str | None, yes: bool) -> str | None:
    """The folder for engines.claude_code.env.BIOINFO_AGENT_DIR, or None: bioinfo-agent stays off (#613).

    Order: --bioinfo-agent PATH, an existing BIOINFO_AGENT_DIR, the common places (D:/bioinfo-agent, ~/bioinfo-agent),
    a WSL path (/mnt/d/bioinfo-agent). When nothing is found and a person is answering, ask. Paths are not echoed."""
    if explicit:
        if not bioinfo_link.is_plugin_dir(explicit):
            raise InitError("--bioinfo-agent 폴더에서 bioinfo-agent plugin(.claude-plugin/plugin.json과 "
                            "skills/bioinfo-analyze)을 찾지 못했습니다.")
        print("bioinfo-agent: --bioinfo-agent 경로로 연결 (경로 출력 생략)")
        return _absolute(explicit)
    found = bioinfo_link.detect()
    if found:
        path, source = found
        print(f"bioinfo-agent: {bioinfo_link.SOURCE_LABEL[source]}에서 찾음 (경로 출력 생략)")
        if not yes:
            answer = input("이 위치로 연결할까요? [Enter: 예, 다른 경로를 적거나 n: 연결 안 함] ").strip().strip('"')
            path = None if answer.lower() in ("n", "no") else (answer or path)
    else:
        path = _ask("bioinfo-agent 폴더를 찾지 못했습니다. 경로를 적거나 비워 두세요(연결 안 함)", "", yes).strip().strip('"')
    if path and not bioinfo_link.is_plugin_dir(path):
        print("주의: 그 폴더에서 plugin(.claude-plugin/plugin.json)을 찾지 못했습니다. 경로를 확인하세요 (doctor가 다시 알려 줌).")
    return _absolute(path) if path else None


def _dev_log_repo(existing: Settings | None, yes: bool) -> str | None:
    """dev_log.repo is a person's repository, so there is no default: ask once, or leave it empty (local rounds only)."""
    if existing is not None and existing.dev_log.repo:
        print("dev_log.repo: 기존 설정의 값을 유지 (이름 출력 생략)")
        return existing.dev_log.repo
    answer = _ask("개발 기록(dev log)을 올릴 비공개 GitHub 저장소 owner/name (없으면 비워 두세요)", "", yes).strip()
    if answer and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9_.-]+", answer):
        print("dev_log.repo: owner/name 형식이 아니라 비워 둡니다.")
        answer = ""
    if answer:
        print("dev_log.repo: 설정 (비공개 저장소인지는 doctor가 확인)")
        return answer
    print("dev_log.repo: 비워 둠 — 개발 기록은 이 PC에만 남습니다. GitHub에도 남기려면 설정의 dev_log.repo에 "
          "비공개 저장소 owner/name을 적으세요.")
    return None


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
        executable = powershell_executable(resolved, env)
        return "$env:CODEX_HOME = Join-Path $HOME '.labhq/codex-staff'; & " + executable + " login"
    return 'CODEX_HOME="$HOME/.labhq/codex-staff" codex login'


def _used_instance_ports() -> set[int]:
    used: set[int] = set()
    root = Path.home() / ".labhq"
    guard_test_path(root)
    for path in root.glob("*/labhq.yaml"):
        guard_test_path(path)
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
        force: bool = False, instance: str | None = None, bioinfo_agent: str | None = None) -> dict:
    target = Path(config or os.environ.get("LABHQ_CONFIG") or "config/labhq.yaml").resolve()
    guard_test_path(target)
    if instance:
        print("주의: 엔진 로그인과 사용량 한도는 모든 labhq 인스턴스가 공유합니다.")
        if force:
            raise InitError("인스턴스 설정은 덮어쓰지 않습니다. --force 없이 기존 설정을 사용하세요.")
    staff_home: Path | None = None
    claude_home: Path | None = None
    try:
        existing = Settings.load(str(target)) if target.exists() else None
    except (OSError, ValueError, yaml.YAMLError):
        if not force:
            raise
        existing = None
    if existing is not None and not force:
        print("기존 설정과 token을 보존합니다. 교체하려면 --force를 사용하세요.")
        settings = existing
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
        data["runner"]["workspace_root"] = _workspace_root(
            existing.runner.workspace_root if existing else data["runner"]["workspace_root"], yes, instance)
        trial = _hpc_consult(hpc, scheduler, dry_run)
        if _is_windows():
            policy = data.setdefault("policy", {})
            policy["data_zones"] = [z for z in policy.get("data_zones", [])
                                    if z.get("level", "restricted") != "restricted"]
            print("Windows: restricted data_zones 제외")
        init_defaults.ensure_workspace_zone(data)  # semantics hashes outputs only inside a public or internal zone
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
        # Private paths close the PI's ~/.claude to staff, and Claude reads its saved long output back from its
        # config folder; Claude staff get their own (#298 ⑤).
        if (data.get("policy") or {}).get("private_paths") != [] and any((home / ".claude").is_dir()
                                                                        for home in homes):
            claude_home = Path.home() / ".labhq" / "claude-staff"
            home_var = "USERPROFILE" if _is_windows() else "HOME"
            engines.setdefault("claude_code", {}).setdefault("env", {})["CLAUDE_CONFIG_DIR"] = \
                "${" + home_var + "}/" + doctor.CLAUDE_STAFF_DIR
            print("개인 Claude 설정(~/.claude) 발견: 직원 전용 ~/.labhq/claude-staff 사용")
        plugin = _bioinfo_agent(bioinfo_agent, yes)
        if plugin:
            engines.setdefault("claude_code", {}).setdefault("env", {})["BIOINFO_AGENT_DIR"] = plugin
            print("engines.claude_code.env.BIOINFO_AGENT_DIR: 설정 (경로 출력 생략)")
        else:
            print(bioinfo_link.NOT_CONNECTED + "bioinfo-agent 직원(Nextflow 분석)을 쓸 수 없습니다. " + bioinfo_link.FIX)
        repo = _dev_log_repo(existing, yes)
        if repo:
            data.setdefault("dev_log", {})["repo"] = repo
        if existing is not None:
            # Consent belongs to its target too; --force must not redirect an existing opt-in.
            bioinfo = data.setdefault("policy", {}).setdefault("bioinfo_agent", {})
            for key in ("pipeline_pr", "pipeline_repo", "pipeline_base_branch"):
                bioinfo[key] = getattr(existing.policy.bioinfo_agent, key)
        else:
            bioinfo = data.setdefault("policy", {}).setdefault("bioinfo_agent", {})
            print(f"새 pipeline 코드를 공개 upstream {bioinfo['pipeline_repo']}에 PR로 올릴지 선택합니다.")
            print("fork 자동 생성은 아직 지원하지 않습니다. 해당 저장소의 쓰기 권한이 필요합니다.")
            bioinfo["pipeline_pr"] = _ask("공개 pipeline 기여에 동의하나요? (y/N)", "n", yes).lower() == "y"
            print("policy.bioinfo_agent.pipeline_pr: " + str(bioinfo["pipeline_pr"]).lower())
        print("켜 둔 기본 설정: 연구 모드(CP1·CP2, 분야 팩 " + ", ".join(data["research"]["active_packs"]) + "), "
              "의미 모델 ab(actions confirm), 산출물 종류 선언. 끄려면 설정의 research.enabled·semantics.mode·"
              "plan.declare_output_types를 바꾸세요.")
        if not yes and not dry_run and _interactive():  # a person at the terminal; Enter skips each question (#826)
            _apply_institution(data, _institution_answers())
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
            if claude_home:
                claude_home.mkdir(parents=True, exist_ok=True)
            print("설정 저장 완료 (token과 로컬 경로 출력 생략)")
            if trial:  # after the save, so the draft survives an interrupted or failed trial
                _hpc_trial(settings, yes)
        if staff_home:
            print("로그인은 직접 실행하세요: " + _login_command(settings))
            if _is_windows() and settings.engines.codex.windows_sandbox == "elevated":
                env = {**os.environ, **settings.engines.codex.env}
                command = codex_command(settings, env)
                print("로그인 뒤 elevated sandbox 준비: " + setup_hint(staff_home, command, env))
        if claude_home:
            print("Claude 직원 로그인은 직접 실행하세요(새 창에서): " + doctor.claude_login_command(_is_windows()))
    if not yes and not dry_run:
        if existing is not None and not force and _interactive():
            _institution_existing(settings)
        _alphagenome_key(settings)
        _github_token(settings)
    # Initial config creation also precedes the clean-install mock setup. Installation is an explicit
    # doctor --fix step; init must stay offline and must not write skills into the user's home.
    result = doctor.collect(settings, dry_run=dry_run, require_roster=True, require_paper2agent=False)
    print(doctor.render(result))
    summary = result["summary"]
    print(f"doctor: ok {summary['ok']}, warn {summary['warn']}, fail {summary['fail']}")
    return result
