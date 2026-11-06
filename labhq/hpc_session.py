"""One interactive HPC shell kept open on the user's PC (#1299, #1295 B).

Some sites record every ssh login and lock an idle session, and some gateways ignore a remote command and only give a
shell. So the manager opens the connection once, keeps one shell alive and sends the commands to it one line at a time.
Nothing here knows a site: the connection argv, the idle-lock time and the failure texts come from the caller (the site
profile). The module is standalone; the tools and the runner are wired to it elsewhere.

* Protocol: a command is wrapped between two ``printf`` lines. The first prints a begin marker and the second prints an
  end marker with ``$?``, so the output is exactly what lies between them, the exit code comes from the marker, and a
  login banner, the prompt or the terminal's echo of the typed line are all dropped. The markers are written so that the
  echoed command line never contains a complete marker.
* Limits: every command has a time limit and an output size cap (the rest is read and dropped so the next command starts
  clean). Commands run one at a time. A command that times out closes the session, because its state is unknown.
* Idle: a light command goes out before the site's idle lock; an idle session with no open job is closed.
* Logins: every connection goes through the #1298 counter (``hpc_login_budget``). A reconnect has a count limit and a
  growing wait, then a cooldown during which no login is made. At a login limit nothing is opened and the caller is told.
* A changed host key stops the manager. A missing tunnel asks a person to open the session and waits for ``resume()``.
"""
from __future__ import annotations

import codecs
import contextlib
import contextvars
import os
import queue
import re
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Sequence

from .hpc_login_budget import LoginBudget, LoginLimitReached

HOST_KEY_TEXTS = ("REMOTE HOST IDENTIFICATION HAS CHANGED", "Host key verification failed")
QUEUE_WAIT_S = 60.0  # how long a caller that asked for a bounded wait stands in line for the shell's one command at a time
_QUEUE_WAIT: contextvars.ContextVar[float | None] = contextvars.ContextVar("labhq_hpc_queue_wait", default=None)
PROMPT = re.compile(r"(?:^|\n)[^\n]*[$#%>] ?$")  # the last line ends like a shell prompt
_OVERLAP = 512  # longer than any marker, so a marker split over two reads is still found


def redaction_words(argv: Sequence[str]) -> tuple[str, ...]:
    """What in a connect command names the site or the user: every word that is not the program or a bare option, split
    at ``@``, ``=`` and ``,``. A host, a port, a user name and a ProxyJump chain all end up here."""
    words: set[str] = set()
    for word in list(argv)[1:]:
        if word.startswith("-") and "=" not in word:
            # An option carries nothing to hide, except a value glued to it (-Jbastion, -p2222, -lbob).
            word = word[2:] if len(word) > 2 and word[1] in "plJLRDWwiFoBbcEeImOQSX" else ""
        for part in re.split(r"[@=,]", word):
            part = part.strip()
            if len(part) >= 2 and part.lower() not in {"yes", "no", "true", "false", "user", "port", "hostname"}:
                words.add(part)
                words.update(piece for piece in part.split(":") if len(piece) >= 2)
    return tuple(sorted(words, key=len, reverse=True))


def redact(text: str, words: Sequence[str]) -> str:
    """``text`` with the site's host, port and user names hidden. A port number is hidden only where it is a port (after
    ``port`` or ``:``), so an exit code of the same digits stays readable."""
    for word in words:
        if word.isdigit():
            text = re.sub(rf"(?i)(port\s+|:){re.escape(word)}\b", lambda m: m.group(1) + "<port>", text)
        else:
            text = re.sub(re.escape(word), "<host>", text, flags=re.IGNORECASE)
    return text


def worst_case_s(cfg: "SessionConfig", command_timeout_s: float | None = None) -> float:
    waits, wait = 0.0, cfg.reconnect_wait_s
    for _ in range(cfg.reconnect_attempts - 1):
        waits += wait
        wait = min(wait * 2, cfg.reconnect_wait_max_s)
    # every attempt may wait for the gateway's first output before it is allowed its connect time
    return (cfg.reconnect_attempts * (cfg.connect_timeout_s + cfg.first_output_wait_s) + waits
            + (command_timeout_s or cfg.command_timeout_s))


def call_budget_s(cfg: "SessionConfig", command_timeout_s: float, commands: int = 1) -> float:
    """The longest a caller's call can take when it runs ``commands`` scheduler commands one after another (a status is a
    squeue and then a sacct): each may stand in line for the shell, and the first may reconnect."""
    reconnect = worst_case_s(cfg, command_timeout_s) - command_timeout_s
    return commands * (QUEUE_WAIT_S + command_timeout_s) + reconnect


@contextlib.contextmanager
def queue_wait(seconds: float | None = QUEUE_WAIT_S):
    """Inside this block ``run`` waits at most ``seconds`` for its turn and then raises ``SessionBusy`` without sending
    anything. A caller with a deadline (the broker) uses it so a command that gave up is never run later."""
    token = _QUEUE_WAIT.set(seconds)
    try:
        yield
    finally:
        _QUEUE_WAIT.reset(token)


class SessionError(RuntimeError):
    """Base of what the session manager raises."""


class SessionBusy(SessionError):
    """The shell was busy with other commands for the whole wait. This command was not sent."""


class CommandTimeout(SessionError):
    """The command did not finish in time. The session was closed: what the shell is doing is not known."""


class SessionLost(SessionError):
    """The connection dropped while a command ran. The command may or may not have run."""


class HostKeyChanged(SessionError):
    """The host key is not the one on record. The manager stops until ``reset()``; nobody should type past it."""


class NeedsHuman(SessionError):
    """The connection cannot be opened without a person (for example the tunnel is not up). ``resume()`` after they did."""


class ReconnectFailed(SessionError):
    """The reconnect attempts are used up, or the cooldown after them has not ended. No login was made."""


@dataclass
class SessionConfig:
    """What the site profile gives the manager. Every time is in seconds."""

    argv: Sequence[str]  # the connection command, used as is; it must give an interactive shell on stdin
    idle_lock_s: float | None = None  # the site's idle lock; None = unknown
    heartbeat_s: float | None = None  # None: a quarter of the idle lock, at most 900 s, 900 s when the lock is unknown
    idle_close_s: float = 900.0  # an idle session with no open job is closed after this long
    command_timeout_s: float = 60.0
    max_output: int = 1_000_000  # characters kept per command
    connect_timeout_s: float = 30.0
    reconnect_attempts: int = 3
    reconnect_wait_s: float = 5.0
    reconnect_wait_max_s: float = 60.0
    reconnect_cooldown_s: float = 60.0
    human_wait_s: float = 300.0  # after "a person must open the tunnel", the next command tries again only after this long
    host_key_texts: tuple[str, ...] = HOST_KEY_TEXTS
    # Text that proves a person must open the connection first (a site that needs a manual tunnel). Empty by default:
    # an ordinary network failure is retried by the reconnect limit, never parked as "wait for a person".
    tunnel_texts: tuple[str, ...] = ()
    heartbeat_command: str = "true"
    env: dict[str, str] | None = None  # the connection process's whole environment; None = the caller's own (tests)
    ready_command: str = "stty -echo 2>/dev/null"  # first command: proves the shell answers and turns the terminal echo off
    # A gateway may still be logging the user in when the connection opens, and drop input that arrives meanwhile. With
    # a wait set, nothing is sent until the first output (banner or prompt) came and then stayed quiet for ``settle_s``;
    # no output in the whole wait goes on anyway (a silent shell). 0 sends at once.
    first_output_wait_s: float = 0.0
    settle_s: float = 1.0
    diag_dir: str | None = None  # where the raw output of a failed connect is kept (a local folder; never in a message)
    login_label: str = "session"  # what the login counter records
    redact_words: tuple[str, ...] | None = None  # hidden in every message; None: taken from argv (host, port, user)

    def __post_init__(self) -> None:
        if not self.argv:
            raise ValueError("접속 argv가 필요합니다(사이트 프로필의 값)")
        if self.idle_lock_s is not None and self.idle_lock_s <= 0:
            raise ValueError("idle_lock_s는 양수여야 합니다")
        if self.redact_words is None:
            self.redact_words = redaction_words(self.argv)
        if self.first_output_wait_s < 0 or self.settle_s <= 0:
            raise ValueError("first_output_wait_s는 0 이상, settle_s는 양수여야 합니다")
        if self.diag_dir is None:
            self.diag_dir = os.environ.get("LABHQ_HPC_DIAG_DIR") or None
        if self.heartbeat_s is None:
            self.heartbeat_s = 900.0 if self.idle_lock_s is None else min(900.0, self.idle_lock_s / 4)
        if self.heartbeat_s <= 0:
            raise ValueError("heartbeat_s는 양수여야 합니다")
        if self.idle_lock_s is not None and self.heartbeat_s >= self.idle_lock_s:
            raise ValueError("heartbeat_s는 유휴 잠김(idle_lock_s)보다 짧아야 합니다")
        for name in ("command_timeout_s", "connect_timeout_s", "reconnect_wait_s"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name}은 양수여야 합니다")
        if self.max_output < 1 or self.reconnect_attempts < 1:
            raise ValueError("max_output과 reconnect_attempts는 1 이상이어야 합니다")


@dataclass
class CommandResult:
    exit_code: int
    output: str
    truncated: bool = False
    dropped_chars: int = 0
    elapsed_s: float = 0.0


@dataclass
class _Conn:
    proc: subprocess.Popen
    chunks: "queue.Queue[str | None]"
    reader: threading.Thread
    seen: str = ""  # the last text read, for classifying a failed connect


class HpcSession:
    def __init__(self, config: SessionConfig, *, budget: LoginBudget | None = None,
                 has_open_jobs: Callable[[], bool] | None = None,
                 on_notice: Callable[[str, str], None] | None = None,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep):
        self.cfg, self.budget, self.has_open_jobs, self.on_notice = config, budget, has_open_jobs, on_notice
        self.clock, self.sleep = clock, sleep
        self._lock = threading.RLock()  # one command at a time; also guards the connection
        self._conn: _Conn | None = None
        self._opening: _Conn | None = None  # a connection still being opened: shutdown must be able to end it too
        self._shutdown = False  # set by shutdown(): nothing opens or runs after it
        self._last_activity = clock()  # the last command a caller ran (or the connect): what "idle" means
        self._last_beat = clock()  # the last heartbeat: it keeps the site awake but is not activity
        self._cooldown_until = 0.0
        self._failed_attempts = 0
        self._stopped: HostKeyChanged | None = None
        self._human: NeedsHuman | None = None
        self._human_until = 0.0
        self.logins = 0  # connections this manager opened
        self._ticker: threading.Thread | None = None
        self._stop_ticker = threading.Event()

    # ---- public ------------------------------------------------------------------------------------------------
    @property
    def connected(self) -> bool:
        conn = self._conn
        return conn is not None and conn.proc.poll() is None

    def worst_case_s(self, command_timeout_s: float | None = None) -> float:
        """The longest one ``run`` can take: every connect attempt with its wait, then the command. A caller that waits
        for ``run`` (a broker call) must wait at least this long, or it gives up while the command still goes through."""
        return worst_case_s(self.cfg, command_timeout_s)

    def run(self, command: str, timeout_s: float | None = None) -> CommandResult:
        """Run one command in the shell and return its exit code and output. Opens (or reopens) the connection first."""
        if "\n" in command or "\r" in command:
            raise ValueError("명령은 한 줄이어야 합니다")
        wait = _QUEUE_WAIT.get()
        if wait is not None and not self._lock.acquire(timeout=wait):
            raise SessionBusy(f"HPC 셸이 {wait:g}초 동안 다른 명령을 처리 중이라 이 명령은 보내지 않았습니다")
        if wait is None:
            self._lock.acquire()
        try:
            conn = self._ensure_open()
            if self._shutdown:  # shutdown() came while the connection opened: do not send the command
                self._drop()
                raise SessionError("HPC 세션이 종료되어 명령을 보내지 않았습니다")
            result = self._exec(conn, command, timeout_s or self.cfg.command_timeout_s)
            self._last_activity = self.clock()
            return result
        finally:
            self._lock.release()

    def close(self, reason: str = "closed") -> None:
        """Close the shell. The next ``run`` opens a new connection (and counts a login)."""
        self._stop_ticker.set()
        ticker = self._ticker
        if ticker is not None and ticker is not threading.current_thread():
            ticker.join(timeout=5)  # tick() never waits for the lock, so this ends within one step
        conn = self._conn
        if conn is not None:
            self._kill(conn)  # outside the lock: a command in flight ends at once (SessionLost) and frees it
        self._kill_opening()
        if self._lock.acquire(timeout=5):
            try:
                self._drop()
            finally:
                self._lock.release()

    def shutdown(self) -> None:
        """Close for good (the runner is stopping): a connection still opening is ended, and a command that was waiting for
        its turn or for the connection is not sent afterwards."""
        self._shutdown = True
        self._kill_opening()
        self.close()

    def _kill_opening(self) -> None:
        opening = self._opening
        if opening is not None:
            self._kill(opening)

    def resume(self) -> None:
        """A person opened the session (the tunnel): allow the next ``run`` to try the connection again."""
        with self._lock:
            self._human, self._human_until = None, 0.0
            self._cooldown_until = 0.0
            self._failed_attempts = 0

    def reset(self) -> None:
        """Clear a stop after a person checked the changed host key."""
        with self._lock:
            self._stopped = None
            self.resume()

    def start(self, tick_s: float | None = None) -> None:
        """Keep the connection alive and close it when idle, from a background thread."""
        if self._ticker and self._ticker.is_alive():
            if not self._stop_ticker.is_set():
                return
            self._ticker.join(timeout=5)  # a close() just asked it to stop: wait, then start a fresh one
        self._stop_ticker.clear()
        step = tick_s or max(0.05, min(self.cfg.heartbeat_s / 2, 30.0))

        def loop() -> None:
            while not self._stop_ticker.wait(step):
                try:
                    self.tick()
                except Exception:  # a failed beat only closes the connection; the loop keeps going
                    pass

        self._ticker = threading.Thread(target=loop, name="hpc-session-tick", daemon=True)
        self._ticker.start()

    def tick(self) -> str:
        """One idle check. Returns what it did: ``busy``, ``closed``, ``idle_close``, ``heartbeat`` or ``none``."""
        if not self._lock.acquire(blocking=False):
            return "busy"
        try:
            conn = self._conn
            if conn is None:
                return "none"
            if conn.proc.poll() is not None:
                self._drop()
                return "closed"
            idle = self.clock() - self._last_activity
            if idle >= self.cfg.idle_close_s and not (self.has_open_jobs and self.has_open_jobs()):
                self._drop()
                return "idle_close"
            if self.clock() - max(self._last_activity, self._last_beat) >= self.cfg.heartbeat_s:
                try:
                    self._exec(conn, self.cfg.heartbeat_command, self.cfg.command_timeout_s)
                except SessionError:
                    return "closed"  # the beat does not reconnect: the next real command does, through the counter
                self._last_beat = self.clock()
                return "heartbeat"
            return "none"
        finally:
            self._lock.release()

    # ---- connection --------------------------------------------------------------------------------------------
    def _hide(self, text: str) -> str:
        return redact(text, self.cfg.redact_words or ())

    def _notify(self, kind: str, text: str) -> None:
        if self.on_notice:
            try:
                self.on_notice(kind, text)
            except Exception:
                pass

    def _ensure_open(self) -> _Conn:
        if self._shutdown:
            raise SessionError("HPC 세션이 종료되어 명령을 보내지 않았습니다")
        if self._stopped:
            raise self._stopped
        if self._human:
            if self.clock() < self._human_until:
                raise self._human
            self._human = None  # long enough: a person may have opened it; one more try (and one login) tells
        conn = self._conn
        if conn is not None and conn.proc.poll() is None:
            return conn
        self._drop()
        if self.clock() < self._cooldown_until:
            raise ReconnectFailed("재접속 시도를 모두 썼습니다. 대기 시간이 끝나면 다시 시도합니다. 새 로그인은 하지 않았습니다")
        wait = self.cfg.reconnect_wait_s
        last: Exception | None = None
        for attempt in range(self.cfg.reconnect_attempts):
            if self._shutdown:
                raise SessionError("HPC 세션이 종료되어 명령을 보내지 않았습니다")
            if attempt:
                self.sleep(wait)
                wait = min(wait * 2, self.cfg.reconnect_wait_max_s)
                if self._shutdown:
                    raise SessionError("HPC 세션이 종료되어 명령을 보내지 않았습니다")
            try:
                if self.budget is not None:
                    self.budget.admit(self.cfg.login_label)
            except LoginLimitReached as stop:
                self._notify("login_limit", str(stop))
                raise
            self.logins += 1
            try:
                try:
                    self._conn = self._open()
                finally:
                    self._opening = None
                self._failed_attempts = 0
                self._last_activity = self.clock()
                return self._conn
            except (HostKeyChanged, NeedsHuman):
                raise
            except SessionError as error:
                if self._shutdown:  # the connect failed because shutdown() ended it: no more attempts
                    raise SessionError("HPC 세션이 종료되어 명령을 보내지 않았습니다") from error
                last = error
        self._cooldown_until = self.clock() + self.cfg.reconnect_cooldown_s
        text = f"HPC 접속을 {self.cfg.reconnect_attempts}번 시도했으나 열지 못했습니다: {self._hide(str(last))}"
        self._notify("reconnect_failed", text)
        raise ReconnectFailed(text)

    def _open(self) -> _Conn:
        try:
            proc = subprocess.Popen(list(self.cfg.argv), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, bufsize=0, env=self.cfg.env)
        except OSError as error:
            raise SessionError(f"접속 명령을 실행하지 못했습니다: {self._hide(str(error))}") from error
        chunks: "queue.Queue[str | None]" = queue.Queue()
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")

        def pump() -> None:
            try:
                while True:
                    data = proc.stdout.read1(65536) if hasattr(proc.stdout, "read1") else proc.stdout.read(65536)
                    if not data:
                        break
                    chunks.put(decoder.decode(data))
            except (OSError, ValueError):
                pass
            finally:
                chunks.put(None)

        reader = threading.Thread(target=pump, name="hpc-session-read", daemon=True)
        reader.start()
        conn = _Conn(proc, chunks, reader)
        self._opening = conn
        if self._shutdown:  # shutdown() came while the process started: end it before anything is sent
            self._kill(conn)
        try:
            self._await_first_output(conn)
            self._exec(conn, self.cfg.ready_command, self.cfg.connect_timeout_s)  # the echo of this one is ignored
        except SessionError as error:
            text = conn.seen
            self._kill(conn)
            self._keep_diagnostics(conn, text, error)
            if any(t in text for t in self.cfg.host_key_texts):
                self._stopped = HostKeyChanged("host key가 바뀌었습니다. 접속을 멈춥니다. 사람이 확인한 뒤 reset()으로 풉니다")
                self._notify("host_key_changed", str(self._stopped))
                raise self._stopped from error
            if any(t in text for t in self.cfg.tunnel_texts):
                self._human = NeedsHuman("HPC 접속 통로가 닫혀 있습니다. 세션(터널)을 열어 주세요. 열었으면 다음 명령이 "
                                         "곧 다시 시도합니다")
                self._human_until = self.clock() + self.cfg.human_wait_s
                self._notify("needs_human", str(self._human))
                raise self._human from error
            raise
        return conn

    def _await_first_output(self, conn: _Conn) -> None:
        """Wait for the gateway's banner or prompt before the first command goes out: until a prompt shows, or output has
        come and stayed quiet for ``settle_s``, at most ``first_output_wait_s``. What arrives is kept in ``conn.seen`` for
        diagnostics."""
        wait = self.cfg.first_output_wait_s
        if wait <= 0:
            return
        deadline = time.monotonic() + wait
        heard = False
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            try:
                chunk = conn.chunks.get(timeout=min(remaining, self.cfg.settle_s) if heard else remaining)
            except queue.Empty:
                return  # quiet after output, or nothing at all within the wait
            if chunk is None:
                conn.chunks.put(None)  # the connection ended: the first command reports it
                return
            conn.seen = (conn.seen + chunk)[-4000:]
            heard = True
            if PROMPT.search(conn.seen):  # a prompt is the gateway saying it is ready: no need to wait for quiet
                return

    def _keep_diagnostics(self, conn: _Conn, text: str, error: Exception) -> None:
        """The raw output of a connect that failed, in the local diagnostics folder when one is set. It is never put in an
        error message or the feed: it can name the site."""
        folder = self.cfg.diag_dir
        if not folder:
            return
        try:
            target = Path(folder).expanduser()
            target.mkdir(parents=True, exist_ok=True, mode=0o700)
            name = f"hpc-connect-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}.log"
            body = (f"error: {type(error).__name__}\nprocess exit: {conn.proc.poll()}\n"
                    f"--- raw output (up to the last 4000 characters) ---\n{text}\n")
            # created private from the start: nothing of the raw output is ever readable by another user, even briefly
            handle = os.open(target / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(handle, "w", encoding="utf-8") as out:
                out.write(body)
        except OSError:
            pass  # diagnostics are a courtesy: a full disk must not change what the caller sees

    def _kill(self, conn: _Conn) -> None:
        for stream in (conn.proc.stdin,):
            try:
                if stream:
                    stream.close()
            except OSError:
                pass
        try:
            conn.proc.kill()
        except OSError:
            pass
        try:
            conn.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        try:
            if conn.proc.stdout:
                conn.proc.stdout.close()
        except OSError:
            pass

    def _drop(self) -> None:
        conn, self._conn = self._conn, None
        if conn is not None:
            self._kill(conn)

    # ---- one command -------------------------------------------------------------------------------------------
    def _exec(self, conn: _Conn, command: str, timeout_s: float) -> CommandResult:
        token = uuid.uuid4().hex[:12]
        begin = f"__LABHQ_BEGIN_{token}__"
        end = re.compile(rf"__LABHQ_END_{token}_(-?\d+)__")
        line = (f"printf '__LABHQ_%s_%s__\\n' BEGIN {token}\n{command}\n"
                f"printf '\\n__LABHQ_%s_%s_%s__\\n' END {token} \"$?\"\n")
        started = time.monotonic()
        deadline = started + timeout_s
        try:
            conn.proc.stdin.write(line.encode("utf-8"))
            conn.proc.stdin.flush()
        except (OSError, ValueError) as error:
            self._drop_if(conn)
            raise SessionLost(f"명령을 보내지 못했습니다: {self._hide(str(error))}") from error
        pre, body, lead = "", None, True
        kept: list[str] = []
        kept_len = dropped = 0
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._drop_if(conn)
                raise CommandTimeout(f"명령이 {timeout_s:g}초 안에 끝나지 않아 세션을 닫았습니다")
            try:
                chunk = conn.chunks.get(timeout=min(remaining, 0.5))
            except queue.Empty:
                continue
            if chunk is None:
                self._drop_if(conn)
                raise SessionLost("명령을 실행하는 동안 접속이 끊겼습니다")
            conn.seen = (conn.seen + chunk)[-4000:]
            if body is None:
                pre += chunk
                at = pre.find(begin)  # search before trimming: the marker and a big output may arrive in one read
                if at < 0:
                    pre = pre[-len(begin):]  # keep only what could be the start of a marker split across reads
                    continue
                body = pre[at + len(begin):]
            else:
                body += chunk
            if lead:  # the newline that ends the begin marker's own line is not output
                if body.startswith("\r\n"):
                    body, lead = body[2:], False
                elif body.startswith("\n"):
                    body, lead = body[1:], False
                elif body == "\r":
                    continue
                elif body:
                    lead = False
            found = end.search(body)
            if found:
                piece = body[:found.start()].replace("\r\n", "\n")  # a terminal ends its lines with CR LF
                if piece.endswith("\n"):
                    piece = piece[:-1]  # the newline our end printf adds
                room = self.cfg.max_output - kept_len
                kept.append(piece[:max(room, 0)])
                dropped += max(len(piece) - max(room, 0), 0)
                output = "".join(kept)
                return CommandResult(int(found.group(1)), output, dropped > 0, dropped, time.monotonic() - started)
            if len(body) > self.cfg.max_output - kept_len + _OVERLAP:  # over the cap: keep reading, keep only the head
                emit, body = body[:-_OVERLAP], body[-_OVERLAP:]
                if emit.endswith("\r"):  # keep a CR with its LF
                    emit, body = emit[:-1], "\r" + body
                emit = emit.replace("\r\n", "\n")
                room = max(self.cfg.max_output - kept_len, 0)
                kept.append(emit[:room])
                kept_len += min(len(emit), room)
                dropped += max(len(emit) - room, 0)

    def _drop_if(self, conn: _Conn) -> None:
        if self._conn is conn:
            self._conn = None
        self._kill(conn)
