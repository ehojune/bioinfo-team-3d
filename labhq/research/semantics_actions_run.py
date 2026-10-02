"""Action layer A2 (#149 결정 16): the PI's own CLI runs one existing action, ``request.followup``.

Only ``labhq semantics action run request.followup <rid>`` runs it, with ``semantics: {mode: shadow (or ab), actions:
confirm}``, at a terminal, after an explicit y/N. It sends the existing ``POST /api/requests/{rid}/followup``, so the
gateway still decides every rule it decides today and no new authority exists. Every other action stays
shadow-only, and ``hpc.*`` is refused before anything else is looked at.

Where it can run: in the PI's CLI process only. Only the shadow's ``run_cli`` imports this module, inside itself,
and only the CLI's ``labhq semantics`` calls that; the gateway, the runner, the adapters, the MCP tools and the
orchestrator never import it (tests check). A process with a staff task's environment is refused, and the runner
hands staff a fresh config copy whose gateway tokens are blank (``settings.write_staff_config``), so a staff
process has no client token to send.

No automatic resend. The execution id is written before the POST, under a per-request lock created with O_EXCL,
both fsynced with their folder entries (``Ledger``). A lost or unclear answer leaves the record ``unknown`` and the
lock in place: nothing retries, and a new run for that request is refused until the PI closes the record with
``labhq semantics action check <id>``. A crash after the intent reads back the same way. ``accepted`` (the server
took it, with its follow-up id) and the follow-up's ``completion`` (read later by ``check``) are separate fields.

Availability is three-valued, as in A1: a precondition the live records cannot show is unknown, an earlier
execution whose outcome is unknown makes it unknown, and only an all-true verdict is offered to the PI.

Records hold ids, states, condition booleans and counts only, never the question or an answer.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import secrets
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from ..util import atomic_write_text, fsync_dir

V = 1
STAFF_ENV = ("LABHQ_TASK_ID", "LABHQ_BROKER_TOKEN", "LABHQ_BROKER_URL", "LABHQ_AGENT_ID", "LABHQ_WORKDIR",
             "LABHQ_EXTRA_ROOTS")  # what the runner sets for a staff task (runner/daemon.py)
DEFAULT_TOKENS = frozenset({"", "change-me-client"})
TEXT_MAX = 4000  # the gateway's FollowupIn limit
COMPLETION = ("running", "done", "failed", "interrupted")
REQUEST_ID = re.compile(r"req_[A-Za-z0-9]{1,64}")
EXEC_ID = re.compile(r"[0-9a-f]{16}")
NOTE = {
    "refused_p3": "hpc.* 액션은 P3(HPC 계정 방식) 결정 전까지 어떤 단계에서도 실행하지 않습니다",
    "refused_action": "이 액션은 그림자 기록만 합니다. CLI가 실행하는 것은 request.followup 하나입니다",
    "refused_config": "설정이 semantics: {mode: shadow 또는 ab, actions: confirm}이 아니거나, semantics가 자동으로 꺼졌거나, "
                      "client token·gateway 주소(loopback만)가 맞지 않습니다",
    "refused_env": "PI가 터미널에서 직접 실행할 때만 동작합니다(직원 작업 환경·직원용 설정 사본·비TTY 거부)",
    "refused_target": "요청 id 형식이 아닙니다(req_...)",
    "refused_text": "글은 공백이 아닌 1–4000자여야 합니다",
    "refused_ledger": "실행 기록을 읽거나 쓸 수 없어 보내지 않습니다",
}

Http = Callable[[str, str, Any], "tuple[int | None, Any]"]


def _shadow() -> Any:
    """The #150 shadow, imported when used: the action layer reaches the model only through it, on a marked hook
    line (tests/test_semantics_pilot.py)."""
    from . import semantics_shadow  # semantics-hook: actions
    return semantics_shadow


def _acts() -> Any:
    return _shadow()._actions()  # semantics_actions (A1), the way the shadow itself reaches it


# ---------------------------------------------------------------- gate (no file, no network)

def _loopback(url: Any) -> bool:
    try:
        host = urlsplit(str(url)).hostname or ""
        return host == "localhost" or ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def gate(action: Any, settings: Any, env: Mapping[str, str], tty: bool) -> str | None:
    """Why this process may not run ``action`` now, or None. HPC first, so it is refused whatever else holds."""
    if not isinstance(action, str) or action.startswith("hpc."):
        return "refused_p3"
    if action not in _acts().CLI_EXECUTABLE:
        return "refused_action"
    shadow = _shadow()
    if shadow.actions_setting(settings) != "confirm":
        return "refused_config"
    if any(name in env for name in STAFF_ENV) or not tty or getattr(settings, "config_base", None):
        return "refused_env"
    gateway = settings.gateway
    if gateway.client_token in DEFAULT_TOKENS or not _loopback(gateway.url):
        return "refused_config"
    try:
        if shadow.read_disabled(shadow.ShadowPaths(shadow.shadow_root(settings))) is not None:
            return "refused_config"  # the shadow latched off: the action layer goes off with it
    except (OSError, ValueError):
        return "refused_config"
    return None


# ---------------------------------------------------------------- ledger (gateway state_dir/semantics/actions)

class LedgerError(Exception):
    pass


def _make_dir(path: Path) -> None:
    """Create a folder and its missing parents, each new entry fsynced into its parent folder."""
    missing = []
    while not path.exists():
        missing.append(path)
        path = path.parent
    for folder in reversed(missing):
        folder.mkdir(exist_ok=True)
        fsync_dir(folder.parent)


class Ledger:
    """One JSON record per execution and one O_EXCL lock per request while an execution is open or unknown.

    Every change is durable before the next step: a lock or record is fsynced, and so is the folder entry that
    names it (created, renamed or removed), so a power loss right after the POST cannot drop both and let the same
    follow-up go twice. On Windows the folder step rests on NTFS's metadata journal (``util.fsync_dir``)."""

    def __init__(self, settings: Any):
        self.root = _shadow().shadow_root(settings) / "actions"
        self.runs, self.open = self.root / "runs", self.root / "open"

    def _ready(self) -> None:
        shadow = _shadow()
        if shadow.inside_git_tree(self.root):
            raise LedgerError("inside a git work tree")
        for place in (self.root.parent, self.root, self.runs, self.open):
            try:
                if shadow._is_link(os.lstat(place)):
                    raise LedgerError("link")
            except FileNotFoundError:
                _make_dir(place)

    def _lock(self, action: str, rid: str) -> Path:
        return self.open / f"{action}__{rid}"

    def holder(self, action: str, rid: str) -> str | None:
        """The execution holding the request's lock: still open or unknown. Unreadable raises."""
        path = self._lock(action, rid)
        if not path.exists():
            return None
        value = path.read_text(encoding="utf-8").strip()
        return value if EXEC_ID.fullmatch(value) else "unreadable"

    def acquire(self, action: str, rid: str, exec_id: str) -> None:
        self._ready()
        path = self._lock(action, rid)
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            os.write(fd, exec_id.encode("ascii"))
            os.fsync(fd)
        finally:
            os.close(fd)
        try:
            fsync_dir(self.open)
        except OSError:
            path.unlink()  # nothing was sent: a lock that may not survive a restart is no lock
            raise

    def release(self, action: str, rid: str, exec_id: str) -> None:
        path = self._lock(action, rid)
        if self.holder(action, rid) == exec_id:
            path.unlink()
            fsync_dir(self.open)

    def write(self, record: Mapping[str, Any]) -> None:
        self._ready()
        atomic_write_text(self.runs / f"{record['exec_id']}.json", json.dumps(record, sort_keys=True))
        fsync_dir(self.runs)  # the rename's entry, not only the file's bytes

    def read(self, exec_id: str) -> dict | None:
        try:
            value = json.loads((self.runs / f"{exec_id}.json").read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        if not isinstance(value, dict) or value.get("exec_id") != exec_id:
            raise ValueError("record")
        return value

    def records(self) -> list[dict]:
        out = []
        for path in sorted(self.runs.glob("*.json")) if self.runs.is_dir() else []:
            try:
                out.append(self.read(path.stem) or {})
            except (OSError, ValueError):
                out.append({"exec_id": path.stem, "outcome": "unreadable"})
        return out

    def locks(self) -> dict[str, str]:
        if not self.open.is_dir():
            return {}
        return {path.name: (path.read_text(encoding="utf-8").strip() or "unreadable") for path in self.open.iterdir()}


# ---------------------------------------------------------------- preconditions from the live records

def _http(settings: Any) -> Http:
    def call(method: str, path: str, body: Any = None) -> tuple[int | None, Any]:
        import httpx

        base = settings.gateway.url.replace("wss://", "https://").replace("ws://", "http://").rstrip("/")
        response = httpx.request(method, base + path, json=body, timeout=30,
                                 headers={"Authorization": f"Bearer {settings.gateway.client_token}"})
        try:
            return response.status_code, response.json()
        except ValueError:
            return response.status_code, None
    return call


def _get(http: Http, path: str) -> Any:
    try:
        code, body = http("GET", path, None)
    except Exception:  # noqa: BLE001 - unreadable is unknown, never open
        return None
    return body if code == 200 else None


def conditions(req: Any, agents: Any, cso_agent: str, previous: str | None) -> tuple[dict, str | None]:
    """The server's follow-up checks as three-valued facts, from the live GETs projected at once (no text kept),
    plus ``previous_settled``: an earlier execution for this request still open or unknown is unknown, not open."""
    from ..adapters import enforces_read_only

    req = req if isinstance(req, Mapping) else {}
    followups = req.get("followups", [])
    if isinstance(followups, list) and all(isinstance(f, Mapping) for f in followups):
        running: bool | None = any(f.get("status") == "running" for f in followups)
    else:
        running = None
    mode = req.get("mode")
    agent = (req.get("agent_id") if mode == "direct" else cso_agent) if isinstance(mode, str) else None
    responder: dict[str, Any] = {"on_roster": None, "read_only": None}
    if isinstance(agents, list) and isinstance(agent, str):
        entry = next((a for a in agents if isinstance(a, Mapping) and a.get("id") == agent), None)
        responder["on_roster"] = entry is not None
        engine = entry.get("engine") if entry is not None else None
        responder["read_only"] = enforces_read_only(engine) if isinstance(engine, str) else None
    conds = _acts().followup_conditions(req.get("status"), running, responder)
    conds["previous_settled"] = True if previous is None else None
    return conds, agent if isinstance(agent, str) else None


# ---------------------------------------------------------------- run and check

def _printable(text: str) -> str:
    return "".join(c if c == "\n" or c.isprintable() else f"\\u{ord(c):04x}" for c in text)


def _tri(value: Any) -> str:
    return "참" if value is True else "거짓" if value is False else "모름"


def _record(exec_id: str, rid: str, decision: str, conds: Mapping[str, Any], text: str, ts: float) -> dict:
    return {"v": V, "exec_id": exec_id, "action": "request.followup", "request_id": rid, "ts": round(ts, 3),
            "decision": decision, "conditions": dict(conds), "text_chars": len(text), "outcome": None,
            "http": None, "status_code": None, "error_kind": None, "followup_id": None, "sent_at": None,
            "ms": None, "completion": None, "checked_at": None}


def run(settings: Any, action: str, rid: str, text: str | None, *, env: Mapping[str, str], tty: bool,
        ask: Callable[[str], str], out: Callable[[str], None], http: Http | None = None,
        reload: Callable[[], Any] | None = None, text_file: str | None = None) -> int:
    """One PI-confirmed follow-up through the existing REST path. 0 only when the server accepted it."""
    refusal = gate(action, settings, env, tty)
    if refusal is None and not (isinstance(rid, str) and REQUEST_ID.fullmatch(rid)):
        refusal = "refused_target"
    if refusal is None:
        if text is None and text_file:
            text = Path(text_file).read_text(encoding="utf-8")
        elif text is None:
            text = ask("이어 물을 글(한 줄): ")
        if not isinstance(text, str) or not text.strip() or len(text.strip()) > TEXT_MAX:
            refusal = "refused_text"
    if refusal:
        out(f"{refusal}: {NOTE[refusal]}. 아무것도 보내지 않았습니다.")
        return 1
    text = text.strip()
    http = http or _http(settings)
    ledger = Ledger(settings)
    exec_id = secrets.token_hex(8)
    try:
        previous = ledger.holder(action, rid)
    except (OSError, ValueError):
        previous = "unreadable"
    conds, responder = conditions(_get(http, f"/api/requests/{rid}"), _get(http, "/api/agents"),
                                  settings.orchestrator.cso_agent, previous)
    verdict = _acts()._all(conds.values())
    shown = " · ".join(f"{name} {_tri(value)}" for name, value in conds.items())
    if verdict is not True:
        _try_write(ledger, _record(exec_id, rid, "refused_model", conds, text, time.time()))
        tail = ("" if previous is None else f" 미결 실행 {previous}: `labhq semantics action check {previous}`로 PI가 "
                "먼저 확인하세요." if EXEC_ID.fullmatch(previous) else " 이 요청의 실행 잠금을 읽을 수 없습니다.")
        out(f"refused_model: 전제 조건이 모두 참이 아니면 보내지 않습니다(모름도 거부). {shown}.{tail} "
            "웹 이어 묻기는 그대로 쓸 수 있습니다.")
        return 1
    answer = ask("\n".join([
        "액션 request.followup (#149 결정 16 A2)",
        f"대상 요청 {rid} · 답할 직원 {_printable(responder or '-')}",
        f"전제 조건: {shown}",
        f"보낼 글({len(text)}자):", _printable(text),
        f"보내면 기존 이어 묻기 경로(POST /api/requests/{rid}/followup)로 한 번 갑니다. 비용이 들고 질문과 답이 요청 "
        "기록에 남으며 되돌릴 수 없습니다. 응답을 받지 못하면 다시 보내지 않습니다.",
        "이 y는 이 터미널의 입력일 뿐 보낸 사람의 신원 증명이 아닙니다.",
        "보낼까요? [y/N] "]))
    if str(answer).strip().lower() not in ("y", "yes"):
        _try_write(ledger, _record(exec_id, rid, "declined", conds, text, time.time()))
        out("declined: 보내지 않았습니다.")
        return 1
    try:  # the setting or the latch may have changed while the PI read
        late = gate(action, reload() if reload else settings, env, tty)
    except Exception:  # noqa: BLE001 - a config that no longer loads is not a confirm
        late = "refused_config"
    if late:
        _try_write(ledger, _record(exec_id, rid, late, conds, text, time.time()))
        out(f"{late}: 확인 뒤 다시 보니 {NOTE[late]}. 보내지 않았습니다.")
        return 1
    record = _record(exec_id, rid, "confirmed", conds, text, time.time())
    try:
        ledger.acquire(action, rid, exec_id)
    except (OSError, LedgerError):
        out(f"refused_ledger: {NOTE['refused_ledger']}(같은 요청의 미결 실행이 방금 생겼을 수 있습니다). "
            "`labhq semantics action check`로 확인하세요.")
        return 1
    try:
        ledger.write({**record, "outcome": "intent"})
    except (OSError, LedgerError):
        ledger.release(action, rid, exec_id)  # nothing was sent
        out(f"refused_ledger: {NOTE['refused_ledger']}.")
        return 1
    started = time.time()
    try:
        code, body = http("POST", f"/api/requests/{rid}/followup", {"text": text})
    except Exception as exc:  # noqa: BLE001 - lost or unclear: the server may have taken it
        code, body, error = None, None, type(exc).__name__
    else:
        error = None
    fid = body.get("followup_id") if isinstance(body, Mapping) else None
    if code == 200 and isinstance(fid, str):
        outcome = "accepted"
    elif isinstance(code, int) and 400 <= code < 500:
        outcome = "refused"  # the gateway checks before it adds the entry
    else:
        outcome = "unknown"
    record.update(outcome=outcome, http=f"{code // 100}xx" if isinstance(code, int) else None, status_code=code,
                  error_kind=error, followup_id=fid if outcome == "accepted" else None, sent_at=round(started, 3),
                  ms=round((time.time() - started) * 1000, 1))
    written = _try_write(ledger, record)
    if outcome != "unknown" and written:
        try:
            ledger.release(action, rid, exec_id)
        except OSError:
            pass  # the lock outlives a settled record: `check` removes it, nothing is resent
    if not written:  # the intent stays on disk, so this run reads back as unknown and holds the request
        out(f"결과 기록을 쓰지 못해 {exec_id}는 미결(unknown)로 남습니다.")
    if outcome == "accepted":
        out(f"accepted {exec_id}: 서버가 이어 묻기 {fid}를 받았습니다. 끝났는지(completed)는 "
            f"`labhq semantics action check {exec_id}`로 따로 확인합니다.")
        return 0
    if outcome == "refused":
        out(f"refused {exec_id}: 서버가 거부했습니다(HTTP {code}). 다시 보내지 않습니다.")
        return 1
    out(f"unknown {exec_id}: 서버가 받았는지 알 수 없습니다({error or f'HTTP {code}'}). 다시 보내지 않으며, 같은 요청의 "
        f"새 실행은 PI가 웹 기록을 보고 `labhq semantics action check {exec_id}`로 닫을 때까지 거부합니다.")
    return 1


def _try_write(ledger: Ledger, record: Mapping[str, Any]) -> bool:
    try:
        ledger.write(record)
        return True
    except (OSError, LedgerError):
        return False


def check(settings: Any, exec_id: str | None, *, env: Mapping[str, str], tty: bool, ask: Callable[[str], str],
          out: Callable[[str], None], http: Http | None = None) -> int:
    """List executions, read an accepted one's completion, or let the PI close an unknown one. Never sends."""
    if any(name in env for name in STAFF_ENV) or getattr(settings, "config_base", None):
        out(f"refused_env: {NOTE['refused_env']}.")
        return 1
    ledger = Ledger(settings)
    if exec_id is None:
        for rec in ledger.records():
            out(" ".join(str(rec.get(k) or "-") for k in ("exec_id", "action", "request_id", "decision", "outcome",
                                                           "completion")))
        recorded = {rec.get("exec_id") for rec in ledger.records()}
        for lock, holder in ledger.locks().items():
            if holder not in recorded:
                out(f"{holder} {lock} 기록 없음(잠금만 남음, unknown)")
        return 0
    if not EXEC_ID.fullmatch(exec_id):
        out("실행 id 형식이 아닙니다(16자리 hex).")
        return 1
    rec = ledger.read(exec_id)
    lock = next((name for name, holder in ledger.locks().items() if holder == exec_id), None)
    if rec is None and lock is None:
        out(f"{exec_id}: 기록이 없습니다.")
        return 1
    outcome = (rec or {}).get("outcome")
    if outcome in ("accepted", "refused") and lock is not None:  # settled, but a crash kept its lock
        ledger.release(rec["action"], rec["request_id"], exec_id)
    if outcome == "accepted":
        req = _get(http or _http(settings), f"/api/requests/{rec['request_id']}")
        followups = req.get("followups") if isinstance(req, Mapping) else None
        entry = next((f for f in followups if isinstance(f, Mapping) and f.get("id") == rec["followup_id"]), None) \
            if isinstance(followups, list) else None
        status = entry.get("status") if entry is not None else None
        rec.update(completion=status if status in COMPLETION else "unknown", checked_at=round(time.time(), 3))
        _try_write(ledger, rec)
        out(f"{exec_id}: accepted · completion {rec['completion']}")
        return 0
    if outcome in (None, "intent", "unknown") and lock is not None:
        if not tty:
            out(f"{exec_id}: 결과 미상. 닫으려면 터미널에서 실행하세요.")
            return 1
        answer = ask(f"{exec_id}: 서버가 이 이어 묻기를 받았는지 알 수 없습니다. 웹 사무실의 요청 기록에서 확인했다면 "
                     "닫습니다. 닫으면 같은 요청에 새 실행을 보낼 수 있고(자동 재전송은 없음), 이 기록은 "
                     "closed_by_pi로 남습니다. 닫을까요? [y/N] ")
        if str(answer).strip().lower() not in ("y", "yes"):
            out("닫지 않았습니다.")
            return 1
        if rec is not None:
            rec.update(outcome="closed_by_pi", checked_at=round(time.time(), 3))
            if not _try_write(ledger, rec):
                out(f"refused_ledger: {NOTE['refused_ledger']}.")
                return 1
        (ledger.open / lock).unlink()
        fsync_dir(ledger.open)
        out(f"{exec_id}: closed_by_pi")
        return 0
    out(f"{exec_id}: {rec.get('decision')} · {outcome or '-'}")
    return 0


def run_cli(args: Any, settings: Any) -> int:
    import sys

    tty = sys.stdin.isatty() and sys.stdout.isatty()

    def reload() -> Any:
        from ..settings import Settings
        return Settings.load(settings.config_path) if settings.config_path else settings

    try:
        if args.action_cmd == "run":
            return run(settings, args.action, args.request_id, None, env=os.environ, tty=tty, ask=input, out=print,
                       reload=reload, text_file=args.text_file)
        return check(settings, args.exec_id, env=os.environ, tty=tty, ask=input, out=print)
    except (OSError, ValueError) as exc:
        print(f"semantics action: {type(exc).__name__}")
        return 1
