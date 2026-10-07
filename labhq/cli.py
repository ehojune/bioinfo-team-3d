from __future__ import annotations

import argparse
import asyncio
import ipaddress
import json
import logging
import math
import os
import re
import secrets
import signal
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

from .costs import cost_detail, cost_text
from .facilities.signatures import problem_text
from .request_status import is_terminal_request
from .settings import Settings
from .util import atomic_write_text, free_port, short, without_windows_app_aliases

REPO = Path(__file__).resolve().parents[1]
ICON = {"cso": "🦉", "chief_of_staff": "🐧", "biologist": "🐻", "data_steward": "🐿️", "lit_scout": "🦊",
        "analyst": "🦝", "engineer": "🐙", "qc_reviewer": "🦔", "sci_reviewer": "🐢", "recruiter": "🦫", "bioinfo-agent": "🦦"}
STATE_KO = {"working": "작업 중", "waiting": "승인 대기", "hibernating": "HPC 대기(수면)", "done": "완료", "error": "오류"}


def _instance_config(name: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", name):
        raise ValueError("--instance must use 1-64 letters, numbers, underscores or hyphens")
    return str(Path.home() / ".labhq" / name / "labhq.yaml")


def _normalize_instance_arg(argv: list[str]) -> list[str]:
    """Accept --instance before or after any subcommand, including nested ones."""
    rest: list[str] = []
    instances: list[str] = []
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg == "--instance":
            if index + 1 >= len(argv):
                raise ValueError("--instance requires a name")
            instances.append(argv[index + 1])
            index += 2
            continue
        if arg.startswith("--instance="):
            instances.append(arg.split("=", 1)[1])
            index += 1
            continue
        rest.append(arg)
        index += 1
    if len(instances) > 1:
        raise ValueError("--instance may be given only once")
    return (["--instance", instances[0]] if instances else []) + rest


def render(ev: dict, login_holds: set[str] | None = None) -> None:
    t, a, d = ev.get("type"), ev.get("agent_id") or "", ev.get("data") or {}
    ic = ICON.get(a, "🐥" if a.startswith("c_") else "·")
    ts = datetime.fromtimestamp(ev.get("ts", time.time())).strftime("%H:%M:%S")
    line = None
    if t == "agent.status" and d.get("state") in STATE_KO and not (d["state"] == "working" and "task" not in d
                                                                    and "approval" not in d and d.get("model")):
        extra = f" — {d['task']}" if d.get("task") else ""
        extra += f" (jobs: {', '.join(d['jobs'])})" if d.get("jobs") else ""
        extra += f" ⚠ {d['error']}" if d.get("error") else ""
        line = f"{ic} {a:<15}{STATE_KO[d['state']]}{extra}"
    elif t == "agent.tool":
        line = f"{ic} {a:<15}🔧 {d.get('name')} {short(d.get('input'), 80)}"
    elif t == "agent.log" and str(d.get("level") or "").casefold() in {"warn", "warning", "alert"}:
        line = f"⚠ {ic} {a:<15}{d.get('text') or d.get('message') or '직원 경고'}"
    elif t == "approval.requested":
        line = f"📱 승인 요청 [{d.get('kind')}] {d.get('summary')}  (id={d.get('id')})"
    elif t == "approval.resolved":
        line = f"✅ 승인 결과: {'허가' if d.get('approved') else '거절'} {d.get('note', '')}"
    elif t == "job.submitted":
        line = f"🖥️  HPC 제출 {d.get('job_id')} ({d.get('name')})"
    elif t == "job.state":
        line = f"🖥️  {d.get('job_id')} → {d.get('state')} (exit={d.get('exit_status')})"
    elif t == "jobs.finished":
        line = f"⏰ HPC 작업 종료 → {ic} {a} 깨우기"
    elif t == "request.plan":
        rows = [f"   {s['id']} → {ICON.get(s['agent_id'], '·')} {s['agent_id']:<13} deps={s['depends_on']}  {s['instruction']}"
                for s in d.get("steps", [])]
        assumptions = [value for value in d.get("assumptions", []) if isinstance(value, str) and value.strip()]
        shown = "" if not assumptions else ("\n   가정\n" + "\n".join(f"   - {value}" for value in assumptions) +
                                             f'\n   바꾸려면: labhq note {ev.get("request_id") or "<request_id>"} "바꿀 내용"')
        processing = d.get("processing") or {}
        route = (f" · 처리: 단독({processing.get('agent_id')})" if processing.get("mode") == "solo" else
                 " · 처리: 단독 실패 → 팀" if processing.get("fallback") else " · 처리: 팀")
        line = ("📋 CSO 계획" + route + "\n" + "\n".join(rows) + shown +
                "".join(f"\n   ⚠ {w}" for w in d.get("warnings", [])))
    elif t == "request.route":
        line = ("↪ 처리: 단독 실패 → 팀" if d.get("fallback") else
                f"↪ 처리: 단독({d.get('agent_id')})" if d.get("mode") == "solo" else "↪ 처리: 팀")
    elif t in {"engine.login_wait", "request.step_login_wait"}:
        engine = str(d.get("engine") or "")
        if login_holds is not None and engine in login_holds:
            return
        if login_holds is not None:
            login_holds.add(engine)
        line = (f"🔐 {engine} 로그인 대기\n   {d.get('command', '')}\n"
                "   다시 로그인한 뒤 자동 재시도 또는 resume")
    elif t == "engine.login_resumed":
        if login_holds is not None:
            login_holds.discard(str(d.get("engine") or ""))
        line = f"🔓 {d.get('engine')} 로그인 대기 해제"
    elif t == "recruit.suggested":
        line = f"🧾 CSO 채용 제안: {d.get('repo') or d.get('paper')} — {d.get('reason')}"
    elif t == "request.review":
        line = f"🐢 과학 리뷰 #{d.get('revision')}: {d.get('verdict')} {d.get('scores')}"
        line += "".join(f"\n   - {i.get('step_id')}: {i.get('problem')} → {i.get('request')}" for i in d.get("issues") or [])
    elif t == "recruit.status":
        line = f"🦫 인사팀: {d.get('slug')} {d.get('stage')}"
    elif t == "recruit.done":
        ag = d.get("agent", {})
        line = f"🐥 파견직 입사: {ag.get('id')} ({ag.get('name')}) · 수습통과={d.get('passed_probation')}"
    elif t == "recruit.failed":
        line = f"🦫 채용 실패: {d.get('error')}"
    elif t == "request.completed":
        summary = d.get("cost_summary")
        line = f"🏁 요청 완료 (ok={d.get('ok')}, cost={cost_text(d.get('cost_usd'), d.get('cost_known'), summary)})"
        if summary and (summary.get("estimated_usd") or summary.get("unknown_count") or summary.get("warnings")):
            line += f"\n   {cost_detail(summary)}"
    elif t == "request.failed":
        line = f"💥 요청 실패: {d.get('error')}"
    elif t == "request.bundle":
        line = (f"📦 요청 묶음: {d['bundle_path']}" if d.get("bundle_path") else
                f"⚠️ 요청 묶음: {d.get('bundle_warning', '만들지 못함')}")
    if t in {"request.completed", "request.failed"} and line:
        if d.get("bundle_path"):
            line += f"\n   요청 묶음: {d['bundle_path']}"
        if d.get("bundle_warning"):
            line += f"\n   경고: {d['bundle_warning']}"
    elif t in ("runner.online", "runner.offline"):
        line = f"🔌 {t}: {d.get('runner_id')}"
    if line:
        print(f"[{ts}] {line}", flush=True)


def render_snapshot(ev: dict, login_holds: set[str]) -> None:
    """Show durable login holds when watch connects after their original events."""
    for hold in (ev.get("data") or {}).get("engine_holds") or []:
        render({"type": "engine.login_wait", "ts": ev.get("ts"), "data": hold}, login_holds)


# ---------------- HTTP / WS client helpers ----------------
def _http_base(s: Settings) -> str:
    return s.gateway.url.replace("wss://", "https://").replace("ws://", "http://").rstrip("/")


def _open_web_office(s: Settings, config: str | None, *, three_d: bool = False, opener=None) -> str:
    """Open the web office with the client token in its address; the page stores it and drops it from the URL.

    The PI could not get past the token prompt on a plain `gateway` start (PI visit 2026-10-04). The token goes
    only to the browser, never to the terminal or a log."""
    import webbrowser
    from urllib.parse import quote

    page = f"{_http_base(s)}/{'3d' if three_d else ''}"
    if (opener or webbrowser.open)(f"{page}?token={quote(s.gateway.client_token, safe='')}"):
        return f"웹 사무실을 열었습니다: {page} (토큰은 이 브라우저에 저장됩니다)"
    return (f"브라우저를 열지 못했습니다. {page} 를 열고 '게이트웨이 토큰' 칸에 {config or '설정 파일'}의 "
            "gateway.client_token 값을 넣으세요.")


def _api_error_detail(response) -> str:
    try:
        payload = response.json()
    except (ValueError, TypeError):
        return ""
    detail = payload.get("detail") if isinstance(payload, dict) else None
    if isinstance(detail, str):
        return " ".join(detail.split())
    return json.dumps(detail, ensure_ascii=False, separators=(",", ":")) if detail is not None else ""


def _api(s: Settings, method: str, path: str, *, raw_errors: bool = False, **kw):
    import httpx

    try:
        r = httpx.request(method, _http_base(s) + path,
                          headers={"Authorization": f"Bearer {s.gateway.client_token}"}, timeout=30, **kw)
        r.raise_for_status()
        return r.json()
    except httpx.HTTPError as exc:
        if raw_errors:
            raise
        if isinstance(exc, httpx.HTTPStatusError):
            detail = _api_error_detail(exc.response)
            message = f"gateway 요청 실패(HTTP {exc.response.status_code})" + (f": {detail}" if detail else "")
        else:
            message = f"gateway에 연결할 수 없습니다: {_http_base(s)} — `labhq up`을 실행하세요."
        print(message, file=sys.stderr)
        raise SystemExit(2) from None
    except ValueError:
        if raw_errors:
            raise
        print("gateway 응답을 읽을 수 없습니다.", file=sys.stderr)
        raise SystemExit(2) from None


def _default_client_token(token: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", token.casefold())
    return normalized in {"changeme", "changemeclient"}


def _config_command(s: Settings, command: str) -> str:
    return f'labhq -c "{s.config_path}" {command}' if s.config_path else f"labhq {command}"


def _health(s: Settings) -> dict | None:
    import httpx

    try:
        value = _api(s, "GET", "/api/health", raw_errors=True)
    except (httpx.HTTPError, ValueError):
        return None
    return value if isinstance(value, dict) and value.get("service") == "labhq gateway" else None


def _wait_for_health(s: Settings, *, runner: bool, timeout: float = 20) -> dict | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        health = _health(s)
        if health and (not runner or s.runner.id in (health.get("runners") or [])):
            return health
        time.sleep(0.2)
    return None


def _launch_state(s: Settings) -> Path:
    return s.path(s.gateway.state_dir)


def _pid_file(s: Settings, role: str) -> Path:
    return _launch_state(s) / f"labhq-{role}.pid"


def _start_background(s: Settings, role: str, *, popen=None) -> int:
    state = _launch_state(s)
    logs = state / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    if role == "runner":
        env = without_windows_app_aliases(env)
    command = [sys.executable, "-m", "labhq.cli"]
    if s.config_path:
        command += ["-c", s.config_path]
    command.append(role)
    options = {
        "cwd": str(REPO), "stdin": subprocess.DEVNULL, "stderr": subprocess.STDOUT,
        "env": env, "close_fds": True,
    }
    if sys.platform == "win32":
        options["creationflags"] = (getattr(subprocess, "DETACHED_PROCESS", 0x00000008) |
                                    getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200))
    else:
        options["start_new_session"] = True
    with (logs / f"{role}.log").open("a", encoding="utf-8") as output:
        process = (popen or subprocess.Popen)(command, stdout=output, **options)
    atomic_write_text(_pid_file(s, role), f"{process.pid}\n")
    return int(process.pid)


def _up(s: Settings, *, popen=None) -> None:
    if _default_client_token(s.gateway.client_token):
        print("gateway 시작 거부: gateway.client_token의 change-me 기본값을 바꾸세요.", file=sys.stderr)
        raise SystemExit(2)
    health = _health(s)
    if health is None:
        _start_background(s, "gateway", popen=popen)
        health = _wait_for_health(s, runner=False)
        if health is None:
            print(f"gateway가 시작되지 않았습니다. 로그: {_launch_state(s) / 'logs' / 'gateway.log'}",
                  file=sys.stderr)
            raise SystemExit(2)
    if s.runner.id not in (health.get("runners") or []):
        _start_background(s, "runner", popen=popen)
        health = _wait_for_health(s, runner=True, timeout=60)
        if health is None:
            print(f"runner가 연결되지 않았습니다. 로그: {_launch_state(s) / 'logs' / 'runner.log'}",
                  file=sys.stderr)
            raise SystemExit(2)
    print(f"웹 사무실: {_http_base(s)}/  (`{_config_command(s, 'open')}`)")


def _stop_background(s: Settings, role: str) -> int | None:
    path = _pid_file(s, role)
    try:
        pid = int(path.read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        return None
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    except OSError as exc:
        print(f"{role} PID {pid}을 종료하지 못했습니다: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
    path.unlink(missing_ok=True)
    return pid


def _down(s: Settings) -> None:
    stopped = [(role, pid) for role in ("runner", "gateway") if (pid := _stop_background(s, role)) is not None]
    print("종료: " + (", ".join(f"{role} PID {pid}" for role, pid in stopped) if stopped else "기록된 프로세스 없음"))


def _terminal_report(data: dict) -> str:
    """PI report first, then the separately labelled execution record for watch/send."""
    body = str(data.get("report") or data.get("error") or "")
    appendix = str(data.get("report_appendix") or "")
    if data.get("report_truncated"):
        body += (f"\n\n[… 본문 잘림 · 전체 {data.get('report_chars', '?')}자 · "
                 f"전문: GET {data.get('report_api', '-')}]")
    if appendix and data.get("report_appendix_truncated"):
        appendix += (f"\n\n[… 실행 기록 잘림 · 전체 {data.get('report_appendix_chars', '?')}자 · "
                     f"전문: GET {data.get('report_appendix_api', '-')}]")
    return body + (("\n\n--- 실행 기록 ---\n" + appendix) if appendix else "")


def _verify(s: Settings, request_id: str, *, as_json: bool, bundle: str | None) -> int:
    """`labhq verify`: 0 when the outputs and report check hold, 1 with problems, 2 when it cannot check (#58 ⑥)."""
    import httpx
    from urllib.parse import quote

    from .evidence.audit import render_verify, verify_request, write_bundle

    def cannot(reason: str) -> int:
        print(json.dumps({"request_id": request_id, "reasons": [reason], "exit_code": 2}, ensure_ascii=False)
              if as_json else f"확인 못함: {reason} (exit 2)")
        return 2

    try:
        req = _api(s, "GET", f"/api/requests/{quote(request_id, safe='')}", raw_errors=True)
    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code
        return cannot("gateway에 그 요청이 없습니다" if code == 404 else f"gateway가 HTTP {code}를 돌려줬습니다")
    except (httpx.HTTPError, ValueError) as exc:
        return cannot(f"gateway에서 요청을 읽지 못했습니다: {exc}")
    if not isinstance(req, dict):
        return cannot("gateway가 요청 기록 대신 다른 값을 돌려줬습니다")
    report = verify_request(req, s)
    if bundle and report["exit_code"] == 2:
        report["reasons"].append("끝까지 검사하지 못해 감사 번들을 만들지 않았습니다")
    elif bundle:
        try:
            report["bundle"] = str(write_bundle(report, req, Path(bundle)))
        except (OSError, ValueError) as exc:  # ValueError: a path with no file name (`--bundle .`)
            report["reasons"].append(f"감사 번들을 쓰지 못했습니다: {exc}")
            report["exit_code"] = 2
    print(json.dumps(report, ensure_ascii=False, indent=2) if as_json else render_verify(report))
    return report["exit_code"]


async def _watch(s: Settings, request_id: str | None = None) -> None:
    import websockets

    url = f"{s.gateway.url.rstrip('/')}/ws/client?token={s.gateway.client_token}"
    login_holds: set[str] = set()
    async with websockets.connect(url, max_size=64 * 2**20) as ws:
        async for raw in ws:
            ev = json.loads(raw)
            if ev.get("type") == "snapshot":
                render_snapshot(ev, login_holds)
                continue
            if request_id and ev.get("request_id") not in (request_id, None):
                continue
            render(ev, login_holds)
            if request_id and ev.get("request_id") == request_id and ev["type"] in ("request.completed", "request.failed"):
                print("\n" + _terminal_report(ev["data"]))
                return


async def _send_and_wait(s: Settings, body: dict) -> None:
    import websockets

    url = f"{s.gateway.url.rstrip('/')}/ws/client?token={s.gateway.client_token}"
    login_holds: set[str] = set()
    async with websockets.connect(url, max_size=64 * 2**20) as ws:
        await ws.recv()  # snapshot
        rid = _api(s, "POST", "/api/requests", json=body)["request_id"]
        print(f"request {rid}")
        async for raw in ws:
            ev = json.loads(raw)
            if ev.get("request_id") != rid:
                continue
            render(ev, login_holds)
            if ev["type"] in ("request.completed", "request.failed"):
                print("\n" + _terminal_report(ev["data"]))
                return


async def _run_runner_with_interrupts(runner) -> None:
    previous = signal.getsignal(signal.SIGINT)
    last_warning = 0.0

    def on_interrupt(_signum, _frame) -> None:
        nonlocal last_warning
        active = [tid for tid, task in runner.tasks.items() if not task.done()]
        # A step asleep on HPC has no live task, but the runner still watches its jobs and wakes the agent.
        jobs = [jid for jid, job in getattr(runner, "jobs", {}).items() if not job.get("terminal")]
        now = time.monotonic()
        if not (active or jobs) or (last_warning and now - last_warning <= 10):
            raise KeyboardInterrupt
        last_warning = now
        print(f"진행 중인 task {len(active)}개, 감시 중인 HPC job {len(jobs)}개: {', '.join(active + jobs)}. "
              "10초 안에 Ctrl+C를 다시 누르면 종료합니다.", file=sys.stderr, flush=True)

    signal.signal(signal.SIGINT, on_interrupt)
    try:
        await runner.run_forever()
    finally:
        signal.signal(signal.SIGINT, previous)
        runner.stop()


# ---------------- demo (no API keys, no cluster) ----------------
def _approval_delay(phone: bool, approve_timeout: float) -> float:
    return approve_timeout if phone else 0.3


LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")
WILDCARD_HOSTS = ("0.0.0.0", "::", "")


def _port_available(host: str, port: int) -> bool:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    bind_host = host or ("::" if family == socket.AF_INET6 else "0.0.0.0")
    try:
        with socket.socket(family, socket.SOCK_STREAM) as sock:
            sock.bind((bind_host, port))
        return True
    except OSError:
        return False


def _demo_web_port(host: str, preferred: int) -> int:
    return preferred if _port_available(host, preferred) else free_port()


def _demo_token(exposed: bool, current: str) -> str:
    return secrets.token_urlsafe(16) if exposed else current


def _demo_dial_host(host: str) -> str:
    """Where the in-process runner connects: loopback of the bind family, or the bound address itself."""
    if host in ("::1", "::"):
        return "[::1]"
    if host in LOOPBACK_HOSTS + WILDCARD_HOSTS:
        return "127.0.0.1"
    return _demo_url_hosts(host)[0]


def _demo_url_hosts(host: str) -> list[str]:
    """Browser addresses for the requested bind family."""
    if host == "::1":
        return ["[::1]"]
    if host == "::":
        return _lan_ipv6_addresses()
    if host in LOOPBACK_HOSTS:
        return ["127.0.0.1"]
    if host in WILDCARD_HOSTS:
        return _lan_ipv4_addresses()
    return [f"[{host}]" if ":" in host else host]


def _lan_ipv4_addresses() -> list[str]:
    addresses: set[str] = set()
    try:
        for family, _, _, _, sockaddr in socket.getaddrinfo(socket.gethostname(), None, family=socket.AF_INET):
            if family == socket.AF_INET:
                addresses.add(sockaddr[0])
    except OSError:
        pass
    try:
        # UDP connect selects a local interface without sending a packet.
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("192.0.2.1", 80))
            addresses.add(sock.getsockname()[0])
    except OSError:
        pass
    return sorted(ip for ip in addresses if not (ipaddress.IPv4Address(ip).is_loopback
                                                  or ipaddress.IPv4Address(ip).is_unspecified))


def _lan_ipv6_addresses() -> list[str]:
    preferred: set[str] = set()
    fallback: set[str] = set()

    def add(sockaddr):
        raw, _, named_scope = sockaddr[0].partition("%")
        try:
            address = ipaddress.IPv6Address(raw)
        except ValueError:
            return
        if address.is_unspecified or address.is_multicast:
            return
        scope = (sockaddr[3] if len(sockaddr) > 3 else 0) or named_scope
        if address.is_link_local and not scope:
            return
        zone = f"%25{scope}" if address.is_link_local and scope else ""
        (fallback if address.is_loopback or address.is_link_local else preferred).add(
            f"[{address.compressed}{zone}]")

    try:
        for family, _, _, _, sockaddr in socket.getaddrinfo(socket.gethostname(), None, family=socket.AF_INET6):
            if family == socket.AF_INET6:
                add(sockaddr)
    except OSError:
        pass
    if not preferred:
        try:
            # UDP connect chooses a route without sending a packet.
            with socket.socket(socket.AF_INET6, socket.SOCK_DGRAM) as sock:
                sock.connect(("2001:db8::1", 80))
                add(sock.getsockname())
        except OSError:
            pass
    if not preferred:
        for sockaddr in _interface_ipv6_addresses():
            add(sockaddr)
    return sorted(preferred) or sorted(fallback) or ["[::1]"]


def _interface_ipv6_addresses() -> list[tuple]:
    """OS interface addresses, including numeric scopes without a global IPv6 route."""
    addresses = []
    try:
        if sys.platform == "win32":
            command = ("@(Get-NetIPAddress -AddressFamily IPv6 -ErrorAction Stop | "
                       "Select-Object IPAddress,InterfaceIndex) | ConvertTo-Json -Compress")
            result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
                                    capture_output=True, text=True, timeout=3, check=True,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            rows = json.loads(result.stdout)
            for row in rows if isinstance(rows, list) else [rows]:
                addresses.append((row["IPAddress"], 0, 0, int(row["InterfaceIndex"])))
        elif sys.platform.startswith("linux"):
            for line in Path("/proc/net/if_inet6").read_text(encoding="ascii").splitlines():
                raw, index, *_ = line.split()
                addresses.append((str(ipaddress.IPv6Address(int(raw, 16))), 0, 0, int(index, 16)))
        else:
            # BSD/macOS expose scoped IPv6 interface addresses through the system ifconfig.
            result = subprocess.run(["ifconfig", "-a"], capture_output=True, text=True, timeout=3, check=True)
            interface = None
            for line in result.stdout.splitlines():
                if line and not line[0].isspace():
                    interface = line.split(":", 1)[0]
                fields = line.split()
                if fields and fields[0] == "inet6" and interface:
                    addresses.append((fields[1], 0, 0, socket.if_nametoindex(interface)))
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
        pass
    return addresses


def _phone_url_lines(addresses: list[str], port: int, token: str) -> list[str]:
    return [f"http://{ip}:{port}/3d?token={token}  http://{ip}:{port}/?token={token}"
            for ip in (addresses or ["127.0.0.1"])]


async def _demo_auto_approve(hub, aid: str, delay: float, phone: bool) -> None:
    await asyncio.sleep(delay)
    if aid not in hub.approvals:
        return
    try:
        await hub.resolve_approval(aid, True, "demo timeout auto-approve" if phone else "demo auto-approve")
    except KeyError:
        return  # A phone tap won the race.
    print("   (demo) 승인 대기 시간 만료 → 자동 승인" if phone else "   (demo) 📱 폰에서 '승인' 탭했다고 가정")


def _tap_publish(publish, after):
    """Wrap Hub.publish; sequenced runner events also pass runner_id/runner_seq, so forward everything."""
    async def tap(ev: dict, *args, **kwargs) -> None:
        await publish(ev, *args, **kwargs)
        after(ev)
    return tap


def _demo_settings(tmp: Path) -> Settings:
    s = Settings()
    s.gateway.state_dir = str(tmp / "state" / "gateway")
    s.runner.state_dir = str(tmp / "state" / "runner")
    s.runner.workspace_root = str(tmp / "runs")
    s.runner.talent_dir = str(tmp / "talent")
    s.runner.agents_dir = str(tmp / "agents")
    s.runner.force_engine = "mock"
    s.runner.job_poll_s = 1
    s.hpc.scheduler = "mock"
    return s


async def _demo(web: bool = False, port: int = 8787, phone: bool = False,
                host: str = "127.0.0.1", approve_timeout: float = 120) -> None:
    import uvicorn

    from .gateway.server import RequestIn, create_app
    from .runner.daemon import Runner

    tmp = Path(tempfile.mkdtemp(prefix="labhq-demo-"))
    shutil.copytree(REPO / "agents", tmp / "agents")
    s = _demo_settings(tmp)
    gport = _demo_web_port(host, port) if web else free_port()
    dial = _demo_dial_host(host)
    s.gateway.port, s.gateway.url = gport, f"ws://{dial}:{gport}"
    exposed = phone or host not in LOOPBACK_HOSTS
    # Both default tokens are public; on the LAN anyone could otherwise join as a client or replace the runner.
    s.gateway.client_token = _demo_token(exposed, s.gateway.client_token)
    s.gateway.runner_token = _demo_token(exposed, s.gateway.runner_token)
    # The mock team's approvals expire after this; in phone mode it must outlast the tap fallback.
    s.policy.approvals.timeout_s = math.ceil(approve_timeout) + 30 if phone else 60
    s.runner.broker_port = free_port()

    app = create_app(s)
    hub = app.state.hub
    publish = hub.publish

    def after(ev: dict) -> None:
        render(ev)
        if ev.get("type") == "approval.requested":
            asyncio.get_running_loop().create_task(_demo_auto_approve(
                hub, ev["data"]["id"], _approval_delay(phone, approve_timeout), phone))

    hub.publish = _tap_publish(publish, after)
    from .security import gateway_log_config

    server = uvicorn.Server(uvicorn.Config(app, host=host, port=gport, log_level="warning",
                                          log_config=gateway_log_config()))
    tasks = [asyncio.create_task(server.serve())]
    while not server.started:
        await asyncio.sleep(0.05)
    runner = Runner(s)
    tasks.append(asyncio.create_task(runner.run_forever()))

    async def until(pred, timeout: float) -> None:
        t0 = time.time()
        while not pred():
            if time.time() - t0 > timeout:
                raise TimeoutError("demo step timed out")
            await asyncio.sleep(0.1)

    try:
        await until(lambda: len(hub.agents) >= 5, 15)
        if web:
            if phone:
                addresses = _demo_url_hosts(host)
                if not addresses:
                    print("LAN IPv4 주소를 찾지 못했습니다. PC에서 열 수 있는 주소:")
                for line in _phone_url_lines(addresses, gport, s.gateway.client_token):
                    print(line)
                print("폰과 PC를 같은 Wi-Fi에 연결하세요.")
                print("Windows 방화벽 알림에서는 Python의 private network 접근을 허용하세요.")
            else:
                for ip in _demo_url_hosts(host) or ["127.0.0.1"]:
                    print(f"\n=== 웹 사무실: http://{ip}:{gport}/?token={s.gateway.client_token} (Ctrl+C로 종료) ===\n")
            texts = ["공개 폐선암 scRNA-seq에서 CD276 고발현 세포유형 찾고 QC까지 [hpc] [needs-approval] [revise] [recruit]",
                     "새로 받은 WGS 배치 표준 QC [hpc] [needs-approval]"]
            for i in range(10**6):
                rid = hub.create_request(RequestIn(text=texts[i % 2]))
                await until(lambda: is_terminal_request(hub.requests[rid]["status"]),
                            max(120, approve_timeout + 60) if phone else 120)
                if i == 0 and "c_scanpy" not in hub.agents:
                    await asyncio.sleep(3)
                    await hub.send_runner(s.runner.id, {"type": "recruit.start", "repo": "https://github.com/scverse/scanpy",
                                                        "focus": "Preprocessing and clustering", "ttl_days": 14,
                                                        "request_id": rid})
                await asyncio.sleep(8)
        print(f"\n=== 정규직 {len(hub.agents)}명 출근 · 오케스트레이션 요청 ===\n")
        rid = hub.create_request(RequestIn(
            text="공개 폐선암 scRNA-seq에서 CD276(B7-H3) 고발현 세포유형 찾고 QC까지 [hpc] [needs-approval] [revise] [recruit]"))
        await until(lambda: is_terminal_request(hub.requests[rid]["status"]), 60)
        print("\n--- CSO 최종 보고 ---\n" + _terminal_report(hub.requests[rid]))

        print("\n=== CSO 채용 제안을 PI가 승인 → 파견직 채용 (Paper2Agent) ===\n")
        await hub.send_runner(s.runner.id, {"type": "recruit.start", "repo": "https://github.com/scverse/scanpy",
                                            "paper": "https://doi.org/10.1186/s13059-017-1382-0",
                                            "focus": "Preprocessing and clustering", "ttl_days": 14})
        await until(lambda: "c_scanpy" in hub.agents, 30)
        rid2 = hub.create_request(RequestIn(text="scanpy 논문 방식으로 PBMC 전처리·클러스터링 계획 자문",
                                            mode="direct", agent_id="c_scanpy"))
        await until(lambda: is_terminal_request(hub.requests[rid2]["status"]), 30)
        print("\n--- 파견직 응답 ---\n" + _terminal_report(hub.requests[rid2]))
        print(f"\n작업 폴더(실험노트): {tmp / 'runs'}\n인재풀: {tmp / 'talent'}")
    finally:
        runner.stop()
        server.should_exit = True
        await asyncio.sleep(0.3)
        for t in tasks:
            t.cancel()


# ---------------- entry point ----------------
def main(argv: list[str] | None = None) -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    p = argparse.ArgumentParser(prog="labhq", description="Bio lab HQ — multi-agent research lab")
    p.add_argument("-c", "--config", default=None, help="config YAML (default: $LABHQ_CONFIG)")
    p.add_argument("--instance", help="use ~/.labhq/NAME/labhq.yaml")
    sub = p.add_subparsers(dest="cmd", required=True)
    init = sub.add_parser("init", help="configure this installation and run doctor")
    init.add_argument("--yes", action="store_true", help="accept suggested defaults")
    init.add_argument("--dry-run", action="store_true", help="preview without writing files")
    init.add_argument("--force", action="store_true", help="replace an existing config and rotate tokens")
    sub.add_parser("gateway")
    sub.add_parser("up", help="start the gateway and runner in the background")
    sub.add_parser("down", help="stop only the gateway and runner recorded by this instance")
    op = sub.add_parser("open", help="open the web office in the browser, signed in (the token is never printed)")
    op.add_argument("--3d", dest="three_d", action="store_true", help="open the 3D office instead of 2.5D")
    sub.add_parser("runner")
    sub.add_parser("agents")
    sub.add_parser("status", help="show runners, running requests and pending approvals")
    doctor = sub.add_parser("doctor", help="check runner capabilities without starting agents")
    doctor.add_argument("--json", action="store_true", help="write state_dir/capabilities.json")
    doctor.add_argument("--network", action="store_true", help="check public data source reachability")
    sp = sub.add_parser("send")
    sp.add_argument("text")
    sp.add_argument("--agent", help="direct mode: send to one agent")
    sp.add_argument("--team", action="store_true", help="force the CSO plan to use the team path")
    sp.add_argument("--project-dir", action="append", default=[])
    sp.add_argument("--budget", type=float)
    sp.add_argument("--project", help="project id → updates go to that project's GitHub repo")
    sp.add_argument("--ref", action="append", default=[], metavar="[KIND:]VALUE",
                    help="reference pointer, repeatable: github owner/repo or URL, DOI, PMID, http(s) URL, "
                         "or a runner path inside runner.reference_roots (prefix kind: to force one)")
    sp.add_argument("--no-default-refs", action="store_true", help="leave out pi_profile.references")
    sp.add_argument("--plan-only", action="store_true", help="stop after the CSO plan; do not run or review steps")
    sp.add_argument("--cso-model", help="request-local CSO model from orchestrator.cso_models")
    sp.add_argument("--no-wait", action="store_true")
    note = sub.add_parser("note", help="send a note to later stages of a running request")
    note.add_argument("request_id")
    note.add_argument("text")
    resume = sub.add_parser("resume", help="retry a step waiting for quota or engine login")
    resume.add_argument("request_id")
    resume.add_argument("step_id")
    sub.add_parser("watch")
    sub.add_parser("projects", help="list projects and their GitHub repos")
    cr = sub.add_parser("codex-review", help="ask Codex to review a PR in a project repo (posts '@codex review')")
    cr.add_argument("project")
    cr.add_argument("pr", type=int)
    cr.add_argument("--note", default="")
    sub.add_parser("approvals")
    ap = sub.add_parser("approve", help="decide a pending approval (CP2 evidence review needs --choice)")
    ap.add_argument("id")
    ap.add_argument("--deny", action="store_true")
    ap.add_argument("--choice", choices=["approve", "revise", "deny"],
                    help="CP2 evidence review (research_evidence) decision; the note never decides it")
    ap.add_argument("--note", default="")
    vp = sub.add_parser("verify", help="re-hash a request's outputs on this PC and recheck its report anchors (#58)")
    vp.add_argument("request_id")
    vp.add_argument("--json", action="store_true", help="print the result as JSON")
    vp.add_argument("--bundle", metavar="OUT.zip", help="also write README.md, claims.json and artifacts.json "
                    "into a zip (the output files themselves are not included)")
    rp = sub.add_parser("recruit", help="hire a contract agent from a paper/repo via Paper2Agent")
    rp.add_argument("--paper")
    rp.add_argument("--repo")
    rp.add_argument("--focus")
    rp.add_argument("--ttl", type=float, help="contract length in days")
    rp.add_argument("--name")
    cp = sub.add_parser("contract", help="extend | release | activate | rehire a contract agent")
    cp.add_argument("action", choices=["extend", "release", "activate", "rehire"])
    cp.add_argument("target", help="agent id (or talent slug for rehire)")
    cp.add_argument("--days", type=float)
    sub.add_parser("talent", help="list the talent pool (past contract agents)")
    sub.add_parser("setup-paper2agent", help="install the paper2agent skill for Claude Code and Codex")
    dp = sub.add_parser("demo", help="offline demo with mock agents (no API keys, no cluster)")
    dp.add_argument("--web", action="store_true", help="keep running and serve the web office")
    dp.add_argument("--phone", action="store_true", help="serve the web office to phones on the same Wi-Fi")
    dp.add_argument("--host", help="demo bind address (default: loopback, or 0.0.0.0 with --phone)")
    dp.add_argument("--approve-timeout", type=float, default=120, help="phone approval fallback in seconds")
    dp.add_argument("--port", type=int, default=8787)
    bp = sub.add_parser("bench", help="compare LabHQ with single-session baselines")
    bsub = bp.add_subparsers(dest="bench_cmd", required=True)
    bsub.add_parser("list", help="list benchmark cases")
    br = bsub.add_parser("run", help="run one case through selected benchmark arms")
    br.add_argument("case_id")
    br.add_argument("--engines", choices=["real", "mock"], default="real")
    br.add_argument("--dry-run", action="store_true", help="print commands without running them")
    br.add_argument("--output", help="result directory (default: $LABHQ_BENCH_DIR or ~/.labhq/bench)")
    selection = br.add_mutually_exclusive_group()
    selection.add_argument("--arms", help="comma-separated arms (default: labhq and configured baselines)")
    selection.add_argument("--arm", help=argparse.SUPPRESS)
    br.add_argument("--staff-model", action="append", metavar="FROM=TO",
                    help="replace Claude staff models; repeat for multiple mappings (default: opus=sonnet)")
    br.add_argument("--approve-budget-up-to", type=float, metavar="MULTIPLIER",
                    help="experimental labhq-only budget approvals up to this multiple of the case budget")
    bt = bsub.add_parser("test-agent", help="run every example job in order and summarize")
    bt.add_argument("--engines", choices=["real", "mock"], default="real")
    bt.add_argument("--output", help="result directory (default: $LABHQ_BENCH_DIR or ~/.labhq/bench)")
    bt.add_argument("--arms", help="comma-separated arms (default: labhq and configured baselines)")
    bt.add_argument("--staff-model", action="append", metavar="FROM=TO")
    bt.add_argument("--approve-budget-up-to", type=float, metavar="MULTIPLIER")
    breport = bsub.add_parser("report", help="combine the latest result for each arm of a case")
    breport.add_argument("case_id")
    breport.add_argument("--engines", choices=["real", "mock"], default="real")
    breport.add_argument("--output", help="result directory (default: $LABHQ_BENCH_DIR or ~/.labhq/bench)")
    bs = bsub.add_parser("rescore", help="recheck saved artifacts with the current case checks")
    bs.add_argument("case_id")
    rescoring = bs.add_mutually_exclusive_group()
    rescoring.add_argument("--run-id", help="saved run to rescore (default: latest run)")
    rescoring.add_argument("--all", action="store_true", help="rescore every saved run of this case")
    bs.add_argument("--output", help="result directory (default: $LABHQ_BENCH_DIR or ~/.labhq/bench)")
    sem = sub.add_parser("semantics", help="shadow semantics records: report | enable | mark (local only)")  # semantics-hook
    sem_sub = sem.add_subparsers(dest="semantics_cmd", required=True)  # semantics-hook
    sem_report = sem_sub.add_parser("report", help="requests, both models, auto-off history, removal proposal")  # semantics-hook
    sem_report.add_argument("--json", action="store_true")  # semantics-hook
    sem_report.add_argument("--today", help="YYYY-MM-DD (default: today)")  # semantics-hook
    sem_sub.add_parser("enable", help="show why semantics turned off and open a new epoch")  # semantics-hook
    sem_mark = sem_sub.add_parser("mark", help="mark a reuse candidate ref (sem:<8 hex>)")  # semantics-hook
    sem_mark.add_argument("request_id")  # semantics-hook
    sem_mark.add_argument("ref")  # semantics-hook
    sem_mark.add_argument("verdict", choices=["ok", "wrong_identity", "wrong_other", "irrelevant"])  # semantics-hook
    sem_act = sem_sub.add_parser("action", help="A2: the PI runs request.followup after a y/N (actions: confirm)")  # semantics-hook: actions
    act_sub = sem_act.add_subparsers(dest="action_cmd", required=True)  # semantics-hook: actions
    act_run = act_sub.add_parser("run", help="send one follow-up through the existing REST path, never resent")  # semantics-hook: actions
    act_run.add_argument("action")  # semantics-hook: actions
    act_run.add_argument("request_id")  # semantics-hook: actions
    act_run.add_argument("--text-file", help="UTF-8 file with the question (default: asked at the prompt)")  # semantics-hook: actions
    act_check = act_sub.add_parser("check", help="list runs, read completion, or close an unknown one")  # semantics-hook: actions
    act_check.add_argument("exec_id", nargs="?")  # semantics-hook: actions
    try:
        normalized = _normalize_instance_arg(list(sys.argv[1:] if argv is None else argv))
    except ValueError as exc:
        p.error(str(exc))
    args = p.parse_args(normalized)
    if args.instance and args.config:
        p.error("--instance cannot be used with --config")
    if args.instance:
        try:
            args.config = _instance_config(args.instance)
        except ValueError as exc:
            p.error(str(exc))
        # A typo must not fall back to the default settings and share its ports and state (#303 review).
        if args.cmd != "init" and not Path(args.config).is_file():
            p.error(f"instance '{args.instance}' is not initialised; run `labhq init --instance {args.instance}` first")
    if args.cmd == "demo" and (not math.isfinite(args.approve_timeout) or args.approve_timeout <= 0):
        p.error("--approve-timeout must be positive")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if args.cmd == "init":
        from .init_wizard import InitError, run
        from yaml import YAMLError

        try:
            result = run(args.config, yes=args.yes, dry_run=args.dry_run, force=args.force, instance=args.instance)
        except InitError as exc:
            p.exit(1, f"init: {exc}\n")
        except (OSError, ValueError, YAMLError):
            p.exit(1, "init: 설정을 읽거나 쓸 수 없습니다. 설정 파일과 권한을 확인하세요.\n")
        if result["summary"]["fail"]:
            raise SystemExit(1)
        return

    if args.cmd == "bench":
        requested = args.config or os.environ.get("LABHQ_CONFIG")
        if requested and not Path(requested).is_file():
            p.exit(2, f"labhq: 설정 파일이 없습니다: {requested}\n")
        from .bench import run_cli

        try:
            run_cli(args)
        except (ValueError, KeyError) as exc:
            p.error(str(exc))
        return
    try:
        s = Settings.load(args.config)
    except FileNotFoundError as exc:
        p.exit(2, f"labhq: {exc}\n")

    if args.cmd == "doctor":
        from .doctor import collect, render, save

        requested = args.config or os.environ.get("LABHQ_CONFIG")
        result = collect(s, requested_config=requested, network=args.network)
        if args.json:
            try:
                save(result, s)
            except OSError:
                result["checks"].append({"group": "config", "name": "manifest", "status": "fail",
                                         "detail": "could not write manifest",
                                         "hint": "Set runner.state_dir to a writable directory outside the repository."})
                result["summary"]["fail"] += 1
        print(render(result))
        if result["summary"]["fail"]:
            raise SystemExit(1)
    elif args.cmd == "gateway":
        import uvicorn

        from .gateway.server import create_app

        from .security import gateway_log_config

        try:
            app = create_app(s)
        except ValueError as exc:  # e.g. a retired research pack version (#170)
            p.exit(1, f"gateway: {exc}\n")
        if _default_client_token(s.gateway.client_token):
            p.exit(2, "gateway: gateway.client_token의 change-me 기본값을 바꾸세요.\n")
        # The web office asks for the client token; say how to get in without printing it (PI visit 2026-10-04).
        print(f"웹 사무실: {_http_base(s)}/  (다른 창에서 `{_config_command(s, 'open')}`을 실행하세요)",
              flush=True)
        uvicorn.run(app, host=s.gateway.host, port=s.gateway.port, log_level="info",
                    log_config=gateway_log_config())
    elif args.cmd == "up":
        _up(s)
    elif args.cmd == "down":
        _down(s)
    elif args.cmd == "open":
        _api(s, "GET", "/api/health")
        print(_open_web_office(s, args.config or os.environ.get("LABHQ_CONFIG"), three_d=args.three_d))
    elif args.cmd == "runner":
        from .runner.daemon import Runner

        clean = without_windows_app_aliases(dict(os.environ))
        path_key = next((key for key in clean if key.casefold() == "path"), None)
        if path_key:
            os.environ[path_key] = clean[path_key]
        asyncio.run(_run_runner_with_interrupts(Runner(s)))
    elif args.cmd == "status":
        health = _api(s, "GET", "/api/health")
        print("러너: " + (", ".join(health["runners"]) or "없음"))
        running = _api(s, "GET", "/api/requests?status=running&limit=200")
        print(f"진행 중 요청: {len(running)}")
        for req in running:
            progress = req["step_progress"]
            print(f"  {req['id']} {progress['done']}/{progress['total']} {req['text']}")
            environment = req.get("step_environment") or {}
            for sid, state in progress["steps"].items():
                problem = problem_text(environment.get(sid))
                print(f"    {sid}: {state}" + (f" — {problem}" if problem else ""))
            summary = req.get("cost_summary")
            if summary or req.get("cost_known") is False or req.get("cost_usd"):
                print(f"    비용: {cost_text(req.get('cost_usd'), req.get('cost_known'), summary)}"
                      + (f" {cost_detail(summary)}" if summary else ""))
        recent = _api(s, "GET", "/api/requests?status=all&limit=20")
        # Running requests show their environment problems above, beside the step.
        shown = {req["id"] for req in running}
        problems = [(req["id"], sid, problem_text(found)) for req in recent if req.get("id") not in shown
                    for sid, found in (req.get("step_environment") or {}).items()]
        if problems:
            print(f"환경 문제로 멈춘 단계: {len(problems)}")
            for rid, sid, problem in problems:
                print(f"  {rid} {sid}: {problem}")
        finished = [req for req in recent
                    if req.get("status") in {"done", "failed"} and
                    (req.get("bundle_path") or req.get("bundle_warning"))]
        if finished:
            print(f"최근 완료 요청: {len(finished)}")
            for req in finished:
                print(f"  {req['id']} {req.get('status')}")
                if req.get("bundle_path"):
                    print(f"    요청 묶음: {req['bundle_path']}")
                if req.get("bundle_warning"):
                    print(f"    경고: {req['bundle_warning']}")
        approvals = _api(s, "GET", "/api/approvals")
        print(f"승인 대기: {len(approvals)}")
        for approval in approvals:
            print(f"  {approval['id']} [{approval['kind']}] {approval['summary']}")
    elif args.cmd == "agents":
        for a in _api(s, "GET", "/api/agents"):
            kind = f"파견 ~{datetime.fromtimestamp(a['expires_at']):%m-%d}" if a.get("expires_at") else "정규"
            print(f"{ICON.get(a['id'], '🐥')} {a['id']:<16} {a['name']:<18} {a['engine']:<11} {a.get('model') or '-':<8} {kind}")
    elif args.cmd == "send":
        from .intake import infer_reference

        references = []
        for raw in args.ref:
            reference = infer_reference(raw)
            if reference is None:
                p.error(f"--ref {raw!r}: kind unclear; prefix one of github: doi: pmid: url: path:")
            references.append(reference)
        if args.agent and (args.plan_only or args.cso_model or args.team):
            p.error("--agent cannot be used with --plan-only, --cso-model or --team")
        body = {"text": args.text, "mode": "direct" if args.agent else "plan_only" if args.plan_only else "orchestrate",
                "agent_id": args.agent, "cso_model": args.cso_model, "route": "team" if args.team else "auto",
                "project_dirs": args.project_dir, "budget_usd": args.budget, "project_id": args.project,
                "references": references, "default_references": not args.no_default_refs}
        if args.no_wait:
            print(_api(s, "POST", "/api/requests", json=body))
        else:
            asyncio.run(_send_and_wait(s, body))
    elif args.cmd == "note":
        print(_api(s, "POST", f"/api/requests/{args.request_id}/notes", json={"text": args.text}))
    elif args.cmd == "resume":
        print(_api(s, "POST", f"/api/requests/{args.request_id}/steps/{args.step_id}/resume-quota", json={}))
    elif args.cmd == "watch":
        asyncio.run(_watch(s))
    elif args.cmd == "projects":
        for pr in _api(s, "GET", "/api/projects"):
            print(f"{pr['id']:<16} {pr.get('repo') or '-':<32} {pr['visibility']:<8} "
                  f"issues={pr['issues']} reports={pr['commit_reports']}")
    elif args.cmd == "codex-review":
        print(_api(s, "POST", f"/api/projects/{args.project}/prs/{args.pr}/codex-review", json={"note": args.note}))
    elif args.cmd == "approvals":
        for a in _api(s, "GET", "/api/approvals"):
            print(f"{a['id']}  [{a['kind']}] {a['summary']}")
    elif args.cmd == "approve":
        if args.deny and args.choice not in (None, "deny"):
            p.error(f"--deny contradicts --choice {args.choice}")
        pending = next((a for a in _api(s, "GET", "/api/approvals") if a.get("id") == args.id), None)
        evidence = bool(pending and pending.get("kind") == "research_evidence")
        if evidence and args.choice is None and not args.deny:
            p.error("CP2 evidence review needs --choice approve, --choice revise or --choice deny")
        if args.choice and pending and not evidence:
            p.error(f"--choice is only for CP2 evidence review; {args.id} is {pending.get('kind')}")
        if pending and pending.get("kind") == "clarify" and not args.deny and not args.note.strip():
            questions = (pending.get("detail") or {}).get("questions") or []
            lines = []
            for index, question in enumerate(questions, 1):
                text = question.get("question") if isinstance(question, dict) else str(question)
                lines.append(f"{index}. {text}")
            if not lines:
                lines = [str(pending.get("summary") or "PI 질문에 답해 주세요.")]
            p.exit(2, "clarify 카드는 --note 답변이 필요합니다. 요청은 그대로 유지됩니다.\n질문:\n" +
                   "\n".join(lines) + f'\n예: labhq approve {args.id} --note "1: a, 2: b"\n')
        body = {"approved": args.choice == "approve" if args.choice else not args.deny, "note": args.note}
        if args.choice:
            body["choice"] = args.choice
        print(_api(s, "POST", f"/api/approvals/{args.id}", json=body))
    elif args.cmd == "verify":
        raise SystemExit(_verify(s, args.request_id, as_json=args.json, bundle=args.bundle))
    elif args.cmd == "recruit":
        print(_api(s, "POST", "/api/recruit", json={"paper": args.paper, "repo": args.repo, "focus": args.focus,
                                                    "ttl_days": args.ttl, "name": args.name}))
    elif args.cmd == "contract":
        body = {"action": args.action, "days": args.days, "slug": args.target if args.action == "rehire" else None}
        print(_api(s, "POST", f"/api/contracts/{args.target}", json=body))
    elif args.cmd == "talent":
        from .registry import Registry

        reg = Registry(s.path(s.runner.agents_dir), s.path(s.runner.talent_dir),
                       s.path(s.runner.contract_dir) if s.runner.contract_dir else None)
        for spec in reg.talent_pool():
            c = spec.contract
            print(f"🐥 {Path(c.talent_dir).name if c and c.talent_dir else spec.id:<24} {spec.name:<24} "
                  f"{c.kind if c else ''}  paper={c.paper if c else ''}")
    elif args.cmd == "setup-paper2agent":
        from .recruit.paper2agent import install_skill

        for d in install_skill(s.recruit.skill_source, Path("~/.labhq/cache").expanduser()):
            print(f"installed → {d}")
    elif args.cmd == "semantics":  # semantics-hook
        from .research.semantics_shadow import run_cli as semantics_cli  # semantics-hook
        raise SystemExit(semantics_cli(args, s))  # semantics-hook
    elif args.cmd == "demo":
        logging.getLogger().setLevel(logging.WARNING)
        asyncio.run(_demo(args.web or args.phone, args.port, args.phone,
                          args.host or ("0.0.0.0" if args.phone else "127.0.0.1"), args.approve_timeout))


if __name__ == "__main__":
    main(sys.argv[1:])
