from __future__ import annotations

import argparse
import asyncio
import hashlib
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
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from . import instance_guard, proc_tree
from .costs import cost_detail, cost_text
from .facilities.signatures import problem_text
from .process_diagnostics import SUPERVISOR_SUFFIX
from .request_status import is_terminal_request, request_display_label, request_status_label
from .settings import Settings
from .test_guard import guard_test_path
from .util import atomic_write_text, free_port, short, without_windows_app_aliases

REPO = Path(__file__).resolve().parents[1]
ICON = {"cso": "🦉", "chief_of_staff": "🐧", "biologist": "🐻", "data_steward": "🐿️", "lit_scout": "🦊",
        "analyst": "🦝", "engineer": "🐙", "qc_reviewer": "🦔", "sci_reviewer": "🐢",
        "recruiter": "카피바라",
        "editor": "🦫", "bioinfo-agent": "🦦"}
STATE_KO = {"working": "작업 중", "waiting": "승인 대기", "hibernating": "HPC 대기(수면)", "done": "완료", "error": "오류"}
_OPERATION_LOCKS: dict[str, threading.Lock] = {}
_OPERATION_LOCKS_GUARD = threading.Lock()
_TERMINATE_GRACE_SECONDS = 10.0
_TERMINATE_POLL_SECONDS = 0.1
_DOWN_RECHECK_SECONDS = 3.0
_BUNDLE_WAIT_SECONDS = 60.0
_BUNDLE_POLL_SECONDS = 3.0
# How long `up` waits for a new supervisor to pin its ancestors and record the role it started (#589).
_CHILD_RECORD_WAIT_SECONDS = 20.0


def _instance_config(name: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", name):
        raise ValueError("--instance must use 1-64 letters, numbers, underscores or hyphens")
    path = Path.home() / ".labhq" / name / "labhq.yaml"
    guard_test_path(path)
    return str(path)


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


def _question_options(approval: dict) -> list[str]:
    """A hard-stop question card's options as "a) …" lines, the way the web card letters them (R19, decide.js).

    Up to 12 non-blank options; a letter the employee already wrote ("a) GRCh38") is not doubled (#331)."""
    detail = approval.get("detail") or {}
    if approval.get("kind") != "question" or not isinstance(detail, dict) or not isinstance(detail.get("options"), list):
        return []
    options = [re.sub(r"^\(?[a-lA-L]\)\s*(?=\S)", "", o.strip())
               for o in detail["options"] if isinstance(o, str) and o.strip()][:12]
    return [f"{'abcdefghijkl'[index]}) {option}" for index, option in enumerate(options)]


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
        # A card nobody answered is not a refusal (R5), and CP1/CP2 수정 요청 is not one either (#521).
        unanswered = ("시간 초과" if d.get("state") == "timed_out" else
                      "답 없이 닫힘" if d.get("state") in {"expired", "cancelled"} else "")
        label = unanswered or ("수정 요청" if d.get("choice") == "revise" else "허가" if d.get("approved") else "거절")
        line = f"{'⏰' if unanswered else '✅'} 승인 결과: {label} {d.get('note', '')}"
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
        line = f"카피바라 인사팀: {d.get('slug')} {d.get('stage')}"
    elif t == "recruit.done":
        ag = d.get("agent", {})
        line = f"🐥 파견직 입사: {ag.get('id')} ({ag.get('name')}) · 수습통과={d.get('passed_probation')}"
    elif t == "recruit.failed":
        line = f"카피바라 채용 실패: {d.get('error')}"
    elif t == "contract.warning":
        line = f"⚠️ 재고용 {d.get('agent_id')}: {', '.join(d.get('changed') or [])} hash가 채용 때와 다름"
    elif t == "request.completed":
        summary = d.get("cost_summary")
        label = "보고서 검토 대기 (CP4)" if d.get("status") == "review_pending" else "요청 완료"
        line = f"🏁 {label} (ok={d.get('ok')}, cost={cost_text(d.get('cost_usd'), d.get('cost_known'), summary)})"
        if d.get("status") == "review_pending" and (final := d.get("final_checkpoint")):
            line += (f"\n   검토 후: labhq review-done {ev.get('request_id')} --revision {final['revision']}"
                     f" --sha256 {final['target_sha256']} (거부: --reject)")
        if summary and (summary.get("estimated_usd") or summary.get("unknown_count") or summary.get("warnings")):
            line += f"\n   {cost_detail(summary)}"
    elif t == "request.failed":
        line = f"💥 요청 실패: {d.get('error')}"
    elif t == "request.reviewed":
        final = d.get("final_checkpoint") or {}
        decision = (final.get("receipt") or {}).get("decision")
        line = f"보고서 검토: {decision or '확인 완료'} · {request_status_label(d.get('status'))}"
    elif t == "request.status":
        line = f"⏸ {ev.get('request_id')} 상태: {request_status_label(d.get('status'))}"
    elif t == "request.runner_wait":
        line = f"🔌 {d.get('message') or '러너를 기다립니다'}"
    elif t == "request.bundle":
        line = (f"📦 요청 묶음: {d['bundle_path']}" if d.get("bundle_path") else
                f"⚠️ 요청 묶음: {d.get('bundle_warning', '만들지 못함')}")
        if d.get("bundle_grade"):
            reasons = "; ".join(map(str, d.get("bundle_grade_reasons") or []))
            line += f"\n   재현 등급: {d['bundle_grade']}" + (f" ({reasons})" if reasons else "")
    if t in {"request.completed", "request.failed"} and line:
        if d.get("bundle_path"):
            line += f"\n   요청 묶음: {d['bundle_path']}"
        if d.get("bundle_grade"):
            reasons = "; ".join(map(str, d.get("bundle_grade_reasons") or []))
            line += f"\n   재현 등급: {d['bundle_grade']}" + (f" ({reasons})" if reasons else "")
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


TASK_CONTROL_DONE = {"steer": "방향 수정을 보냈습니다", "pause": "일시 정지를 보냈습니다", "resume": "재개를 보냈습니다",
                     "halt": "멈춤을 보냈습니다"}


def task_control(s: Settings, action: str, task_id: str, text: str | None = None) -> str:
    """`labhq task steer|pause|resume|halt`: the same REST call as the staff sheet's buttons (#59 ①)."""
    from urllib.parse import quote

    if action == "steer" and not (text or "").strip():
        raise SystemExit("labhq task steer <task_id> \"<방향 수정>\" — 보낼 내용을 적어 주세요")
    out = _api(s, "POST", f"/api/tasks/{quote(task_id, safe='')}/{action}",
               json={"text": text} if action == "steer" else {})
    if out.get("resumed_halted"):
        return f"{task_id}: 멈췄던 작업을 재개합니다 · 같은 세션으로 다음 turn을 보냅니다"
    where = ("" if out.get("sent", True) else " · 러너가 꺼져 있어 다시 연결되면 전달합니다" if action == "halt"
             else " · 같은 단계의 다음 turn에 적용합니다")
    return f"{task_id}: {TASK_CONTROL_DONE[action]}{where}"


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
            message = (f"gateway에 연결할 수 없습니다: {_http_base(s)} — "
                       f"`{_config_command(s, 'up')}`을 실행하세요.")
        print(message, file=sys.stderr)
        raise SystemExit(2) from None
    except ValueError:
        if raw_errors:
            raise
        print("gateway 응답을 읽을 수 없습니다.", file=sys.stderr)
        raise SystemExit(2) from None


PAPER_SKILL_RECEIPT_MAX_BYTES = 256 * 1024  # a ready receipt holds ids, hashes and one fulltext receipt


def _talent_text(value, n: int = 120) -> str:
    """One printable line: a receipt value never moves the cursor or floods the terminal."""
    return short("".join(c if c.isprintable() else " " for c in str(value)), n)


def talent_lines(registry) -> list[str]:
    """`labhq talent`: past contracts, then paper skills built by Paper2Skill (#660).

    A built paper skill leaves only talent/paper-skill-<key>/paper-skill.json, no contract.yaml, so the contract
    pool alone never listed it. A line says the skill was built; whether a step used it or it helped is recorded per
    request (run record/appendix), not here. A receipt that does not belong to its folder or lacks a field gets one
    line saying so; its values are not echoed."""
    from .recruit.paper_skills import paper_id

    lines, contract_skills = [], set()
    for spec in registry.talent_pool():
        c = spec.contract
        if c and c.skill_sha256:
            contract_skills.add((paper_id(c.paper), c.skill_sha256))
        lines.append(f"🐥 {Path(c.talent_dir).name if c and c.talent_dir else spec.id:<24} {spec.name:<24} "
                     f"{c.kind if c else ''}  paper={c.paper if c else ''}"
                     f"  commit={(c.source_commit or '-')[:7] if c else '-'}"
                     f"  external={','.join(x.host for x in c.external_services) if c and c.external_services else '-'}")
    for folder in sorted(registry.talent_dir.glob("paper-skill-*")):
        receipt = folder / "paper-skill.json"
        if not folder.is_dir() or (folder / "contract.yaml").exists() or not receipt.is_file():
            continue  # listed above as a contract, or no build finished here (failures write no receipt)
        name = _talent_text(folder.name, 40)
        try:
            if receipt.is_symlink() or receipt.stat().st_size > PAPER_SKILL_RECEIPT_MAX_BYTES:
                raise ValueError
            row = json.loads(receipt.read_text(encoding="utf-8"))
            pid = paper_id(row.get("paper_id")) if isinstance(row, dict) else None
            digest = row.get("skill_sha256") if isinstance(row, dict) else None
            if (not pid or row.get("status") != "ready" or not isinstance(digest, str)
                    or not re.fullmatch(r"[0-9a-f]{64}", digest)):
                raise ValueError
        except (OSError, UnicodeError, ValueError):
            lines.append(f"📄 {name:<24} 논문 스킬 기록 불완전·손상(표시 생략)")
            continue
        if folder.name != "paper-skill-" + hashlib.sha256(pid.encode()).hexdigest()[:24]:
            lines.append(f"📄 {name:<24} 논문 스킬 기록이 폴더와 다른 논문을 가리킴(표시 생략)")
            continue
        if (pid, digest) in contract_skills:
            continue  # the same skill already appears as a contract line
        commit = row.get("paper2agent_commit")
        commit = commit[:7] if isinstance(commit, str) and re.fullmatch(r"[0-9a-f]{40}", commit) else "-"
        lines.append(f"📄 {name:<24} 논문 자문 스킬 생성됨  paper={_talent_text(pid)}  skill={digest[:12]}"
                     f"  commit={commit}  사용·효용=요청별 기록 참조")
    return lines


def _run_websocket(s: Settings, command) -> None:
    """Run a command that holds the gateway's client websocket (`send` without --no-wait, `watch`). A gateway that is
    down, refuses the token or stops midway gives one line and exit 2 like _api, not a traceback (docs audit 10-09)."""
    from websockets.exceptions import ConnectionClosed, InvalidHandshake, InvalidStatus

    try:
        asyncio.run(command)
    except InvalidStatus as exc:
        status = getattr(exc.response, "status_code", None)
        print(f"gateway가 연결을 거부했습니다(HTTP {status}). gateway.client_token이 gateway 설정과 같은지 "
              "확인하세요." if status in (401, 403) else f"gateway가 연결을 거부했습니다(HTTP {status}).",
              file=sys.stderr)
        raise SystemExit(2) from None
    except ConnectionClosed as exc:
        if exc.rcvd is not None and exc.rcvd.code == 1008:  # the gateway accepts, then closes a wrong token
            print("gateway가 client_token을 거부했습니다. gateway.client_token이 gateway 설정과 같은지 확인하세요.",
                  file=sys.stderr)
        else:
            print("gateway 연결이 끊겼습니다(gateway가 멈췄거나 다시 시작됨). 요청은 gateway에서 계속됩니다. "
                  f"`{_config_command(s, 'watch')}`로 다시 보세요.", file=sys.stderr)
        raise SystemExit(2) from None
    except (OSError, InvalidHandshake):  # refused, unreachable, timed out, or not a labhq gateway
        print(f"gateway에 연결할 수 없습니다: {_http_base(s)} — `{_config_command(s, 'up')}`을 실행하세요.",
              file=sys.stderr)
        raise SystemExit(2) from None


def _default_client_token(token: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", token.casefold())
    return normalized in {"changeme", "changemeclient"}


def _config_command(s: Settings, command: str) -> str:
    launcher = os.environ.get("LABHQ_LAUNCHER") or "labhq"
    return f'{launcher} -c "{s.config_path}" {command}' if s.config_path else f"{launcher} {command}"


def _authenticated_requests(s: Settings, *, limit: int) -> list[dict] | None:
    import httpx

    try:
        value = _api(s, "GET", f"/api/requests?status=running&limit={limit}", raw_errors=True)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 401:
            print("client_token이 이 gateway와 다릅니다.", file=sys.stderr)
            raise SystemExit(2) from None
        return None
    except (httpx.HTTPError, ValueError):
        return None
    return value if isinstance(value, list) and all(isinstance(row, dict) for row in value) else None


def _active_requests(s: Settings) -> list[dict] | None:
    return _authenticated_requests(s, limit=200)


def _request_cost_totals_overview(totals: dict) -> str:
    text = f"${float(totals.get('cost_usd') or 0):.2f}"
    estimated = float(totals.get("estimated_usd") or 0)
    unknown = int(totals.get("unknown_count") or 0)
    legacy_unknown = totals.get("legacy_unknown") is True
    if estimated:
        text += f" (추정 ${estimated:.2f} 포함)"
    if unknown:
        text += f" + 비용 모름 {unknown}건" + (" 이상" if legacy_unknown else "")
    elif legacy_unknown:
        text += " + 비용 모름"
    return text


def _health(s: Settings) -> dict | None:
    import httpx

    if _authenticated_requests(s, limit=1) is None:
        return None
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


def _supervisor_role(role: str) -> str:
    """The record name of a role's supervisor: `labhq-<role>-supervisor.pid` (#589)."""
    return role + SUPERVISOR_SUFFIX


def _stopping_file(s: Settings, role: str) -> Path:
    return _launch_state(s) / f"labhq-{role}.stopping"


def _mark_stopping(s: Settings, role: str) -> None:
    """The role is being stopped on purpose: its supervisor must not restart it. `up` lifts this when it starts the
    role again. Written before the supervisor or the role is touched."""
    _stopping_file(s, role).parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(_stopping_file(s, role), datetime.now().astimezone().isoformat(timespec="seconds") + "\n")


def _clear_stopping(s: Settings, role: str) -> None:
    try:
        _stopping_file(s, role).unlink(missing_ok=True)
    except OSError:
        pass


def _operation_lock_file(s: Settings) -> Path:
    return _launch_state(s) / "labhq-up-down.lock"


def _operation_busy() -> None:
    print("다른 up/down이 진행 중입니다.", file=sys.stderr)
    raise SystemExit(2)


@contextmanager
def _operation_lock(s: Settings):
    """Hold one non-blocking up/down lock for this instance across processes and threads."""
    path = _operation_lock_file(s)
    path.parent.mkdir(parents=True, exist_ok=True)
    key = str(path.resolve()).casefold() if os.name == "nt" else str(path.resolve())
    with _OPERATION_LOCKS_GUARD:
        local_lock = _OPERATION_LOCKS.setdefault(key, threading.Lock())
    if not local_lock.acquire(blocking=False):
        _operation_busy()

    handle = None
    locked = False
    try:
        handle = os.fdopen(os.open(path, os.O_RDWR | os.O_CREAT, 0o600), "r+b")
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                _operation_busy()
        else:
            import fcntl

            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                _operation_busy()
        locked = True
        record = json.dumps({
            "pid": os.getpid(),
            "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        }, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
        handle.seek(0)
        handle.write(record)
        handle.truncate()
        handle.flush()
        yield
    finally:
        if locked and handle is not None:
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        if handle is not None:
            handle.close()
        local_lock.release()


class ProcessIdentityError(RuntimeError):
    pass


def _process_created(pid: int) -> str | None:
    """Return an OS process-generation identifier, None only when the PID does not exist."""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class FILETIME(ctypes.Structure):
            _fields_ = [("low", wintypes.DWORD), ("high", wintypes.DWORD)]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            error = ctypes.get_last_error()
            if error in {87, 1168}:  # invalid parameter / not found
                return None
            raise ProcessIdentityError(f"Windows에서 PID {pid} 생성 시각을 읽을 수 없음(오류 {error})")
        created, exited, kernel, user = FILETIME(), FILETIME(), FILETIME(), FILETIME()
        exit_code = wintypes.DWORD()
        try:
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                error = ctypes.get_last_error()
                raise ProcessIdentityError(f"Windows에서 PID {pid} 상태를 읽을 수 없음(오류 {error})")
            if exit_code.value != 259:  # STILL_ACTIVE
                return None
            if not kernel32.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited),
                                            ctypes.byref(kernel), ctypes.byref(user)):
                error = ctypes.get_last_error()
                raise ProcessIdentityError(f"Windows에서 PID {pid} 생성 시각을 읽을 수 없음(오류 {error})")
        finally:
            kernel32.CloseHandle(handle)
        return f"windows:{(created.high << 32) | created.low}"

    proc = Path("/proc")
    if proc.is_dir():
        stat_path = proc / str(pid) / "stat"
        try:
            raw = stat_path.read_text(encoding="ascii")
        except (FileNotFoundError, ProcessLookupError):
            return None  # a process in the middle of exiting answers ESRCH, not ENOENT: it is gone either way
        except OSError as exc:
            raise ProcessIdentityError(f"/proc에서 PID {pid} 생성 시각을 읽을 수 없음: {exc}") from None
        end = raw.rfind(")")
        fields = raw[end + 2:].split() if end >= 0 else []
        if len(fields) <= 19:
            raise ProcessIdentityError(f"/proc에서 PID {pid} 생성 시각 형식을 읽을 수 없음")
        if fields[0] == "Z":
            return None
        return f"proc:{fields[19]}"

    try:
        result = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True,
                                timeout=3, env={**os.environ, "LC_ALL": "C"})
    except (OSError, subprocess.SubprocessError) as exc:
        raise ProcessIdentityError(f"ps로 PID {pid} 생성 시각을 읽을 수 없음: {exc}") from None
    created = " ".join(result.stdout.split())
    if result.returncode != 0 and not created:
        return None
    if not created:
        raise ProcessIdentityError(f"ps가 PID {pid} 생성 시각을 돌려주지 않음")
    return f"ps:{created}"


def _process_record(s: Settings, role: str, *, cleanup: bool = True) -> tuple[str, dict | None, str | None]:
    """Return alive/missing/stale/unknown. Stale records are removed without touching a process."""
    path = _pid_file(s, role)
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return "missing", None, None
    except OSError as exc:
        return "unknown", None, f"프로세스 기록을 읽을 수 없음: {exc}"
    except (ValueError, TypeError):
        try:
            if cleanup:
                path.unlink(missing_ok=True)
        except OSError as exc:
            return "unknown", None, f"잘못된 프로세스 기록을 지울 수 없음: {exc}"
        return "stale", None, None
    valid = (isinstance(record, dict) and isinstance(record.get("pid"), int)
             and record.get("pid", 0) > 0 and record.get("role") == role
             and isinstance(record.get("created"), str))
    if not valid:
        try:
            if cleanup:
                path.unlink(missing_ok=True)
        except OSError as exc:
            return "unknown", record if isinstance(record, dict) else None, f"stale 기록을 지울 수 없음: {exc}"
        return "stale", None, None
    try:
        created = _process_created(record["pid"])
    except ProcessIdentityError as exc:
        return "unknown", record, str(exc)
    if created is None or created != record["created"]:
        try:
            if cleanup:
                path.unlink(missing_ok=True)
        except OSError as exc:
            return "unknown", record, f"stale 기록을 지울 수 없음: {exc}"
        return "stale", record, None
    if record.get("config") != s.config_path:
        return "unknown", record, "다른 설정 파일로 실행 중입니다. 실행할 때 쓴 설정 파일을 확인하세요."
    return "alive", record, None


def _exit_text(row: dict) -> str:
    """One `child_exit` row from a supervisor, as `status` prints it: when, the exit code, and whether `down` asked."""
    code = f"code={row.get('code')}"
    if row.get("code_hex"):
        code += f" ({row['code_hex']})"
    if row.get("signal"):
        code += f" {row['signal']}"
    extra = [f"PID {row.get('child_pid')}"]
    if row.get("uptime_s") is not None:
        extra.append(f"가동 {row['uptime_s']}초")
    if row.get("stop_requested"):
        extra.append("종료 요청(down·웹 종료)")
    return f"{row.get('time')} {code} ({', '.join(extra)})"


def _supervisor_detail(s: Settings, role: str, logs: Path, child_state: str) -> tuple[str, bool]:
    """What `status` and `doctor` say about a role's supervisor and the last end it recorded (#589), and whether that
    is a warning even though the role runs: its supervisor's record exists but the process is gone, or its identity
    cannot be read (another config's, or not openable), so nobody knows the role is protected."""
    from .process_diagnostics import latest_child_exit

    state, record, problem = _process_record(s, _supervisor_role(role), cleanup=False)
    unprotected = False
    if state == "alive" and record:
        text = f"감독 PID {record['pid']} 실행 중"
    elif state == "unknown":
        text = f"감독 신원 확인 불가 — {problem}"
        unprotected = True
    elif state == "stale":
        text = f"감독 PID {record['pid']} 프로세스 없음" if record else "감독 기록이 잘못됨"
        unprotected = child_state == "alive"
    else:
        text = "감독 없음" + ("(감독 도입 전 방식으로 시작됨: down 뒤 up으로 다시 시작하면 적용)"
                              if child_state == "alive" else "")
    last = latest_child_exit(logs, role)
    return text + ("; 마지막 종료 " + _exit_text(last) if last else "; 마지막 종료 기록 없음"), unprotected


def _local_process_diagnostics(s: Settings) -> list[dict]:
    from .process_diagnostics import latest_records

    rows = []
    logs = _launch_state(s) / "logs"
    for role in ("gateway", "runner"):
        state, record, problem = _process_record(s, role, cleanup=False)
        if state == "missing" and _process_record(s, _supervisor_role(role), cleanup=False)[0] != "alive":
            continue  # Remote/foreground daemons need no local PID record.
        detail = f"{role}: "
        if state == "unknown":
            detail += f"프로세스 신원 확인 불가 — {problem}"
        elif record:
            # `up` records the launcher (a venv's python.exe), whose real-interpreter child writes the diagnostics.
            children = (proc_tree.child_pids(record["pid"], created=proc_tree.parse_created(record["created"]))
                        if state == "alive" else [])
            latest = latest_records(logs, role, record["pid"], also=children)
            exited = latest.get("exit")
            detail += f"PID {record['pid']} " + ("실행 중" if state == "alive" else
                "프로세스 없음(비정상 종료 의심)" if not exited else "프로세스 없음(종료 기록 있음)")
            heartbeat = latest.get("heartbeat")
            if heartbeat:
                detail += (f"; heartbeat {heartbeat.get('time')} RSS={heartbeat.get('rss_bytes')}"
                           f" handles={heartbeat.get('handles')}")
            else:
                detail += "; heartbeat 기록 없음"
            detail += (f"; exit {exited.get('time')} reason={exited.get('reason')} code={exited.get('code')}"
                       if exited else "; exit 기록 없음")
        elif state == "missing":
            detail += "프로세스 기록 없음(감독이 다시 시작하는 중일 수 있음)"
        else:
            detail += "잘못된 PID 기록"
        supervision, unprotected = _supervisor_detail(s, role, logs, state)
        detail += "; " + supervision
        rows.append({"group": "process", "name": role,
                     "status": "ok" if state == "alive" and not unprotected else "warn",
                     "detail": detail, "hint": "state/logs의 diagnostics.jsonl·fault.log·일반 로그를 확인하세요."})
    return rows


def _record_problem(role: str, reason: str | None) -> None:
    print(f"{role} 프로세스 신원을 확인할 수 없어 건드리지 않습니다: {reason or '알 수 없는 이유'}",
          file=sys.stderr)
    raise SystemExit(2)


def _launch_options(s: Settings) -> dict:
    """What every detached labhq process of `up` shares: no console, no stdin, stderr into stdout, a fresh process
    group or session so a signal sent to the starter's group never reaches it."""
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONFAULTHANDLER"] = "1"  # Also cover interpreter/import failures before role hooks install.
    options = {
        "cwd": str(REPO), "stdin": subprocess.DEVNULL, "stderr": subprocess.STDOUT,
        "env": env, "close_fds": True,
    }
    if sys.platform == "win32":
        from .windows_launch import BREAKAWAY, CHECK_ENV
        env[CHECK_ENV] = "1"
        options["creationflags"] = (getattr(subprocess, "DETACHED_PROCESS", 0x00000008) |
                                    getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200) | BREAKAWAY)
    else:
        options["start_new_session"] = True
    return options


def _child_launch(s: Settings, role: str) -> tuple[list[str], dict]:
    """The command and Popen options for the gateway or runner itself. The role's supervisor starts it, not `up`
    (#589), so its recorded parent is a process that lives as long as it does."""
    options = _launch_options(s)
    if role == "runner":
        options["env"] = without_windows_app_aliases(options["env"])
    command = [sys.executable, "-m", "labhq.cli"]
    if s.config_path:
        command += ["-c", s.config_path]
    command.append(role)
    return command, options


def _supervisor_launch(s: Settings, role: str) -> tuple[list[str], dict]:
    options = _launch_options(s)
    if role == "runner":
        options["env"] = without_windows_app_aliases(options["env"])
    return [sys.executable, "-m", "labhq.supervisor", s.config_path or "", role], options


def _await_child_record(s: Settings, role: str, *, supervisor_pid: int, supervisor_created: str,
                        timeout: float | None = None) -> None:
    """Wait until the role's supervisor has recorded the process it started, or fail the start.

    The supervisor pins its own ancestors and then starts and records the role, so a record means both are done.
    `up` is one of those ancestors and must still be alive while it happens: a PID that is already free cannot be
    held (#589). A supervisor that ended first, or never recorded anything, is stopped; its record is removed only
    after cleanup succeeds, so a failed cleanup can be retried (#627)."""
    supervisor = _supervisor_role(role)
    deadline = time.monotonic() + (_CHILD_RECORD_WAIT_SECONDS if timeout is None else timeout)
    while True:
        state, _record, _reason = _process_record(s, role, cleanup=False)
        if state == "alive":
            return
        try:
            still = _process_created(supervisor_pid) == supervisor_created
        except ProcessIdentityError:
            still = True  # unreadable is not gone: keep waiting for the record, then give up at the deadline
        if not still:
            reason = "감독 프로세스가 먼저 끝났습니다"
            break
        if time.monotonic() >= deadline:
            reason = f"감독 프로세스가 {role} 기록을 남기지 않았습니다"
            break
        time.sleep(0.1)
    try:
        _terminate_tree(supervisor_pid, created=supervisor_created)
    except OSError as exc:
        reason += (f"; {supervisor} PID {supervisor_pid} 정리 실패: {exc}; "
                   "다시 시도할 수 있게 PID 기록을 유지했습니다")
    else:
        _pid_file(s, supervisor).unlink(missing_ok=True)
    print(f"{role}을 시작하지 못했습니다: {reason}. 로그: {_launch_state(s) / 'logs' / (supervisor + '.log')}",
          file=sys.stderr)
    raise SystemExit(2)


def _start_background(s: Settings, role: str, *, popen=None) -> int:
    """Start the gateway or runner under its own detached supervisor and return the supervisor's PID (#589).

    `up` exits once everything answers, so a process `up` started directly would keep naming the dead `up` as its
    parent, and Windows can give that PID to a stranger that something later kills by parent PID alone. The
    supervisor outlives `up`, starts the role as its own child and records how it ended; `up` records the
    supervisor, which records the role."""
    s.runner.validate_wsl()
    state = _launch_state(s)
    logs = state / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    supervisor = _supervisor_role(role)
    _clear_stopping(s, role)
    command, options = _supervisor_launch(s, role)
    from .windows_launch import LaunchError, spawn
    try:
        with (logs / f"{supervisor}.log").open("a", encoding="utf-8") as output:
            process = spawn(command, state=state, role=supervisor, popen=popen, stdout=output, **options)
    except LaunchError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2) from None
    try:
        created = _process_created(process.pid)
    except ProcessIdentityError as exc:
        try:
            _terminate_tree(process.pid)
        except OSError:
            if hasattr(process, "terminate"):
                process.terminate()
        _record_problem(supervisor, str(exc))
    if created is None:
        try:
            _terminate_tree(process.pid)
        except OSError:
            if hasattr(process, "terminate"):
                process.terminate()
        _record_problem(supervisor, f"띄운 PID {process.pid}이 이미 종료됨")
    record = {"pid": int(process.pid), "role": supervisor, "created": created, "config": s.config_path}
    atomic_write_text(_pid_file(s, supervisor), json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
    _await_child_record(s, role, supervisor_pid=int(process.pid), supervisor_created=created)
    from .windows_launch import outcome
    outcome(state, supervisor, "recorded", child_pid=int(process.pid))
    return int(process.pid)


def _supervised(s: Settings, role: str) -> bool:
    """The role's supervisor is running (it may be between a restart's end and its next start)."""
    return _process_record(s, _supervisor_role(role))[0] == "alive"


def _up_unlocked(s: Settings, *, popen=None, make_primary: bool = False) -> None:
    s.runner.validate_wsl()
    if _default_client_token(s.gateway.client_token):
        print("gateway 시작 거부: gateway.client_token의 change-me 기본값을 바꾸세요.", file=sys.stderr)
        raise SystemExit(2)
    # Another config on the same state folder or port is named, with the command that fixes it (#613). A live one
    # stops `up`: it would otherwise report that other config's gateway as this one.
    notices = instance_guard.check(s)
    for notice in notices:
        print(notice.render(), file=sys.stderr)
    if any(notice.blocking for notice in notices):
        raise SystemExit(2)
    # A healthy server can share the port and state directory while using another config (#559).
    # Do not silently claim the requested settings are active, or delete the other process's record.
    for role in ("gateway", "runner"):
        for name in (role, _supervisor_role(role)):
            state, _record, reason = _process_record(s, name)
            if state == "unknown":
                _record_problem(name, reason)
    confirmed = True

    def verify(answer: dict, *, started_here: bool) -> None:
        """Every gateway answer is checked for whose it is before `up` relies on it (#613): another identity stops
        `up`, and only a confirmed gateway is recorded."""
        nonlocal confirmed
        found, ours = instance_guard.check_health(s, answer, started_here=started_here)
        for notice in found:
            print(notice.render(), file=sys.stderr)
        if any(notice.blocking for notice in found):
            raise SystemExit(2)
        confirmed = confirmed and ours

    try:
        health = _health(s)
    except SystemExit:  # 401: _authenticated_requests already said the token differs; say whose gateway it is
        for notice in instance_guard.check(s, token_mismatch=True):
            if notice.key == "port":
                print(notice.render(), file=sys.stderr)
        raise
    if health is not None:
        verify(health, started_here=False)  # something answered before this `up` started anything
    if health is None:
        state, _record, reason = _process_record(s, "gateway")
        if state == "unknown":
            _record_problem("gateway", reason)
        if state == "alive":
            health = _wait_for_health(s, runner=False, timeout=30)
            if health is None:
                print(f"gateway 프로세스는 살아 있으나 인증된 상태를 확인할 수 없음 — 로그: "
                      f"{_launch_state(s) / 'logs' / 'gateway.log'}", file=sys.stderr)
                raise SystemExit(2)
        elif _supervised(s, "gateway"):
            # Its supervisor is restarting it (settings: supervisor.restart); a second supervisor would race it.
            health = _wait_for_health(s, runner=False, timeout=30)
            if health is None:
                print(f"gateway 감독 프로세스는 살아 있으나 gateway가 응답하지 않음 — 로그: "
                      f"{_launch_state(s) / 'logs' / 'gateway.log'}", file=sys.stderr)
                raise SystemExit(2)
        else:
            _start_background(s, "gateway", popen=popen)
            health = _wait_for_health(s, runner=False)
        if health is None:
            print(f"gateway가 시작되지 않았습니다. 로그: {_launch_state(s) / 'logs' / 'gateway.log'}",
                  file=sys.stderr)
            raise SystemExit(2)
        # Our own process, the one we just started, or the one this config's supervisor started again: if another
        # config's gateway answers instead (ours could not take the port), it is not ours. The gateway and runner
        # write their own owner records and answer with their own identity, from the same `-c` the supervisor passes
        # on, and the supervisor records the role with this config's path: nothing here changes for #613.
        verify(health, started_here=True)
    if s.runner.id not in (health.get("runners") or []):
        state, _record, reason = _process_record(s, "runner")
        if state == "unknown":
            _record_problem("runner", reason)
        if state == "alive":
            health = _wait_for_health(s, runner=True, timeout=30)
            if health is None:
                print(f"runner 프로세스는 살아 있으나 gateway에 연결되지 않음 — 로그: "
                      f"{_launch_state(s) / 'logs' / 'runner.log'}", file=sys.stderr)
                raise SystemExit(2)
        elif _supervised(s, "runner"):
            health = _wait_for_health(s, runner=True, timeout=30)
            if health is None:
                print(f"runner 감독 프로세스는 살아 있으나 runner가 gateway에 연결되지 않음 — 로그: "
                      f"{_launch_state(s) / 'logs' / 'runner.log'}", file=sys.stderr)
                raise SystemExit(2)
        else:
            _start_background(s, "runner", popen=popen)
            health = _wait_for_health(s, runner=True, timeout=60)
        if health is None:
            print(f"runner가 연결되지 않았습니다. 로그: {_launch_state(s) / 'logs' / 'runner.log'}",
                  file=sys.stderr)
            raise SystemExit(2)
    if confirmed:  # never record a gateway that is someone else's
        instance_guard.record_success(s, make_primary=make_primary)
    if s.runner.wsl.enabled:
        from .wsl_setup import WslError, control

        try:
            control(s, "start")
        except WslError as exc:
            print(f"경고: WSL runner 시작 실패 — {' '.join(str(exc).split())}; native 시작은 완료했습니다.",
                  file=sys.stderr)
    print(f"웹 사무실: {_http_base(s)}/  (`{_config_command(s, 'open')}`)")


def _claim_or_exit(s: Settings, role: str) -> None:
    """A gateway or runner records itself as the owner of its state folder; another config's live process there
    stops it, however it was started (#613)."""
    notice = instance_guard.claim(s, role)
    if notice is not None:
        print(notice.render(), file=sys.stderr)
        raise SystemExit(2)


def _up(s: Settings, *, popen=None, make_primary: bool = False) -> None:
    with _operation_lock(s):
        _up_unlocked(s, popen=popen, make_primary=make_primary)


def _wait_for_process_end(pid: int, created: str, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while True:
        try:
            current = _process_created(pid)
        except ProcessIdentityError as exc:
            raise OSError(str(exc)) from None
        if current is None or current != created:
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(_TERMINATE_POLL_SECONDS, remaining))


def _terminate_tree(pid: int, *, created: str | None = None) -> None:
    if sys.platform == "win32":
        # Not a kill that collects the tree by parent PID alone: Windows may have reused those PIDs. Launchers started
        # before the supervisor (#589) keep a stale parent PID after `up` exits, and a tree collected by it ended the
        # PI's gateway and runner together. The helper keeps only descendants created no earlier than their parent and
        # re-checks each one's creation time as it kills.
        identity = proc_tree.parse_created(created)
        if created is not None and identity is None:
            raise OSError(f"PID {pid}의 생성 시각 형식({created})을 읽을 수 없어 종료하지 않음")
        result = proc_tree.kill_tree(pid, created=identity, spare_ancestors_on_failure=True)
        if result.failed:  # the root or any descendant: a survivor must not pass as stopped, and its record must stay
            detail = "; ".join(f"PID {failed}: {reason}" for failed, reason in result.failed.items())
            if result.spared:
                detail += "; 다시 시도할 수 있게 PID " + ", ".join(map(str, result.spared)) + "은 종료하지 않음"
            raise OSError(detail)
        if created is not None and not _wait_for_process_end(pid, created, _TERMINATE_GRACE_SECONDS):
            raise OSError(f"{_TERMINATE_GRACE_SECONDS:g}초 안에 프로세스가 끝나지 않음")
        return
    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    if created is None:
        return
    if _wait_for_process_end(pid, created, _TERMINATE_GRACE_SECONDS):
        return
    try:
        if _process_created(pid) != created:
            return
    except ProcessIdentityError as exc:
        raise OSError(str(exc)) from None
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    if not _wait_for_process_end(pid, created, _TERMINATE_GRACE_SECONDS):
        raise OSError(f"SIGKILL 뒤에도 PID {pid} 프로세스가 종료되지 않음")


def _stop_background(s: Settings, role: str) -> int | None:
    """Stop one role: its supervisor first, so nothing restarts it, then whatever of the role itself is left (#589).

    On Windows ending the supervisor's tree ends the role too, so the second step usually finds it gone; on POSIX the
    supervisor passes the stop on and waits, and the role has its own process group. The role's record is read again
    after the supervisor is gone: a role that ended meanwhile may have had its PID reused, and is not touched."""
    supervisor = _supervisor_role(role)
    supervisor_state, supervisor_record, supervisor_reason = _process_record(s, supervisor)
    if supervisor_state == "unknown":
        _record_problem(supervisor, supervisor_reason)
    state, record, reason = _process_record(s, role)
    if state == "unknown":
        _record_problem(role, reason)
    role_pid = int(record["pid"]) if state == "alive" else None
    supervisor_pid: int | None = None
    if supervisor_state == "alive":
        _mark_stopping(s, role)
        supervisor_pid = int(supervisor_record["pid"])
        try:
            _terminate_tree(supervisor_pid, created=str(supervisor_record["created"]))
        except OSError as exc:
            print(f"{supervisor} PID {supervisor_pid}을 종료하지 못했습니다: {exc}", file=sys.stderr)
            raise SystemExit(2) from None
        _pid_file(s, supervisor).unlink(missing_ok=True)
        if state == "alive":
            state, record, reason = _process_record(s, role)
            if state == "unknown":
                _record_problem(role, reason)
    if state != "alive":
        return role_pid or supervisor_pid
    pid = int(record["pid"])
    try:
        _terminate_tree(pid, created=str(record["created"]))
    except OSError as exc:
        print(f"{role} PID {pid}을 종료하지 못했습니다: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
    _pid_file(s, role).unlink(missing_ok=True)
    return pid


def _active_request_line(request: dict) -> str:
    steps = (request.get("step_progress") or {}).get("steps") or {}
    active = [f"{step}={state}" for step, state in steps.items() if state in {"running", "hibernating"}]
    suffix = f" · {', '.join(active)}" if active else ""
    return f"{request.get('id', '?')} ({request.get('status', '?')}) {short(request.get('text'), 80)}{suffix}"


def _shutdown_readiness(s: Settings) -> dict | str | None:
    """The gateway's shutdown readiness (#500): its active requests, follow-ups still running on finished requests,
    recruitments and finished requests still being recorded (#521). None for an older gateway without the endpoint (HTTP 404); "unknown" for any other failure,
    which `down` refuses like an unknown request list (#515 review: fail closed)."""
    import httpx

    try:
        value = _api(s, "GET", "/api/shutdown-readiness", raw_errors=True)
    except httpx.HTTPStatusError as exc:
        return None if exc.response.status_code == 404 else "unknown"
    except (httpx.HTTPError, ValueError):
        return "unknown"
    return value if isinstance(value, dict) else "unknown"


def _gateway_is_local(s: Settings) -> bool:
    """Only call a gateway remote when its URL resolves away from this PC.

    The bind host is not its identity (a local TLS proxy may use any name).
    Failed address discovery is ambiguous, so a responding gateway must still
    make `down` report that it could not stop an unrecorded process.
    """
    from urllib.parse import urlsplit

    host = (urlsplit(s.gateway.url).hostname or "").lower().rstrip(".")
    machine = socket.gethostname().lower().rstrip(".")
    if not host or host in LOOPBACK_HOSTS or host in {machine, "0.0.0.0", "::"}:
        return True
    try:
        local_addresses = {ipaddress.ip_address(row[4][0].split("%")[0])
                           for row in socket.getaddrinfo(machine, None)}
        target_addresses = {ipaddress.ip_address(row[4][0].split("%")[0])
                            for row in socket.getaddrinfo(host, None)}
    except (OSError, ValueError):
        return True
    if not local_addresses or not target_addresses:
        return True
    return bool(local_addresses & target_addresses) or any(address.is_loopback for address in target_addresses)


def _unrecorded_process_still_running(s: Settings, *, timeout: float) -> str | None:
    """After `down` stopped what it had records for: what still answers, or None.

    A gateway on this PC that still answers was started outside `up`. A gateway on another PC is not ours to stop
    (a runner-only PC), so there only this config's runner id still being connected counts. After a stop, the closing
    socket gets up to `timeout` seconds."""
    local = _gateway_is_local(s)
    deadline = time.monotonic() + timeout
    while True:
        health = _health(s)
        if health is None or (not local and s.runner.id not in (health.get("runners") or [])):
            return None
        if time.monotonic() >= deadline:
            break
        time.sleep(0.2)
    if local:
        return (f"`up`이 아닌 방법으로 시작한 gateway/runner가 아직 실행 중입니다({_http_base(s)}). "
                "PID 기록이 없어 종료하지 못했습니다. 그 프로세스를 직접 끈 뒤 "
                f"`{_config_command(s, 'up')}`으로 다시 시작하세요.")
    return (f"`up`이 아닌 방법으로 시작한 runner({s.runner.id})가 gateway({_http_base(s)})에 아직 연결되어 있습니다. "
            "PID 기록이 없어 종료하지 못했습니다. 그 프로세스를 직접 끈 뒤 "
            f"`{_config_command(s, 'up')}`으로 다시 시작하세요.")


def external_wait_text(wait: dict) -> str:
    """`외부 응답 대기 n분째 / 상한 m분` for a step whose public lookup is waiting (#676)."""
    limit = max(1, round(float(wait.get("timeout_s") or 0) / 60))
    return f"외부 응답 대기 {min(limit, int(float(wait.get('elapsed_s') or 0)) // 60)}분째 / 상한 {limit}분"


FINISHING_STAGE_TEXT = {"cancel": "취소 기록을 쓰는 중", "bundle": "보고서 묶음 저장을 마무리하는 중",
                        "result_storage": "결과 저장을 마무리하는 중", "pi_interpretation": "PI 해석을 마무리하는 중"}


def _finishing_line(row: dict) -> str:
    text = FINISHING_STAGE_TEXT.get(row.get("stage"), "기록을 마무리하는 중")
    elapsed = row.get("elapsed_s")
    since = f" ({max(1, int(elapsed) // 60)}분째)" if isinstance(elapsed, (int, float)) and elapsed >= 60 else ""
    return f"마무리 중인 요청: {row.get('id')} · {text}{since}"


def _down_unlocked(s: Settings, *, force: bool = False, keep_gateway: bool = False) -> None:
    """Stop the recorded runner and gateway. With keep_gateway the gateway keeps running: the web shutdown runs inside
    that gateway, which cannot wait here for its own end, so it ends itself afterwards (gateway/shutdown.py)."""
    active = _active_requests(s)
    readiness = _shutdown_readiness(s) if active is not None else "unknown"
    lines = [f"진행 중 요청: {_active_request_line(request)}" for request in active or []]
    if isinstance(readiness, dict):
        # One answer decides: a request submitted after the request list was read is in it too (#515 review).
        listed = {request.get("id") for request in active or []}
        lines += [f"진행 중 요청: {row.get('id')} ({row.get('status')}) {short(row.get('text'), 80)}"
                  for row in readiness.get("requests") or [] if isinstance(row, dict) and row.get("id") not in listed]
        lines += [f"진행 중 이어 묻기: {row.get('request_id')} · {short(row.get('text'), 60)}"
                  for row in readiness.get("followups") or [] if isinstance(row, dict)]
        if readiness.get("recruits"):
            lines.append(f"진행 중 채용: {readiness['recruits']}건")
        # A finished request whose record is still being written (#521): a forced stop loses its report or bundle.
        # Worded by stage and age; a bundle worker stuck past the gateway's limit is told, not waited for (#658).
        finishing = [row for row in readiness.get("finishing") or [] if isinstance(row, dict)]
        lines += [_finishing_line(row) for row in finishing if not row.get("stuck")]
        stuck_lines = [_finishing_line(row) for row in finishing if row.get("stuck")]
        resumable = any(row.get("stage") == "bundle" and not row.get("stuck") for row in finishing)
        if readiness.get("ready") is False and not lines:
            lines.append("진행 중 작업: gateway가 아직 끝나지 않은 일이 있다고 답했습니다")
        busy: bool | None = bool(lines)
        for line in stuck_lines:
            print(f"멈춘 마무리: {line[len('마무리 중인 요청: '):]} — 종료를 막지 않습니다. 다음 시작 때 이어서 마무리합니다.",
                  file=sys.stderr)
    elif readiness is None:  # an older gateway without the endpoint: the request list decides, as before
        busy = bool(active)
    else:  # no answer: known active requests still refuse, otherwise it is unknown
        busy = True if active else None
    if busy:
        for line in lines:
            print(line, file=sys.stderr)
        if not force:
            if isinstance(readiness, dict) and resumable:
                print("요청 묶음은 기다리거나, 지금 종료하면 다음 시작 때 이어서 만듭니다.", file=sys.stderr)
            print(f"종료를 거부했습니다. 계속하려면 `{_config_command(s, 'down --force')}`를 쓰세요.",
                  file=sys.stderr)
            raise SystemExit(2)
    elif busy is None and not force:
        print(f"진행 중 작업을 확인할 수 없어 종료하지 않았습니다. 계속하려면 "
              f"`{_config_command(s, 'down --force')}`를 쓰세요.", file=sys.stderr)
        raise SystemExit(2)
    roles = ("runner",) if keep_gateway else ("runner", "gateway")
    for role in roles:
        for name in (role, _supervisor_role(role)):
            state, _record, reason = _process_record(s, name)
            if state == "unknown":
                _record_problem(name, reason)
    wsl_stop_error = None
    if s.runner.wsl.enabled:
        from .wsl_setup import WslError, control

        try:
            control(s, "stop")
        except WslError as exc:
            wsl_stop_error = exc
    stopped = [(role, pid) for role in roles if (pid := _stop_background(s, role)) is not None]
    if stopped:
        print("종료: " + ", ".join(f"{role} PID {pid}" for role, pid in stopped))
    if wsl_stop_error is not None:
        print(f"WSL runner를 종료하지 못했습니다(native 역할 종료는 계속): {wsl_stop_error}", file=sys.stderr)
    if keep_gateway:
        return
    # A gateway or runner started outside `up` (a script, another shell) has no process record, so nothing above
    # stopped it. Say so instead of "기록된 프로세스 없음" with exit 0, or `up` reuses it as if it were fresh.
    running = _unrecorded_process_still_running(s, timeout=_DOWN_RECHECK_SECONDS if stopped else 0)
    if running:
        print(running, file=sys.stderr)
        raise SystemExit(2)
    if not stopped:
        print("종료: 기록된 프로세스 없음")
    if wsl_stop_error is not None:
        raise SystemExit(2)


def _down(s: Settings, *, force: bool = False, keep_gateway: bool = False) -> None:
    with _operation_lock(s):
        _down_unlocked(s, force=force, keep_gateway=keep_gateway)


def _terminal_report(data: dict) -> str:
    """PI report first, then the separately labelled execution record for watch/send."""
    body = str(data.get("report") or data.get("error") or "")
    if data.get("report_truncated"):
        body += (f"\n\n[… 본문 잘림 · 전체 {data.get('report_chars', '?')}자 · "
                 f"전문: GET {data.get('report_api', '-')}]")
    for field, label in (("report_appendix", "과학 부록"), ("execution_record", "실행 기록")):
        appendix = str(data.get(field) or "")
        if field == "report_appendix" and not appendix.startswith("## 부록\n"):
            label = "실행 기록"  # old requests
        if appendix and data.get(f"{field}_truncated"):
            appendix += (f"\n\n[… {label} 잘림 · 전체 {data.get(f'{field}_chars', '?')}자 · "
                         f"전문: GET {data.get(f'{field}_api', '-')}]")
        if appendix:
            body += f"\n\n--- {label} ---\n" + appendix
    return body


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


def _report(s: Settings, request_id: str, *, pdf: bool, out: str | None) -> int:
    """`labhq report <request>`: print the PI report. With `--pdf`, save it as a PDF with its figures, or as an HTML
    page and the reason when this PC has no Edge or Chrome to print it (#584)."""
    import httpx
    from urllib.parse import quote, unquote

    path = f"/api/requests/{quote(request_id, safe='')}"
    if not pdf:
        req = _api(s, "GET", path)
        print(_terminal_report(req) if isinstance(req, dict) else "")
        return 0
    try:
        response = httpx.get(_http_base(s) + path + "/report-pdf", timeout=180,
                             headers={"Authorization": f"Bearer {s.gateway.client_token}"})
    except httpx.HTTPError:
        print(f"gateway에 연결할 수 없습니다: {_http_base(s)} — `{_config_command(s, 'up')}`을 실행하세요.",
              file=sys.stderr)
        return 2
    if response.status_code != 200:
        detail = _api_error_detail(response)
        print("gateway에 그 요청이 없습니다" if response.status_code == 404 else
              f"보고서를 만들지 못했습니다(HTTP {response.status_code})" + (f": {detail}" if detail else ""),
              file=sys.stderr)
        return 2
    kind = "pdf" if response.headers.get("X-Labhq-Report-Kind") == "pdf" else "html"
    target = Path(out) if out else Path.cwd() / f"{request_id}-report.{kind}"
    if target.suffix.casefold() != f".{kind}":
        target = target.with_suffix(f".{kind}")  # an HTML fallback never lands in a .pdf name
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_name(f".{target.name}.partial")
        partial.write_bytes(response.content)
        os.replace(partial, target)
    except OSError as exc:
        print(f"보고서를 저장하지 못했습니다: {exc}", file=sys.stderr)
        return 2
    print(f"보고서 {kind.upper()}: {target}")
    reason = unquote(response.headers.get("X-Labhq-Reason", ""))
    if reason:
        print(reason, file=sys.stderr)
    return 0


_BUNDLE_FIELDS = ("bundle_path", "bundle_status", "bundle_grade", "bundle_grade_reasons", "bundle_warning")


def _request_record(s: Settings, request_id: str) -> dict | None:
    """The gateway's record of one request, or None when it cannot be read right now."""
    import httpx
    from urllib.parse import quote

    try:
        record = _api(s, "GET", f"/api/requests/{quote(request_id, safe='')}", raw_errors=True)
    except (httpx.HTTPError, ValueError):
        return None
    return record if isinstance(record, dict) else None


async def _wait_for_bundle(s: Settings, ws, terminal: dict, login_holds: set[str], *,
                           timeout: float | None = None) -> None:
    """Print the request bundle path and grade after a terminal event, then let the caller return.

    The gateway commits request.completed/failed before it builds the bundle (`Hub.schedule_terminal`), so those
    fields arrive in a later request.bundle event; returning at the terminal event never showed them. A request
    that never ran a step has no bundle and nothing pending, so it returns at once. Bounded: a bundle still being
    copied after `timeout` seconds is left to `labhq status`."""
    request_id = terminal.get("request_id")
    data = terminal.get("data") or {}
    if not request_id or data.get("bundle_path") or data.get("bundle_warning"):
        return  # render() already printed them
    import websockets

    deadline = time.monotonic() + (_BUNDLE_WAIT_SECONDS if timeout is None else timeout)
    connected = True
    while True:
        record = _request_record(s, request_id)
        if record is not None:
            if record.get("bundle_path") or record.get("bundle_warning"):
                render({"type": "request.bundle", "ts": time.time(), "request_id": request_id,
                        "data": {key: record[key] for key in _BUNDLE_FIELDS if key in record}}, login_holds)
                return
            if not record.get("bundle_pending"):
                return  # no bundle is being built
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            print(f"요청 묶음을 아직 만드는 중입니다. 끝나면 `{_config_command(s, 'status')}`에 경로가 나옵니다.",
                  flush=True)
            return
        wait = min(_BUNDLE_POLL_SECONDS, remaining)
        if not connected:
            await asyncio.sleep(wait)
            continue
        try:
            raw = await asyncio.wait_for(ws.recv(), wait)
        except asyncio.TimeoutError:
            continue  # poll the record again: a missed event must not cost the whole wait
        except websockets.ConnectionClosed:
            connected = False
            continue
        ev = json.loads(raw)
        if ev.get("request_id") == request_id and ev.get("type") != "snapshot":
            render(ev, login_holds)
            if ev.get("type") == "request.bundle":
                return


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
                await _wait_for_bundle(s, ws, ev, login_holds)
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
                await _wait_for_bundle(s, ws, ev, login_holds)
                return


async def _run_runner_with_interrupts(runner, diagnostics=None) -> None:
    if diagnostics:
        diagnostics.attach_loop()
    previous = signal.getsignal(signal.SIGINT)
    last_warning = 0.0
    owner = asyncio.current_task()
    shutdown_job = None
    finishing = False

    async def shutdown() -> None:
        try:
            await runner.shutdown()
        finally:
            if not finishing:
                owner.cancel()

    def on_shutdown(signum, _frame) -> None:
        nonlocal shutdown_job
        if shutdown_job is None:
            if diagnostics:
                diagnostics.note_signal(signum)
            shutdown_job = asyncio.create_task(shutdown())

    def on_interrupt(_signum, _frame) -> None:
        nonlocal last_warning
        active = [tid for tid, task in runner.tasks.items() if not task.done()]
        # A step asleep on HPC has no live task, but the runner still watches its jobs and wakes the agent.
        jobs = [jid for jid, job in getattr(runner, "jobs", {}).items() if not job.get("terminal")]
        now = time.monotonic()
        if not (active or jobs) or (last_warning and now - last_warning <= 10):
            if diagnostics:
                diagnostics.note_signal(_signum)
            runner._stopping = True
            raise KeyboardInterrupt
        last_warning = now
        print(f"진행 중인 task {len(active)}개, 감시 중인 HPC job {len(jobs)}개: {', '.join(active + jobs)}. "
              "10초 안에 Ctrl+C를 다시 누르면 종료합니다.", file=sys.stderr, flush=True)

    from .interruption import watch_shutdown_signals
    restore_signals = watch_shutdown_signals(lambda: setattr(runner, "_stopping", True), interrupt=False,
                                            default_handler=on_shutdown,
                                            disposition=getattr(diagnostics, "signal_disposition", None))
    # Keep the interactive handler last. SIGTERM/SIGHUP mark an external shutdown;
    # SIGINT still warns once while work is active and exits on the second press.
    signal.signal(signal.SIGINT, on_interrupt)
    try:
        await runner.run_forever()
    except asyncio.CancelledError:
        if shutdown_job is None:
            raise
    finally:
        finishing = True
        try:
            if shutdown_job is not None:
                await asyncio.shield(shutdown_job)
        finally:
            restore_signals()
            signal.signal(signal.SIGINT, previous)
            runner.stop()


# ---------------- demo (no API keys, no cluster) ----------------
def _approval_delay(phone: bool, approve_timeout: float, web: bool = False) -> float:
    return approve_timeout if phone else 1.0 if web else 0.3


LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")
WILDCARD_HOSTS = ("0.0.0.0", "::", "")


def _port_available(host: str, port: int) -> bool:
    # Windows can allow the same port on different addresses. A successful
    # wildcard bind alone says nothing about the loopback server the runner dials.
    import errno

    for address in ("127.0.0.1", "::1"):
        try:
            with socket.create_connection((address, port), timeout=0.15):
                return False
        except OSError:
            pass
    for address in dict.fromkeys((host or "0.0.0.0", "127.0.0.1", "0.0.0.0", "::1", "::")):
        family = socket.AF_INET6 if ":" in address else socket.AF_INET
        try:
            with socket.socket(family, socket.SOCK_STREAM) as sock:
                if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                sock.bind((address, port))
        except OSError as exc:
            # Optional IPv6 probes must not disable demos on IPv4-only systems.
            if address != host and family == socket.AF_INET6 and exc.errno in (
                    errno.EAFNOSUPPORT, errno.EPROTONOSUPPORT, errno.EADDRNOTAVAIL):
                continue
            return False
    return True


def _demo_web_port(host: str, preferred: int) -> int:
    candidate = preferred
    for _ in range(100):
        if _port_available(host, candidate):
            return candidate
        candidate = free_port()
    raise RuntimeError("데모에 쓸 빈 포트를 찾지 못했습니다")


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


def _demo_settings(tmp: Path, web: bool = False) -> Settings:
    s = Settings()
    s.gateway.state_dir = str(tmp / "state" / "gateway")
    s.runner.state_dir = str(tmp / "state" / "runner")
    s.runner.workspace_root = str(tmp / "runs")
    s.runner.talent_dir = str(tmp / "talent")
    s.runner.agents_dir = str(tmp / "agents")
    s.runner.force_engine = "mock"
    s.runner.mock_delay_s = 2.0 if web else 0.05
    s.runner.job_poll_s = 1
    s.hpc.scheduler = "mock"
    return s


async def _demo_status(hub, phase: str, remaining: int = 0) -> None:
    hub.demo_status = {"phase": phase, "remaining_s": remaining}
    await hub.publish({"type": "demo.status", "data": hub.demo_status})


async def _demo_countdown(hub, seconds: int = 8) -> None:
    for remaining in range(seconds, 0, -1):
        await _demo_status(hub, "waiting", remaining)
        await asyncio.sleep(1)


async def _demo(web: bool = False, port: int = 8787, phone: bool = False,
                host: str = "127.0.0.1", approve_timeout: float = 120) -> None:
    import uvicorn

    from .gateway.server import RequestIn, create_app
    from .runner.daemon import Runner

    tmp = Path(tempfile.mkdtemp(prefix="labhq-demo-"))
    shutil.copytree(REPO / "agents", tmp / "agents")
    s = _demo_settings(tmp, web=web)
    gport = _demo_web_port(host, port) if web else free_port()
    dial = _demo_dial_host(host)
    s.gateway.port, s.gateway.url = gport, f"ws://{dial}:{gport}"
    s.gateway.host = host
    exposed = phone or host not in LOOPBACK_HOSTS
    # Both default tokens are public; on the LAN anyone could otherwise join as a client or replace the runner.
    s.gateway.client_token = _demo_token(exposed, s.gateway.client_token)
    s.gateway.runner_token = _demo_token(exposed, s.gateway.runner_token)
    # The mock team's approvals expire after this; in phone mode it must outlast the tap fallback.
    s.policy.approvals.timeout_s = math.ceil(approve_timeout) + 30 if phone else 60
    s.runner.broker_port = free_port()

    app = create_app(s, demo=True)
    hub = app.state.hub
    publish = hub.publish

    def after(ev: dict) -> None:
        render(ev)
        if ev.get("type") == "approval.requested":
            asyncio.get_running_loop().create_task(_demo_auto_approve(
                hub, ev["data"]["id"], _approval_delay(phone, approve_timeout, web=web), phone))

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
            if gport != port:
                url_host = (_demo_url_hosts(host) or [dial])[0]
                print(f"{port}은 실행 중인 서비스가 쓰고 있어 데모는 "
                      f"http://{url_host}:{gport}/?token={s.gateway.client_token} 에서 엽니다 — 이 주소만 여세요")
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
                await _demo_status(hub, "running")
                rid = hub.create_request(RequestIn(text=texts[i % 2]))
                await until(lambda: is_terminal_request(hub.requests[rid]["status"]),
                            max(120, approve_timeout + 60) if phone else 120)
                if i == 0 and "c_scanpy" not in hub.agents:
                    await asyncio.sleep(3)
                    await hub.send_runner(s.runner.id, {"type": "recruit.start", "repo": "https://github.com/scverse/scanpy",
                                                        "focus": "Preprocessing and clustering", "ttl_days": 14,
                                                        "request_id": rid})
                await _demo_countdown(hub)
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
        await asyncio.gather(*tasks, return_exceptions=True)


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
    init.add_argument("--bioinfo-agent", metavar="PATH",
                      help="folder of the bioinfo-agent plugin (otherwise init looks for it, then asks)")
    sub.add_parser("gateway")
    up = sub.add_parser("up", help="start the gateway and runner in the background")
    up.add_argument("--make-primary", action="store_true",
                    help="record this config as this PC's primary config (the first `up` records it by itself)")
    down = sub.add_parser("down", help="stop only the gateway and runner recorded by this instance")
    down.add_argument("--force", action="store_true", help="stop even when work is active or cannot be checked")
    op = sub.add_parser("open", help="open the web office in the browser, signed in (the token is never printed)")
    op.add_argument("--3d", dest="three_d", action="store_true", help="open the 3D office instead of 2.5D")
    sub.add_parser("runner")
    sw = sub.add_parser("setup-wsl", help="이 PC의 WSL 분석 runner 준비")
    sw.add_argument("--check", action="store_true", help="설치하지 않고 준비 상태만 확인")
    wr = sub.add_parser("wsl-runner", help="이 설정의 WSL runner 시작·종료")
    wr.add_argument("action", choices=("start", "stop"))
    wiki = sub.add_parser("wiki", help="link a local LLM-wiki folder as a read-only, provenance-tracked reference (#827)")
    wiki.add_argument("action", choices=("connect", "status", "reindex", "search", "disconnect"))
    wiki.add_argument("target", nargs="?", help="connect: the wiki folder; search: the query")
    sub.add_parser("agents")
    storage = sub.add_parser("storage", help="open the storage choice card for a finished request, or redraft, republish or rebuild its edition")
    storage.add_argument("id")
    again = storage.add_mutually_exclusive_group()
    again.add_argument("--redraft", action="store_true",
                       help="run the editor again for an edition not published yet (publish card pending, declined or upload failed)")
    again.add_argument("--republish", action="store_true",
                       help="after a failed upload, open the publish card again with the local draft (the editor does not run)")
    again.add_argument("--to", choices=["github"],
                       help="move a request that ended local to a new GitHub repository (the publish card still asks first)")
    again.add_argument("--rebuild", action="store_true",
                       help="make a published edition again with the current editor: a new commit in the same repository, "
                            "or with --name a new repository (the publish card still asks first)")
    storage.add_argument("--name", help="with --to github: repository name (default labhq-<request id>); "
                                        "with --rebuild: a new repository instead of the same one")
    storage.add_argument("--visibility", choices=["private", "public"], default="private",
                         help="with --to github or --rebuild --name: repository visibility (default private)")
    review = sub.add_parser("review-done", help="mark a finished report reviewed (or failure acknowledged)")
    review.add_argument("id")
    review.add_argument("--revision", type=int, help="CP4 draft revision shown in the report card")
    review.add_argument("--sha256", help="CP4 target hash shown in the report card")
    review.add_argument("--reject", action="store_true", help="reject this CP4 draft")
    review.add_argument("--storage", choices=["keep", "local", "github", "existing"],
                        help="answer the final storage question while marking the report reviewed")
    review.add_argument("--name", help="with --storage github: new repository name; with existing: owner/repository")
    review.add_argument("--visibility", choices=["private", "public"], default="private",
                        help="with --storage github or existing: repository visibility")
    sub.add_parser("status", help="show runners, running requests and pending approvals")
    recall = sub.add_parser("recall", help="search past requests in this gateway (2+ characters, at most 5 hits)")
    recall.add_argument("query")
    doctor = sub.add_parser("doctor", help="check runner capabilities without starting agents")
    doctor.add_argument("--json", action="store_true", help="write state_dir/capabilities.json")
    doctor.add_argument("--network", action="store_true", help="check public data source reachability")
    doctor.add_argument("--fix", action="store_true", help="install missing Paper2Agent for the recruiter and recheck")
    doctor.add_argument("--runtime", action="store_true", help="require live authenticated gateway/runner and valid managed process identities")
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
    sp.add_argument("--work-kind", choices=("auto", "simple", "research"), default="auto",
                    help="with research.enabled: simple skips the research lane (CP1/CP2), research forces it")
    sp.add_argument("--cso-model", help="request-local CSO model from orchestrator.cso_models")
    sp.add_argument("--dry-run", action="store_true",
                    help="show pre-plan calls and cost ranges without calling models, tools or the network")
    sp.add_argument("--no-wait", action="store_true")
    note = sub.add_parser("note", help="send a note to later stages of a running request")
    note.add_argument("request_id")
    note.add_argument("text")
    request_p = sub.add_parser("request", help="correct a request with recorded shutdown evidence")
    request_p.add_argument("action", choices=("mark-interrupted",))
    request_p.add_argument("request_id")
    cancel = sub.add_parser("cancel", help="cancel a request: stop its tasks, waits and cards, keep partial results")
    cancel.add_argument("request_id")
    cancel.add_argument("--active-only", action="store_true", help="cancel only an active request; never decide a final review")
    cancel.add_argument("--expected-created-at", type=float, help="exact request creation timestamp; required with --active-only")
    task_p = sub.add_parser("task", help="steer, pause, resume or halt one running task (#59)")
    task_p.add_argument("action", choices=("steer", "pause", "resume", "halt"))
    task_p.add_argument("task_id")
    task_p.add_argument("text", nargs="?", help="steer: the direction change for the staff member (2,000 characters)")
    resume = sub.add_parser("resume", help="retry a step waiting for quota or engine login")
    resume.add_argument("request_id")
    resume.add_argument("step_id")
    sub.add_parser("watch")
    sub.add_parser("projects", help="list projects and their GitHub repos")
    cr = sub.add_parser("codex-review", help="ask Codex to review a PR in a project repo (posts '@codex review')")
    cr.add_argument("project")
    cr.add_argument("pr", type=int)
    cr.add_argument("--note", default="")
    sub.add_parser("approvals").add_argument('--details', action='store_true', help='show original tool input and rule')
    ap = sub.add_parser("approve", help="decide a pending approval (CP2 evidence review needs --choice)")
    ap.add_argument("id")
    ap.add_argument("--deny", action="store_true")
    ap.add_argument("--choice", choices=["approve", "revise", "deny"],
                    help="CP2 evidence review (research_evidence) decision; the note never decides it. CP1 "
                         "(research_plan) takes --choice revise --note: a new request plans again from the note")
    ap.add_argument("--note", default="")
    vp = sub.add_parser("verify", help="re-hash a request's outputs on this PC and recheck its report anchors (#58)")
    vp.add_argument("request_id")
    vp.add_argument("--json", action="store_true", help="print the result as JSON")
    vp.add_argument("--bundle", metavar="OUT.zip", help="also write README.md, claims.json and artifacts.json "
                    "into a zip (the output files themselves are not included)")
    rep = sub.add_parser("report", help="print a request's PI report, or save it as a PDF with its figures (--pdf)")
    rep.add_argument("request_id")
    rep.add_argument("--pdf", action="store_true", help="save a PDF with the figures (an HTML page and the reason "
                     "when this PC has no Edge or Chrome to print it); never made for controlled-data requests")
    rep.add_argument("--out", metavar="FILE", help="where to save it (default: ./<request>-report.pdf)")
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
    sp2a = sub.add_parser("setup-paper2agent", help="install the paper2agent skill for Claude Code and Codex")
    sp2a.add_argument("--commit", help="install exactly this 40-hex Paper2Agent commit (default: recruit.skill_commit, "
                                       "else the latest HEAD)")
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
    sem_report.add_argument("--request", dest="request_id", help="compare one request's shadow with its progress")  # semantics-hook
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
            result = run(args.config, yes=args.yes, dry_run=args.dry_run, force=args.force, instance=args.instance,
                         bioinfo_agent=args.bioinfo_agent)
        except InitError as exc:
            p.exit(1, f"init: {exc}\n")
        except (OSError, ValueError, YAMLError):
            p.exit(1, "init: 설정을 읽거나 쓸 수 없습니다. 설정 파일과 권한을 확인하세요.\n")
        if result["summary"]["fail"]:
            raise SystemExit(1)
        return

    if args.cmd == "bench":
        requested = args.config or os.environ.get("LABHQ_CONFIG")
        if requested:
            guard_test_path(Path(requested))
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

    if args.cmd in ("setup-wsl", "wsl-runner"):
        from .wsl_setup import WslError, check, control, render as render_wsl, setup

        try:
            if args.cmd == "wsl-runner":
                control(s, args.action)
                print("WSL runner: " + ("시작" if args.action == "start" else "종료"))
            else:
                rows = check(s) if args.check else setup(s)
                print(render_wsl(rows))
                if any(row["status"] != "ok" for row in rows):
                    raise SystemExit(1)
        except WslError as exc:
            p.exit(2, f"WSL: {exc}\n")
    elif args.cmd == "wiki":
        from .wiki import cli_main as wiki_cli

        raise SystemExit(wiki_cli(s, args.action, args.target, p))
    elif args.cmd == "doctor":
        from .doctor import collect, render, save

        requested = args.config or os.environ.get("LABHQ_CONFIG")
        result = collect(s, requested_config=requested, network=args.network, fix=args.fix, runtime=args.runtime)
        if args.json:
            try:
                save(result, s)
            except OSError:
                result["checks"].append({"group": "config", "name": "manifest", "status": "fail",
                                         "detail": "could not write manifest",
                                         "hint": "Set runner.state_dir to a writable directory outside the repository."})
                result["summary"]["fail"] += 1
        print(render(result))
        if result["summary"]["fail"] or (args.runtime and not result["readiness"]["ready"]):
            raise SystemExit(1)
    elif args.cmd == "gateway":
        from .windows_launch import require_detached_job
        require_detached_job()
        from .process_diagnostics import ProcessDiagnostics

        with ProcessDiagnostics(_launch_state(s) / "logs", "gateway") as diagnostics:
            import uvicorn

            from .gateway.server import create_app

            from .security import gateway_log_config

            from .github_token import export_to_env

            # The token `labhq init` stored, for this process only: staff, runner and MCP processes never get it (#687).
            export_to_env(s)
            try:
                app = create_app(s)
            except ValueError as exc:  # e.g. a retired research pack version (#170)
                p.exit(1, f"gateway: {exc}\n")
            if _default_client_token(s.gateway.client_token):
                p.exit(2, "gateway: gateway.client_token의 change-me 기본값을 바꾸세요.\n")
            # The web office asks for the client token; say how to get in without printing it (PI visit 2026-10-04).
            print(f"웹 사무실: {_http_base(s)}/  (다른 창에서 `{_config_command(s, 'open')}`을 실행하세요)",
                  flush=True)
            @app.on_event("startup")
            async def install_loop_diagnostics():
                diagnostics.attach_loop()
                # Uvicorn has installed its handlers by startup; preserve its exit path.
                from .interruption import watch_shutdown_signals
                watch_shutdown_signals(lambda: setattr(app.state.hub, "stopping", True))

            _claim_or_exit(s, "gateway")
            try:
                uvicorn.run(app, host=s.gateway.host, port=s.gateway.port, log_level="info",
                            log_config=gateway_log_config())
            finally:
                error = sys.exc_info()[1]
                diagnostics.write("uvicorn_end", reason=diagnostics.reason if error is None else type(error).__name__,
                                  code=getattr(error, "code", 1) if error is not None else diagnostics.code)
    elif args.cmd == "up":
        _up(s, make_primary=args.make_primary)
    elif args.cmd == "down":
        _down(s, force=args.force)
    elif args.cmd == "open":
        if _health(s) is None:
            print(f"gateway에 연결할 수 없습니다: {_http_base(s)} — "
                  f"`{_config_command(s, 'up')}`을 실행하세요.", file=sys.stderr)
            raise SystemExit(2)
        print(_open_web_office(s, args.config or os.environ.get("LABHQ_CONFIG"), three_d=args.three_d))
    elif args.cmd == "runner":
        from .windows_launch import require_detached_job
        require_detached_job()
        from .process_diagnostics import ProcessDiagnostics

        with ProcessDiagnostics(_launch_state(s) / "logs", "runner") as diagnostics:
            from .runner.daemon import Runner

            clean = without_windows_app_aliases(dict(os.environ))
            path_key = next((key for key in clean if key.casefold() == "path"), None)
            if path_key:
                os.environ[path_key] = clean[path_key]
            _claim_or_exit(s, "runner")  # before Runner opens runner-<id>.sqlite3
            asyncio.run(_run_runner_with_interrupts(Runner(s), diagnostics))
    elif args.cmd == "status":
        for row in _local_process_diagnostics(s):
            print(row["detail"])
        from .research.semantics_shadow import status_line as semantics_status_line  # semantics-hook
        semantics_line = semantics_status_line(s)  # semantics-hook
        if semantics_line: print(semantics_line)  # semantics-hook
        health = _api(s, "GET", "/api/health")
        print("러너: " + (", ".join(health["runners"]) or
                         f"없음 — `{_config_command(s, 'up')}`로 켜야 요청이 진행됩니다"))
        running = _api(s, "GET", "/api/requests?status=running&limit=200")
        print(f"진행 중 요청: {len(running)}")
        for req in running:
            progress = req["step_progress"]
            held = "" if req.get("status") == "running" else f" [{request_display_label(req)}]"
            print(f"  {req['id']} {progress['done']}/{progress['total']} {req['text']}{held}")
            environment = req.get("step_environment") or {}
            waits = req.get("step_waits") or {}
            for sid, state in progress["steps"].items():
                problem = problem_text(environment.get(sid))
                print(f"    {sid}: {state}" + (f" — {problem}" if problem else "")
                      + (f" — {external_wait_text(waits[sid])}" if sid in waits else ""))
            summary = req.get("cost_summary")
            if summary or req.get("cost_known") is False or req.get("cost_usd"):
                print(f"    비용: {cost_text(req.get('cost_usd'), req.get('cost_known'), summary)}"
                      + (f" {cost_detail(summary)}" if summary else ""))
        recent = _api(s, "GET", "/api/requests?status=all&limit=200")
        for req in recent:
            if req.get("outcome") == "model_refused":
                from .model_refusal import REASON
                print(f"  {req['id']} [model_refused] {REASON}")
        totals = _api(s, "GET", "/api/requests/summary")
        print(f"이번 세션·진행 중 비용: {_request_cost_totals_overview(totals.get('session') or {})}")
        if totals.get("past_count"):
            print(f"지난 요청: {int(totals['past_count'])}건 · 누적 비용 "
                  f"{_request_cost_totals_overview(totals.get('total') or {})}")
        reviews = [req for req in recent if req.get("pi_review_pending")]
        if reviews:
            print(f"PI 검토·확인 대기: {len(reviews)}")
            for req in reviews:
                print(f"  {req['id']} [{request_display_label(req)}] {req.get('text', '')}")
                print(f"    비용: {cost_text(req.get('cost_usd'), req.get('cost_known'), req.get('cost_summary'))}")
                final = req.get("final_checkpoint")
                target = f" --revision {final['revision']} --sha256 {final['target_sha256']}" if final else ""
                print(f"    확인 후: labhq review-done {req['id']}{target}")
                if final:
                    print("    CP4 수용 거부: 같은 명령에 --reject 추가. 수용은 게시·전송·실행 승인이 아닙니다.")
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
                if req.get("bundle_grade"):
                    reasons = "; ".join(map(str, req.get("bundle_grade_reasons") or []))
                    print(f"    재현 등급: {req['bundle_grade']}" + (f" ({reasons})" if reasons else ""))
                if req.get("bundle_warning"):
                    print(f"    경고: {req['bundle_warning']}")
        approvals = _api(s, "GET", "/api/approvals")
        print(f"승인 대기: {len(approvals)}")
        for approval in approvals:
            print(f"  {approval['id']} [{approval['kind']}] {approval['summary']}")
    elif args.cmd == "recall":
        if not 2 <= len(args.query.strip()) <= 200:
            p.error("recall query must contain 2 to 200 characters")
        for hit in _api(s, "GET", "/api/knowledge/search", params={"q": args.query}):
            print(f"{hit['request_id']} ({hit['source']})\n{hit['body']}\n")
    elif args.cmd == "storage" and args.redraft:
        result = _api(s, "POST", f"/api/requests/{args.id}/storage/redraft", json={})
        closed = f" (이전 게시 카드 {len(result['closed_cards'])}장 닫음)" if result.get("closed_cards") else ""
        print(f"{args.id}: 편집자를 다시 돌립니다{closed} — 새 정리본이 만들어지면 게시 카드가 다시 뜹니다.")
    elif args.cmd == "storage" and args.to == "github":
        result = _api(s, "POST", f"/api/requests/{args.id}/storage/to-github",
                      json={"name": args.name, "visibility": args.visibility})
        wait = (" 보고서 검토를 마치면(`labhq review-done`) 편집자가 정리본을 만들고" if result.get("waiting_review")
                else " 편집자가 정리본을 다시 만들고")
        print(f"{args.id}: {result['repo']}로 옮깁니다.{wait} 게시 카드가 뜨면 확인하고 올려 주세요.")
        if result.get("note"):
            print(result["note"])
    elif args.cmd == "storage" and args.rebuild:
        result = _api(s, "POST", f"/api/requests/{args.id}/storage/rebuild",
                      json={"name": args.name, "visibility": args.visibility})
        where = (f"새 저장소 {result['repo']}에" if result.get("new_repository")
                 else f"같은 저장소 {result['repo']}에 새 판으로")
        print(f"{args.id}: 지금 편집기로 정리본을 다시 만들어 {where} 올립니다. 게시 카드가 뜨면 확인하고 올려 주세요. "
              "이전 판에만 있던 파일은 지우지 않습니다.")
        if result.get("note"):
            print(result["note"])
    elif args.cmd == "storage" and args.republish:
        result = _api(s, "POST", f"/api/requests/{args.id}/storage/republish", json={})
        closed = f" (이전 게시 카드 {len(result['closed_cards'])}장 닫음)" if result.get("closed_cards") else ""
        print(f"{args.id}: 로컬 정리본으로 다시 올립니다{closed} — 게시 카드가 뜨면 확인하고 올려 주세요.")
    elif args.cmd == "storage":
        result = _api(s, "POST", f"/api/requests/{args.id}/storage", json={})
        print(f"{args.id}: 저장 위치 카드 {result['id']} — 웹에서 저장 위치를 골라 주세요.")
    elif args.cmd == "request":
        result = _api(s, "POST", f"/api/requests/{args.request_id}/mark-interrupted")
        print(f"{args.request_id}: {result['status']}")
    elif args.cmd == "review-done":
        target = ({"revision": args.revision, "target_sha256": args.sha256,
                   "decision": "rejected" if args.reject else "accepted"}
                  if args.revision is not None or args.sha256 or args.reject else {})
        if args.storage:
            target.update(storage=args.storage, name=args.name, visibility=args.visibility)
        result = _api(s, "POST", f"/api/requests/{args.id}/review-done", json=target)
        print(f"{args.id}: 검토·확인 완료 ({result['pi_reviewed_at']})")
        if result.get("result_storage"):
            print(f"저장 위치: {result['result_storage'].get('mode', 'local')}")
        elif result.get("storage_card"):
            print(f"저장 위치 카드 {result['storage_card']} — 처음 선택을 유지하거나 다시 골라 주세요.")
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
        if args.agent and args.work_kind == "research":
            p.error("--work-kind research needs the team (CSO) path: drop --agent")
        body = {"text": args.text, "mode": "direct" if args.agent else "plan_only" if args.plan_only else "orchestrate",
                "agent_id": args.agent, "cso_model": args.cso_model, "route": "team" if args.team else "auto",
                "project_dirs": args.project_dir, "budget_usd": args.budget, "project_id": args.project,
                "references": references, "default_references": not args.no_default_refs,
                "work_kind": args.work_kind}
        if args.dry_run:
            from .dry_run import build_preview, render_preview

            try:
                print(render_preview(build_preview(s, body)))
            except ValueError as exc:
                p.error(str(exc))
        elif args.no_wait:
            print(_api(s, "POST", "/api/requests", json=body))
        else:
            _run_websocket(s, _send_and_wait(s, body))
    elif args.cmd == "note":
        print(_api(s, "POST", f"/api/requests/{args.request_id}/notes", json={"text": args.text}))
    elif args.cmd == "cancel":
        from urllib.parse import quote

        if args.active_only:
            if args.expected_created_at is None or not math.isfinite(args.expected_created_at) or args.expected_created_at <= 0:
                p.error("--active-only requires finite positive --expected-created-at")
            out = _api(s, "POST", f"/api/requests/{quote(args.request_id, safe='')}/cancel-active",
                       json={"expected_created_at": args.expected_created_at})
        else:
            if args.expected_created_at is not None:
                p.error("--expected-created-at requires --active-only")
            out = _api(s, "POST", f"/api/requests/{quote(args.request_id, safe='')}/cancel", json={})
        if out.get("skipped"):
            print(f"{out['request_id']}: active-only 취소 생략 · 상태 {out.get('status')} · PI 결정은 유지했습니다")
            return
        print(f"{out['request_id']}: 이미 취소된 요청입니다" if out.get("already") else
              f"{out['request_id']}: 취소했습니다 · 멈춘 작업 {len(out.get('cancelled_tasks') or [])}개 · "
              "부분 결과는 보고서에 있습니다")
    elif args.cmd == "task":
        print(task_control(s, args.action, args.task_id, args.text))
    elif args.cmd == "resume":
        print(_api(s, "POST", f"/api/requests/{args.request_id}/steps/{args.step_id}/resume-quota", json={}))
    elif args.cmd == "watch":
        _run_websocket(s, _watch(s))
    elif args.cmd == "projects":
        for pr in _api(s, "GET", "/api/projects"):
            print(f"{pr['id']:<16} {pr.get('repo') or '-':<32} {pr['visibility']:<8} "
                  f"issues={pr['issues']} reports={pr['commit_reports']}")
    elif args.cmd == "codex-review":
        print(_api(s, "POST", f"/api/projects/{args.project}/prs/{args.pr}/codex-review", json={"note": args.note}))
    elif args.cmd == "approvals":
        from .pi_text import with_approval_copy
        for a in _api(s, "GET", "/api/approvals"):
            a = with_approval_copy(a)
            print(f"{a['id']}  [{a['kind']}] {a['pi_summary']}")
            if a['kind'] == 'tool_permission':
                print(f"요청 직원: {(a.get('detail') or {}).get('requester_name') or a.get('agent_id') or '미제공'}")
            print(a['pi_reason'])
            print(a['pi_outcome'])
            if args.details:
                print('자세히: ' + json.dumps({'summary': a['summary'], 'detail': a.get('detail') or {}}, ensure_ascii=False))
            for option in _question_options(a):
                print(f"    {option}")
    elif args.cmd == "approve":
        if args.deny and args.choice not in (None, "deny"):
            p.error(f"--deny contradicts --choice {args.choice}")
        pending = next((a for a in _api(s, "GET", "/api/approvals") if a.get("id") == args.id), None)
        evidence = bool(pending and pending.get("kind") == "research_evidence")
        # CP1 수정 요청 opens a new request from the note; a continuation round's CP1 has none (revise_allowed).
        plan_revise = bool(pending and pending.get("kind") == "research_plan"
                           and (pending.get("detail") or {}).get("revise_allowed") is True)
        if evidence and args.choice is None and not args.deny:
            p.error("CP2 evidence review needs --choice approve, --choice revise or --choice deny")
        if args.choice and pending and not (evidence or plan_revise):
            p.error(f"--choice is only for CP2 evidence review and CP1 수정 요청; {args.id} is {pending.get('kind')}")
        # CP2 수정 요청 re-plans from its note through a new CP1 until the continuation cap is reached (R10).
        if (args.choice == "revise" and not args.note.strip()
                and ((pending or {}).get("detail") or {}).get("revise_continues") is not False):
            p.error('수정 요청(--choice revise)에는 고칠 점을 --note로 적어야 합니다. CSO가 그 메모로 새 계획을 세워 '
                    f'새 CP1을 받습니다. 예: labhq approve {args.id} --choice revise --note "s2에 민감도 분석 추가"')
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
        # A hard-stop question with options needs a pick, as on the web (R19, #521); --deny still refuses it.
        options = _question_options(pending or {})
        if options and not args.deny and not args.note.strip():
            p.exit(2, "이 직원 질문은 답을 골라 --note로 보내야 합니다. 요청은 그대로 유지됩니다.\n"
                   f"{(pending or {}).get('summary') or ''}\n선택지:\n" + "\n".join(options) +
                   f'\n예: labhq approve {args.id} --note "a"\n')
        body = {"approved": args.choice == "approve" if args.choice else not args.deny, "note": args.note}
        if args.choice:
            body["choice"] = args.choice
        print(_api(s, "POST", f"/api/approvals/{args.id}", json=body))
    elif args.cmd == "verify":
        raise SystemExit(_verify(s, args.request_id, as_json=args.json, bundle=args.bundle))
    elif args.cmd == "report":
        raise SystemExit(_report(s, args.request_id, pdf=args.pdf, out=args.out))
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
        for line in talent_lines(reg):
            print(line)
    elif args.cmd == "setup-paper2agent":
        from .recruit.paper2agent import setup_paper2agent, SOURCE_MARKER

        pin = args.commit or s.recruit.skill_commit
        installed = setup_paper2agent(s, commit=pin)
        for d in installed:
            print(f"installed → {d}")
        commit = json.loads((installed[0] / SOURCE_MARKER).read_text(encoding="utf-8")).get("commit") if installed else None
        print(f"Paper2Agent commit {commit or 'unknown'}" + ("" if pin else
              " (not pinned: set recruit.skill_commit or pass --commit to keep this one)"))
    elif args.cmd == "semantics":  # semantics-hook
        from .research.semantics_shadow import run_cli as semantics_cli  # semantics-hook
        raise SystemExit(semantics_cli(args, s))  # semantics-hook
    elif args.cmd == "demo":
        logging.getLogger().setLevel(logging.WARNING)
        asyncio.run(_demo(args.web or args.phone, args.port, args.phone,
                          args.host or ("0.0.0.0" if args.phone else "127.0.0.1"), args.approve_timeout))


if __name__ == "__main__":
    main(sys.argv[1:])
