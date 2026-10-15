#!/usr/bin/env python3
"""Rehearse the documented install and one mock public-data-shaped request."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
from pathlib import Path


for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")


class CheckFailure(RuntimeError):
    def __init__(self, stage: str, detail: str):
        super().__init__(detail)
        self.stage = stage
        self.detail = detail


def _one_line(value: object, limit: int = 500) -> str:
    lines = [line.strip() for line in str(value or "").splitlines() if line.strip()]
    text = " | ".join(lines[-3:]) if lines else "출력 없음"
    return text[-limit:]


def _run(
    stage: str,
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    input_text: str | None = None,
    timeout: float = 600,
) -> str:
    try:
        done = subprocess.run(
            command,
            cwd=cwd,
            env=env,
            input=input_text,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        detail = getattr(exc, "stdout", None) or str(exc)
        raise CheckFailure(stage, _one_line(detail)) from None
    if done.returncode:
        raise CheckFailure(stage, f"exit {done.returncode}: {_one_line(done.stdout)}")
    print(f"PASS {stage}")
    return done.stdout


def _expect(stage: str, condition: bool, detail: str) -> None:
    if not condition:
        raise CheckFailure(stage, detail)
    print(f"PASS {stage}")


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _labhq_command(checkout: Path, *args: str) -> list[str]:
    if os.name == "nt":
        return [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", str(checkout / "labhq.cmd"), *args]
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
    env.update(
        {
            "HOME": str(task_home),
            "USERPROFILE": str(task_home),
            "APPDATA": str(task_home / "AppData/Roaming"),
            "LOCALAPPDATA": str(task_home / "AppData/Local"),
            "XDG_CONFIG_HOME": str(task_home / ".config"),
            "CODEX_HOME": str(task_home / ".codex"),
            "CLAUDE_CONFIG_DIR": str(task_home / ".claude"),
            "LABHQ_STATE_DIR": str(root / "state"),
            "TEMP": str(scratch),
            "TMP": str(scratch),
            "TMPDIR": str(scratch),
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
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


def rehearse(source: Path, guard_paths: list[Path]) -> str:
    source = source.resolve()
    guards = list(guard_paths)
    source_config = source / "config/labhq.yaml"
    if source_config.exists():
        guards.append(source_config)
    before = _snapshot(guards)
    started = False
    request_id = ""

    root = Path(tempfile.mkdtemp(prefix="labhq-clean-install-"))
    try:
        checkout = root / "bioinfo-team-3d"
        clone_env = os.environ.copy()
        _run(
            "새 clone",
            ["git", "clone", "--local", "--no-hardlinks", str(source), str(checkout)],
            cwd=root,
            env=clone_env,
        )
        source_head = _run("source HEAD", ["git", "rev-parse", "HEAD"], cwd=source, env=clone_env).strip()
        clone_head = _run("clone HEAD", ["git", "rev-parse", "HEAD"], cwd=checkout, env=clone_env).strip()
        _expect("현재 HEAD clone", source_head == clone_head, f"source {source_head[:12]} != clone {clone_head[:12]}")

        create_venv = ["py", "-3", "-m", "venv", ".venv"] if os.name == "nt" else ["python3", "-m", "venv", ".venv"]
        _run("README venv", create_venv, cwd=checkout, env=clone_env)
        python = _venv_python(checkout)
        env = _isolated_env(root, checkout)
        _run(
            "README install",
            [str(python), "-m", "pip", "install", "-e", ".[dev]"],
            cwd=checkout,
            env=env,
        )

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

            _run(
                "gateway와 runner 시작",
                _labhq_command(checkout, "-c", "config/labhq.yaml", "up"),
                cwd=checkout,
                env=env,
                timeout=120,
            )
            started = True
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
                subprocess.run(
                    _labhq_command(checkout, "-c", "config/labhq.yaml", "down", "--force"),
                    cwd=checkout,
                    env=env,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=30,
                    check=False,
                )
    finally:
        # Preserve an actionable stage failure (especially the remaining process names) if that same process also
        # keeps Windows from deleting its loaded modules. With no prior failure, cleanup errors still fail the check.
        active_error = sys.exc_info()[0] is not None
        try:
            shutil.rmtree(root)
        except OSError:
            if not active_error:
                raise

    after = _snapshot(list(before))
    changed = [str(path) for path, value in before.items() if after[path] != value]
    _expect("보호 경로 mtime", not changed, "mtime 변경: " + ", ".join(changed))
    return request_id


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--guard-path", action="append", default=[], type=Path)
    args = parser.parse_args(argv)
    try:
        request_id = rehearse(args.source, args.guard_path)
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
