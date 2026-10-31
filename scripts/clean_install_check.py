#!/usr/bin/env python3
"""Rehearse the documented install and one mock public-data-shaped request."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import zipfile
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path


for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")


class CheckFailure(RuntimeError):
    def __init__(self, stage: str, detail: str):
        super().__init__(detail)
        self.stage = stage
        self.detail = detail


_RESERVED_PORTS = {8787, 55921}  # PI instance and its runner; clean-install must never borrow them
_RECEIPT: ContextVar = ContextVar("clean_install_receipt", default=None)
_RECEIPT_REPLACE_RETRY = os.name == "nt"


class Receipt:
    """Local evidence only: commands and logs can contain absolute paths."""

    def __init__(self, path: Path, root: Path):
        self.path = path
        self.root = root
        self.data = {"schema_version": 1, "status": "RUNNING", "work_dir": str(root),
                     "started_at": datetime.now(timezone.utc).isoformat(), "steps": []}
        self.write()

    def write(self):
        payload = json.dumps(self.data, ensure_ascii=False, indent=2) + "\n"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n", delete=False,
                                             prefix=".labhq-receipt-", suffix=".tmp", dir=self.path.parent) as stream:
                temporary = Path(stream.name)
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            # Match labhq.util.replace_file without importing the package before installation.
            for attempt in range(20):
                try:
                    os.replace(temporary, self.path)
                    break
                except PermissionError:
                    if not _RECEIPT_REPLACE_RETRY or attempt == 19:
                        raise
                    time.sleep(0.05)
        finally:
            if temporary is not None:
                active_error = sys.exc_info()[0] is not None
                try:
                    temporary.unlink(missing_ok=True)
                except BaseException:
                    if not active_error:
                        raise

    def record(self, stage, status, *, stdout=None, stderr=None, **facts):
        row = {"stage": stage, "status": status, **facts}
        index = len(self.data["steps"]) + 1
        self.data["steps"].append(row)
        for name, value in (("stdout", stdout), ("stderr", stderr)):
            if value is not None:
                path = self.root / "logs" / f"{index:02d}.{name}.log"
                path.parent.mkdir(parents=True, exist_ok=True)
                if isinstance(value, bytes):
                    value = value.decode("utf-8", errors="replace")
                path.write_text(value, encoding="utf-8")
                row[name] = str(path.relative_to(self.root))
        self.write()


def _record(stage, status, **facts):
    receipt = _RECEIPT.get()
    if receipt is not None:
        try:
            receipt.record(stage, status, **facts)
        except BaseException as exc:
            detail = _one_line(str(exc) or type(exc).__name__)
            receipt.data.setdefault("evidence_errors", []).append({"stage": stage, "detail": detail})
            if status != "FAIL":
                if not isinstance(exc, Exception):
                    raise
                raise CheckFailure(stage, "증거 기록 실패: " + detail) from None


def _one_line(value: object, limit: int = 500) -> str:
    lines = [line.strip() for line in str(value or "").splitlines() if line.strip()]
    text = " | ".join(lines[-3:]) if lines else "출력 없음"
    return text[-limit:]


def _run(
    stage: str,
    command: list[str] | str,
    *,
    cwd: Path,
    env: dict[str, str],
    input_text: str | None = None,
    timeout: float = 600,
) -> str:
    began = time.monotonic()
    try:
        done = subprocess.run(
            command,
            cwd=cwd,
            env=env,
            input=input_text,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        _record(stage, "FAIL", command=command, cwd=str(cwd),
                stdout=getattr(exc, "stdout", None) or "", stderr=getattr(exc, "stderr", None) or str(exc),
                elapsed_seconds=round(time.monotonic() - began, 3), error=type(exc).__name__)
        detail = getattr(exc, "stderr", None) or getattr(exc, "stdout", None) or str(exc)
        raise CheckFailure(stage, _one_line(detail)) from None
    _record(stage, "FAIL" if done.returncode else "PASS", command=command, cwd=str(cwd),
            stdout=done.stdout, stderr=done.stderr, returncode=done.returncode,
            elapsed_seconds=round(time.monotonic() - began, 3))
    if done.returncode:
        raise CheckFailure(stage, f"exit {done.returncode}: {_one_line(done.stderr or done.stdout)}")
    print(f"PASS {stage}")
    return done.stdout


def _expect(stage: str, condition: bool, detail: str) -> None:
    _record(stage, "PASS" if condition else "FAIL", detail="" if condition else detail)
    if not condition:
        raise CheckFailure(stage, detail)
    print(f"PASS {stage}")


def _startup_journal(path: Path) -> dict:
    """Project existing launch evidence before scratch cleanup; never print raw logs."""
    try:
        with path.open(mode="rb") as stream:
            stream.seek(0, 2)
            start = max(0, stream.tell() - 16384)
            stream.seek(start)
            raw = stream.read(16384)
        if start:
            raw = raw.partition(b"\n")[2]
    except FileNotFoundError:
        return {"journal": "missing", "events": []}
    except OSError:
        return {"journal": "unreadable", "events": []}
    enums = {
        "role": ("gateway-supervisor", "runner-supervisor"),
        "status": ("attempt", "denied", "spawn_failed", "unverified", "cleanup_failed", "direct_reaped",
                   "job_verified", "recorded", "wmi_attempt", "wmi_unverified", "wmi_verified", "wmi_failed",
                   "wmi_control_cleanup_failed"),
        "phase": ("provider_create", "process_open", "process_identity", "receipt_wait", "identity_verify",
                  "job_verify", "ack_write"),
        "reason": ("provider_timeout", "provider_protocol", "protocol_error", "native_error", "provider_subprocess",
                   "create_failed", "invalid_returned_pid", "receipt_timeout", "identity_mismatch", "job_unverified",
                   "system_directory_unknown", "native_or_protocol", "request_too_large"),
        "error": ("BrokerFailure", "TimeoutExpired", "CalledProcessError", "SubprocessError", "PermissionError",
                  "FileNotFoundError", "TimeoutError", "OSError", "ValueError", "TypeError", "OtherError"),
        "trace_status": ("absent", "partial", "phases_complete", "invalid_or_partial"),
    }

    def facts(row):
        safe = {key: row[key] for key, allowed in enums.items() if row.get(key) in allowed}
        for key in ("code", "returncode", "elapsed_ms"):
            value = row.get(key)
            valid = (type(value) in (int, float) and 0 <= value <= 60000) if key == "elapsed_ms" else (
                type(value) is int and -2**32 <= value <= 2**32)
            if valid:
                safe[key] = value
        return safe

    def count(value):  # a strict bounded non-negative integer; never a bool, float or text
        return type(value) is int and 0 <= value <= 2**32

    result = {"journal": "tail_only" if start else "present", "events": []}
    for line in raw.splitlines()[-8:]:
        try:
            row = json.loads(line)
        except (ValueError, UnicodeError, RecursionError):
            row = None
        if not isinstance(row, dict):
            result["events"].append({"journal": "malformed"})
            continue
        if row.get("role") not in enums["role"]:
            continue
        safe = facts(row)
        if count(row.get("denied_reads")):  # the parent's receipt reads held elsewhere (#844)
            safe["denied_reads"] = row["denied_reads"]
        provider = row.get("provider")
        if isinstance(provider, dict):
            safe["provider"] = facts(provider)
            # What reached the provider's stdout, by kind and size only: never its content (#844).
            if provider.get("stdout") in ("bytes", "missing", "invalid_type"):
                safe["provider"]["stdout"] = provider["stdout"]
            if count(provider.get("stdout_bytes")):
                safe["provider"]["stdout_bytes"] = provider["stdout_bytes"]
            phases = provider.get("phases")
            if isinstance(phases, list):
                valid = []
                for index, step in enumerate(phases[:7]):
                    if not isinstance(step, dict) or step.get("phase") != (
                            "powershell_started", "stdin_open", "header_read", "input_read", "input_accepted",
                            "create_entered", "create_returned")[index]:
                        break
                    elapsed = facts(step).get("elapsed_ms")
                    if elapsed is None or (valid and elapsed < valid[-1]["elapsed_ms"]):
                        break
                    valid.append({"phase": step["phase"], "elapsed_ms": elapsed})
                safe["provider"]["phases"] = valid
        result["events"].append(safe)
    while len(json.dumps(result, ensure_ascii=True)) > 4096:
        result["events"].pop(0)
        result["journal"] = "tail_only"
    return result


def _start_services(root: Path, checkout: Path, env: dict[str, str]) -> None:
    try:
        _run("gateway와 runner 시작", _labhq_command(checkout, "-c", "config/labhq.yaml", "up"),
             cwd=checkout, env=env, timeout=120)
    except CheckFailure:
        try:
            facts = _startup_journal(root / "state/gateway/logs/detached-launch.jsonl")
            print("STARTUP_DIAGNOSTIC " + json.dumps(facts, ensure_ascii=True), file=sys.stderr)
        except BaseException as exc:
            receipt = _RECEIPT.get()
            if receipt is not None:
                receipt.data["startup_diagnostic_error"] = _one_line(str(exc) or type(exc).__name__)
            pass  # Diagnostics must never replace the actual startup failure.
        raise


def _free_port() -> int:
    while True:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = int(sock.getsockname()[1])
        if port not in _RESERVED_PORTS:
            return port


def _labhq_command(checkout: Path, *args: str) -> list[str] | str:
    if os.name == "nt":
        # cmd /s needs one outer quote pair around the quoted batch path and its arguments.
        # Passing that as a list would add MSVC backslash escapes before cmd sees it.
        shell = subprocess.list2cmdline([os.environ.get("COMSPEC", "cmd.exe")])
        command = f'"{checkout / "labhq.cmd"}" {subprocess.list2cmdline(list(args))}'
        return f'{shell} /d /s /v:off /c "{command}"'
    return [str(checkout / "labhq.sh"), *args]


def _venv_python(checkout: Path) -> Path:
    return checkout / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _process_images() -> list[tuple[int, Path]]:
    """Best-effort active PID/image pairs without adding a clean-install dependency."""
    if os.name != "nt":
        found: list[tuple[int, Path]] = []
        proc = Path("/proc")
        if not proc.is_dir():
            return found
        for row in proc.iterdir():
            if not row.name.isdigit():
                continue
            try:
                argv0 = (row / "cmdline").read_bytes().split(b"\0", 1)[0].decode(errors="replace")
                image = Path(argv0) if argv0 else (row / "exe").resolve(strict=True)
            except (OSError, UnicodeError):
                continue
            found.append((int(row.name), image))
        return found

    powershell = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    query = (
        "$ErrorActionPreference='Stop'; "
        "$rows=@(Get-CimInstance Win32_Process -Filter \"Name='python.exe' OR Name='pythonw.exe'\" | "
        "Select-Object ProcessId,ExecutablePath); ConvertTo-Json -Compress -InputObject $rows"
    )
    done = subprocess.run([str(powershell), "-NoProfile", "-NonInteractive", "-Command", query],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20, check=False)
    if done.returncode:
        raise OSError("Windows process 목록을 읽지 못함: " + _one_line(done.stderr or done.stdout))
    try:
        rows = json.loads(done.stdout or "[]")
    except ValueError:
        raise OSError("Windows process 목록 JSON을 읽지 못함") from None
    if isinstance(rows, dict):
        rows = [rows]
    return [(int(row["ProcessId"]), Path(row["ExecutablePath"])) for row in rows
            if isinstance(row, dict) and row.get("ExecutablePath")]


def _launch_process_names(state_dir: Path) -> dict[int, str]:
    names: dict[int, str] = {}
    for path in state_dir.glob("labhq-*.pid"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            pid = int(record["pid"])
        except (OSError, ValueError, TypeError, KeyError):
            continue
        names[pid] = path.stem.removeprefix("labhq-")
    return names


def _venv_python_processes(checkout: Path, names: dict[int, str]) -> list[str]:
    venv = os.path.normcase(str((checkout / ".venv").resolve()))
    remaining = []
    for pid, image in _process_images():
        normalized = os.path.normcase(str(image.resolve()))
        if normalized != venv and not normalized.startswith(venv + os.sep):
            continue
        label = names.get(pid, image.name)
        remaining.append(f"{label} ({image.name} PID {pid})")
    return remaining


def _isolated_env(root: Path, checkout: Path) -> dict[str, str]:
    task_home = root / "home"
    task_home.mkdir()
    scratch = root / "tmp"
    scratch.mkdir()
    env = os.environ.copy()
    for name in list(env):
        upper = name.upper()
        # Git Bash exports MSYSTEM and friends. With them set and PATH narrowed to System32, the mingw64 git.exe it
        # puts first on PATH dies at startup (0xC0000005) in "새 clone" (#1216). The check is isolated from the shell
        # anyway, so the shell's own variables are dropped like the operator's.
        if upper.startswith(("MSYS", "MINGW")) or upper in {"EXEPATH", "ORIGINAL_PATH", "ORIGINAL_TEMP", "ORIGINAL_TMP"}:
            env.pop(name)
            continue
        if (upper.startswith(("LABHQ_", "GIT_")) and not upper.startswith("LABHQ_TEST_")) or (
            upper.startswith(("OPENAI_", "ANTHROPIC_", "CODEX_", "CLAUDE_", "GEMINI_", "BIOINFO_", "GH_", "GITHUB_", "PIP_"))
            or upper.endswith(("_API_KEY", "_TOKEN")) or upper in {
                "PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "ANTHROPIC_AUTH_TOKEN", "OPENAI_API_KEY",
                "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN",
            }
        ):
            env.pop(name)
    env.update(
        {
            "HOME": str(task_home),
            "USERPROFILE": str(task_home),
            "APPDATA": str(task_home / "AppData/Roaming"),
            "LOCALAPPDATA": str(task_home / "AppData/Local"),
            "XDG_CONFIG_HOME": str(task_home / ".config"),
            "XDG_CACHE_HOME": str(task_home / ".cache"),
            "XDG_DATA_HOME": str(task_home / ".local/share"),
            "XDG_STATE_HOME": str(task_home / ".local/state"),
            "LABHQ_HOME": str(task_home / ".labhq"),
            "CODEX_HOME": str(task_home / ".codex"),
            "CLAUDE_CONFIG_DIR": str(task_home / ".claude"),
            "LABHQ_STATE_DIR": str(root / "state"),
            "TEMP": str(scratch),
            "TMP": str(scratch),
            "TMPDIR": str(scratch),
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONNOUSERSITE": "1",
            "PIP_CONFIG_FILE": os.devnull,
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
        }
    )
    if os.name == "nt":
        drive, tail = os.path.splitdrive(str(task_home))
        env["HOMEDRIVE"] = drive
        env["HOMEPATH"] = tail
        system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
        env["PATH"] = os.pathsep.join(
            [str(checkout / ".venv/Scripts"), str(system_root / "System32"), str(system_root)]
        )
    else:
        env["PATH"] = os.pathsep.join([str(checkout / ".venv/bin"), "/usr/local/bin", "/usr/bin", "/bin"])
    return env


def _snapshot(paths: list[Path]) -> dict[Path, tuple[bool, int | None]]:
    result: dict[Path, tuple[bool, int | None]] = {}
    for path in paths:
        resolved = path.expanduser().resolve()
        result[resolved] = (resolved.exists(), resolved.stat().st_mtime_ns if resolved.exists() else None)
    return result


def _patch_mock_config(python: Path, checkout: Path, root: Path, env: dict[str, str]) -> None:
    patch_env = env.copy()
    patch_env.update(
        {
            "CHECK_ROOT": str(root),
            "CHECK_GATEWAY_PORT": str(_free_port()),
            "CHECK_BROKER_PORT": str(_free_port()),
        }
    )
    code = """
import os
from pathlib import Path
import yaml

root = Path(os.environ["CHECK_ROOT"])
path = Path("config/labhq.yaml")
data = yaml.safe_load(path.read_text(encoding="utf-8"))
gateway_port = int(os.environ["CHECK_GATEWAY_PORT"])
data["gateway"].update({
    "host": "127.0.0.1",
    "port": gateway_port,
    "url": f"ws://127.0.0.1:{gateway_port}",
    "state_dir": str(root / "state/gateway"),
})
data["runner"].update({
    "broker_port": int(os.environ["CHECK_BROKER_PORT"]),
    "force_engine": "mock",
    "state_dir": str(root / "state/runner"),
    "workspace_root": str(root / "runs"),
    "talent_dir": str(root / "talent"),
})
data["hpc"]["scheduler"] = "none"
data["projects"] = []
data["dev_log"]["enabled"] = False
path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
"""
    _run("mock 설정", [str(python), "-c", code], cwd=checkout, env=patch_env)


def rehearse(source: Path, guard_paths: list[Path], *, work_dir: Path | None = None,
             receipt_path: Path | None = None) -> str:
    """An explicit empty work_dir retains the installation and local-only logs."""
    if work_dir is None:
        root = Path(tempfile.mkdtemp(prefix="labhq-clean-install-"))
    else:
        root = work_dir.resolve()
        if root.exists() and (not root.is_dir() or any(root.iterdir())):
            raise CheckFailure("빈 작업 폴더", "--work-dir은 새 폴더 또는 빈 폴더여야 함")
        root.mkdir(parents=True, exist_ok=True)
    receipt = Receipt(receipt_path.resolve() if receipt_path else root / "receipt.json", root)
    context = _RECEIPT.set(receipt)
    before = None
    started = False
    request_id = ""

    try:
        source = source.resolve()
        guards = list(guard_paths)
        source_config = source / "config/labhq.yaml"
        if source_config.exists():
            guards.append(source_config)
        try:
            before = _snapshot(guards)
        except OSError as exc:
            raise CheckFailure("보호 경로 snapshot", _one_line(exc)) from None
        checkout = root / "bioinfo-team-3d"
        env = _isolated_env(root, checkout)
        git = shutil.which("git") or "git"
        _run(
            "새 clone",
            [git, "clone", "--local", "--no-hardlinks", str(source), str(checkout)],
            cwd=root,
            env=env,
        )
        source_head = _run("source HEAD", [git, "rev-parse", "HEAD"], cwd=source, env=env).strip()
        clone_head = _run("clone HEAD", [git, "rev-parse", "HEAD"], cwd=checkout, env=env).strip()
        _expect("현재 HEAD clone", source_head == clone_head, f"source {source_head[:12]} != clone {clone_head[:12]}")
        _run("AGENTS hooks", [git, "config", "core.hooksPath", ".githooks"], cwd=checkout, env=env)

        launcher = shutil.which("py" if os.name == "nt" else "python3") or sys.executable
        create_venv = [launcher, "-3", "-m", "venv", ".venv"] if os.name == "nt" and Path(launcher).stem == "py" else [launcher, "-m", "venv", ".venv"]
        _run("README venv", create_venv, cwd=checkout, env=env)
        python = _venv_python(checkout)
        _run("README install", [str(python), "-m", "pip", "install", "-e", ".[dev]"], cwd=checkout, env=env)
        _run("README CLI help", _labhq_command(checkout, "--help"), cwd=checkout, env=env)
        version = _run("Python version", [str(python), "--version"], cwd=checkout, env=env).strip()
        freeze = _run("의존성 판본", [str(python), "-m", "pip", "freeze", "--all"], cwd=checkout, env=env)
        (root / "requirements.freeze.txt").write_text(freeze, encoding="utf-8")
        receipt.data.update(source_commit=source_head, python=version, dependencies=freeze.splitlines(),
                            isolation={name: env[name] for name in ("HOME", "LABHQ_HOME", "LABHQ_STATE_DIR",
                                       "CODEX_HOME", "CLAUDE_CONFIG_DIR", "TEMP")})
        receipt.write()
        _record("사람 인증·실제 모델", "SKIP", detail="직원 CLI 설치·로그인과 실제 모델 호출은 사람이 별도로 확인")
        print("SKIP 사람 인증·실제 모델: mock만 실행")

        # The former editable install read these files from the checkout, so it could not detect wheel omissions.
        wheel_dir = root / "wheel"
        wheel_dir.mkdir()
        _run("wheel build", [str(python), "-m", "pip", "wheel", ".", "--no-deps", "-w", str(wheel_dir)],
             cwd=checkout, env=env)
        wheel = next(wheel_dir.glob("labhq-*.whl"), None)
        _expect("wheel 생성", wheel is not None, "labhq wheel 없음")
        receipt.data["wheel_sha256"] = hashlib.sha256(wheel.read_bytes()).hexdigest()
        receipt.write()
        with zipfile.ZipFile(wheel) as archive:
            wheel_files = set(archive.namelist())
        required_plugin_files = {
            "labhq/skills/.claude-plugin/plugin.json",
            "labhq/skills/humanizer/SKILL.md",
            "labhq/skills/graphical-abstract/templates/graphical-abstract.svg",
        }
        missing_plugin_files = sorted(required_plugin_files - wheel_files)
        _expect("wheel bundled plugin", not missing_plugin_files,
                "누락: " + ", ".join(missing_plugin_files))

        try:
            init_output = _run(
                "labhq init",
                _labhq_command(checkout, "init"),
                cwd=checkout,
                env=env,
                input_text="\n" * 8,  # Enter through every question (scheduler, bioinfo-agent, dev log, consent, ...)
                timeout=120,
            )
            _expect("init 설정 생성", (checkout / "config/labhq.yaml").is_file(), "config/labhq.yaml 없음")
            _expect("init token 비공개", "change-me" not in init_output, "기본 token이 남음")
            _patch_mock_config(python, checkout, root, env)

            doctor = _run(
                "labhq doctor",
                _labhq_command(checkout, "-c", "config/labhq.yaml", "doctor"),
                cwd=checkout,
                env=env,
                timeout=120,
            )
            _expect("doctor fail 0", " fail " not in doctor, "doctor에 fail 항목 있음")
            _expect("doctor 실행 준비", "실행 준비: 예" in doctor, "실행 준비가 아님")

            started = True
            _start_services(root, checkout, env)
            sent = _run(
                "공개 데이터 형태 mock 요청",
                _labhq_command(
                    checkout,
                    "-c",
                    "config/labhq.yaml",
                    "send",
                    "--agent",
                    "analyst",
                    "공개 데이터 예시 3개에서 행 수를 세고 짧게 보고해 줘 [artifact]",
                ),
                cwd=checkout,
                env=env,
                timeout=120,
            )
            matched = re.search(r"\brequest (req_[a-z0-9]+)\b", sent)
            _expect("요청 ID와 보고서", bool(matched) and "[mock:analyst]" in sent, "요청 ID 또는 보고서 없음")
            request_id = matched.group(1) if matched else ""

            verify = _run(
                "labhq verify",
                _labhq_command(checkout, "-c", "config/labhq.yaml", "verify", request_id),
                cwd=checkout,
                env=env,
                timeout=120,
            )
            _expect("verify exit 0", "문제 없음 (exit 0)" in verify, _one_line(verify))
            bundle = root / "runs/requests" / request_id
            required = ["report.md", "report_appendix.md", "MANIFEST.tsv", "ro-crate-metadata.json"]
            _expect("보고서와 요청 묶음", all((bundle / name).is_file() for name in required), "묶음 파일 누락")

            launch_names = _launch_process_names(root / "state/gateway")
            _run(
                "labhq down",
                _labhq_command(checkout, "-c", "config/labhq.yaml", "down"),
                cwd=checkout,
                env=env,
                timeout=120,
            )
            remaining = _venv_python_processes(checkout, launch_names)
            _expect("down 뒤 venv 프로세스 종료", not remaining, "남은 프로세스: " + ", ".join(remaining))
            started = False
        finally:
            if started:
                # up can fail after spawning one service; cleanup also covers that partial start.
                active_error = sys.exc_info()[0] is not None
                try:
                    names = _launch_process_names(root / "state/gateway")
                    _run("실패 뒤 강제 종료", _labhq_command(checkout, "-c", "config/labhq.yaml", "down", "--force"),
                         cwd=checkout, env=env, timeout=120)
                    remaining = _venv_python_processes(checkout, names)
                    _expect("실패 뒤 자식 종료", not remaining, ", ".join(remaining))
                except BaseException as exc:
                    receipt.data["service_cleanup_error"] = _one_line(str(exc) or type(exc).__name__)
                    if not active_error:
                        raise
    except BaseException as exc:
        default_stage = "내부 오류" if isinstance(exc, Exception) else type(exc).__name__
        receipt.data.update(status="FAIL", failure_stage=getattr(exc, "stage", default_stage),
                            detail=_one_line(str(exc) or type(exc).__name__))
        raise
    finally:
        active_error = sys.exc_info()[0] is not None
        try:
            if before is not None:
                after = _snapshot(list(before))
                changed = [str(path) for path, value in before.items() if after[path] != value]
                _expect("보호 경로 mtime", not changed, "mtime 변경: " + ", ".join(changed))
        except BaseException as exc:
            if active_error:
                receipt.data["guard_error"] = _one_line(str(exc) or type(exc).__name__)
            else:
                receipt.data.update(status="FAIL", failure_stage=getattr(exc, "stage", "보호 경로"),
                                    detail=_one_line(str(exc) or type(exc).__name__))
                active_error = True
                raise
        finally:
            try:
                if work_dir is None:
                    try:
                        shutil.rmtree(root)
                    except BaseException as exc:
                        receipt.data["cleanup_error"] = _one_line(str(exc) or type(exc).__name__)
                        if not active_error:
                            receipt.data.update(status="FAIL", failure_stage="임시 폴더 정리",
                                                detail=_one_line(str(exc) or type(exc).__name__))
                            if isinstance(exc, OSError):
                                raise CheckFailure("임시 폴더 정리", _one_line(exc)) from None
                            raise
            finally:
                pending_error = sys.exc_info()[1]
                active_error = active_error or pending_error is not None
                if receipt.data["status"] == "RUNNING":
                    if active_error:
                        label = type(pending_error).__name__ if pending_error is not None else "finalization"
                        receipt.data.update(status="FAIL", failure_stage=label,
                                            detail=_one_line(str(pending_error) or label))
                    else:
                        receipt.data.update(status="PASS", request_id=request_id)
                receipt.data["finished_at"] = datetime.now(timezone.utc).isoformat()
                try:
                    # Do not recreate the automatically removed scratch tree for its default receipt.
                    if work_dir is not None or root.exists() or not receipt.path.is_relative_to(root):
                        receipt.write()
                except BaseException:
                    if not active_error:
                        raise
                finally:
                    _RECEIPT.reset(context)
    return request_id


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--guard-path", action="append", default=[], type=Path)
    parser.add_argument("--work-dir", type=Path, help="새/빈 폴더; 설치판·분리 stdout/stderr를 보존 (로컬 전용)")
    parser.add_argument("--receipt", type=Path, help="PASS/FAIL/SKIP JSON 경로 (로컬 전용)")
    args = parser.parse_args(argv)
    try:
        request_id = rehearse(args.source, args.guard_path, work_dir=args.work_dir, receipt_path=args.receipt)
    except CheckFailure as exc:
        print(f"FAIL {exc.stage}: {_one_line(exc.detail)}", file=sys.stderr)
        return 1
    except Exception as exc:  # keep unexpected failures actionable and one-line too
        print(f"FAIL 내부 오류: {_one_line(exc)}", file=sys.stderr)
        return 1
    print(f"PASS clean install: {request_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
