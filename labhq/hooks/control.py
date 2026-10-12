"""Steer, pause and halt a running Claude staff member through its hooks (#59 ①).

The runner keeps one control file per task outside every staff write root (``<runner state_dir>/control/``) and
names it in ``LABHQ_CONTROL_FILE``. Claude's PreToolUse, PostToolUse and PostToolUseFailure hooks run this module,
which reads that file and consumes what it delivers. No network. Hook contract:
https://code.claude.com/docs/en/hooks (PreToolUse decision control, PostToolUse decision control, JSON output).

- steer: a PostToolUse hook returns ``hookSpecificOutput.additionalContext`` once; delivered messages leave the file.
  The queue keeps the newest ``STEER_LIMIT`` and counts what it dropped.
- pause: PreToolUse holds the call while paused, up to ``PAUSE_HOLD_S``, then denies it. A deny holds even when a
  permission rule or the approval gate would allow the tool.
- halt: PreToolUse or PostToolUse returns ``{"continue": false, "stopReason": ...}``. Claude stops; the session stays
  on disk, so the next turn resumes it with --resume.

The runner uses the same functions to write the file. Standard library only: the hook starts on every tool call.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Callable, Iterator

CONTROL_ENV = "LABHQ_CONTROL_FILE"
STEER_LIMIT = 20  # queued messages kept; the oldest go first
STEER_CHARS = 2000  # one message, as the gateway accepts it
CONTEXT_CHARS = 9000  # one delivery; Claude caps additionalContext at 10,000 characters
PAUSE_HOLD_S = 300.0  # how long PreToolUse holds one call while paused; Claude's hook timeout is 600 s
PAUSE_POLL_S = 0.5
LOCK_WAIT_S = 5.0
SEQS_KEPT = 200  # gateway control numbers a file remembers, so a resend is applied once
LOCK_STALE_S = 10.0

PAUSE_REASON = ("PI가 일시 정지함: 지금은 도구를 쓸 수 없습니다. 재개되면 같은 도구를 다시 부르세요. "
                "그 전에 작업을 끝내거나 결과를 지어내지 마세요.")
HALT_REASON = "PI가 멈춤(halt): 여기서 멈춥니다. 세션은 보존되고, 재개하면 같은 세션으로 이어 갑니다."
STEER_HEADER = "[PI 방향 수정] 실행 중에 PI가 보낸 지시입니다. 지금 하던 일에 바로 반영하세요."


def new_state(task_id: str = "") -> dict[str, Any]:
    """``agent_id``/``session_id`` name the session carry a restarted runner returns this file's leftovers to;
    ``prompt_steers`` are the carried steer ids this turn's prompt holds, consumed once the CLI starts."""
    return {"version": 1, "task_id": task_id, "paused": False, "halt": None, "halted_at": None, "steers": [],
            "next_seq": 1, "dropped": 0, "dropped_told": 0, "delivered": 0, "applied_seqs": [], "toggle_seq": 0,
            "agent_id": "", "session_id": None, "prompt_steers": []}


def read_state(path: str | Path) -> dict[str, Any] | None:
    """The file's state, or None when there is none (the runner gives no control to this task) or it cannot be read."""
    for attempt in range(20):
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            break
        except PermissionError:  # Windows: the runner is replacing the file this instant
            if attempt == 19:
                return None
            time.sleep(0.01)
        except (OSError, ValueError):
            return None
    if not isinstance(data, dict):
        return None
    state = new_state()
    state.update({key: data[key] for key in state if key in data})
    if not isinstance(state["steers"], list):
        state["steers"] = []
    return state


def _replace(source: Path, target: Path) -> None:
    # Windows refuses to replace a file another process has open for that instant (the hook polling): retry.
    for attempt in range(100):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if attempt == 99:
                raise
            time.sleep(0.01)


def write_state(path: str | Path, state: dict[str, Any]) -> None:
    target = Path(path)
    temporary = target.with_name(f".{target.name}.{os.getpid()}.{time.monotonic_ns()}.tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    try:
        _replace(temporary, target)
    except OSError:
        with contextlib.suppress(OSError):
            os.unlink(temporary)
        raise


@contextlib.contextmanager
def locked(path: str | Path, wait_s: float = LOCK_WAIT_S) -> Iterator[None]:
    """One writer at a time: the runner applying a gateway message, or one of several parallel tool hooks."""
    lock = Path(path).with_name(Path(path).name + ".lock")
    deadline = time.monotonic() + wait_s
    while True:
        try:
            os.close(os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            break
        except (FileExistsError, PermissionError):  # Windows: PermissionError while the old lock is being deleted
            try:
                if time.time() - os.stat(lock).st_mtime > LOCK_STALE_S:  # its holder died
                    os.unlink(lock)
                    continue
            except OSError:
                continue
            if time.monotonic() > deadline:
                raise TimeoutError("control file lock is busy") from None
            time.sleep(0.01)
    try:
        yield
    finally:
        with contextlib.suppress(OSError):
            os.unlink(lock)


def update_state(path: str | Path, change: Callable[[dict[str, Any]], Any], *, create: str | None = None,
                 wait_s: float = LOCK_WAIT_S) -> Any:
    """Apply ``change`` to the file's state under the lock and write it back. ``create`` names the task when the file
    may not exist yet; without it a missing file is left alone and None returned. TimeoutError: the lock stayed busy."""
    with locked(path, wait_s):
        state = read_state(path)
        if state is None:
            if create is None:
                return None
            state = new_state(create)
        outcome = change(state)
        write_state(path, state)
        return outcome


def add_steer(state: dict[str, Any], text: str, now: float | None = None, steer_id: str | None = None) -> None:
    """``steer_id`` (the gateway control number) lets a carry merge the same message from two files once."""
    state["steers"].append({"seq": state["next_seq"], "text": str(text)[:STEER_CHARS],
                            "at": time.time() if now is None else now,
                            "id": steer_id or f"local-{state['task_id']}-{state['next_seq']}"})
    state["next_seq"] += 1
    over = len(state["steers"]) - STEER_LIMIT
    if over > 0:
        del state["steers"][:over]
        state["dropped"] += over


def steer_text(steers: list[dict[str, Any]], dropped: int = 0) -> str:
    lines = [STEER_HEADER, *(f"- {steer.get('text', '')}" for steer in steers)]
    if dropped:
        lines.append(f"(대기열 상한 {STEER_LIMIT}개를 넘어 앞선 지시 {dropped}개는 전달하지 못하고 버렸습니다)")
    return "\n".join(lines)


def take_steers(state: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
    """Remove the messages one delivery carries (as many as fit CONTEXT_CHARS, at least one) and the drop count not
    yet told."""
    taken, size = [], len(STEER_HEADER) + 200
    for steer in state["steers"]:
        size += len(str(steer.get("text", ""))) + 3
        if taken and size > CONTEXT_CHARS:
            break
        taken.append(steer)
    del state["steers"][:len(taken)]
    state["delivered"] += len(taken)
    dropped = max(0, int(state["dropped"]) - int(state["dropped_told"]))
    state["dropped_told"] = state["dropped"]
    return taken, dropped


def apply_control(state: dict[str, Any], action: str, text: str | None = None, seq: int | None = None,
                  now: float | None = None) -> bool:
    """The runner applies one gateway message and returns whether it changed the state.

    Each ``seq`` is applied once, so a resend after a reconnect cannot steer twice. A message that failed and comes back
    after later ones still applies, except a pause or resume older than one already applied: the PI's last toggle wins."""
    if seq is not None:
        seen = [number for number in state.get("applied_seqs") or [] if isinstance(number, int)]
        if seq in seen:
            return False
        state["applied_seqs"] = [*seen, seq][-SEQS_KEPT:]
        if action in ("pause", "resume"):
            if seq < int(state.get("toggle_seq") or 0):
                return False
            state["toggle_seq"] = seq
    if action == "steer" and text:
        add_steer(state, text, now, f"ctl-{seq}" if seq is not None else None)
    elif action == "pause":
        state["paused"] = True
    elif action == "resume":
        state["paused"] = False
    elif action == "halt":
        state["halt"] = {"requested_at": time.time() if now is None else now}
        state["paused"] = False  # resuming a halted task continues it; it does not start paused again
    else:
        return False
    return True


def summary(state: dict[str, Any] | None) -> dict[str, Any]:
    state = state or new_state()
    return {"paused": bool(state["paused"]), "halt": bool(state["halt"]), "halted": bool(state["halted_at"]),
            "steers_pending": len(state["steers"]), "steers_delivered": int(state["delivered"]),
            "steers_dropped": int(state["dropped"])}


def _halt(path: str | Path, state: dict[str, Any]) -> dict[str, Any]:
    if not state.get("halted_at"):
        def mark(current: dict[str, Any]) -> None:
            current["halted_at"] = current.get("halted_at") or time.time()
        update_state(path, mark)
    return {"continue": False, "stopReason": HALT_REASON}


def pre_tool_use(path: str | Path, *, hold_s: float = PAUSE_HOLD_S, poll_s: float = PAUSE_POLL_S,
                 sleep: Callable[[float], Any] = time.sleep,
                 clock: Callable[[], float] = time.monotonic) -> dict[str, Any] | None:
    """Halt stops Claude before the tool runs; pause holds the call, then denies it if the PI has not resumed."""
    state = read_state(path)
    if state is None:
        return None
    deadline = clock() + hold_s
    while state["paused"] and not state["halt"] and clock() < deadline:
        sleep(poll_s)
        state = read_state(path) or state  # an unreadable moment does not lift the pause
    if state["halt"]:
        return _halt(path, state)
    if state["paused"]:
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                       "permissionDecisionReason": PAUSE_REASON}}
    return None


def post_tool_use(path: str | Path, event: str = "PostToolUse") -> dict[str, Any] | None:
    """Halt stops Claude after the tool; otherwise queued steer messages go to Claude once."""
    state = read_state(path)
    if state is None or not (state["halt"] or state["steers"]):
        return None  # the common case reads once and takes no lock
    if state["halt"]:
        return _halt(path, state)
    delivered = update_state(path, take_steers)
    if not delivered or not delivered[0]:
        return None
    steers, dropped = delivered
    return {"hookSpecificOutput": {"hookEventName": event, "additionalContext": steer_text(steers, dropped)}}


EVENTS = {"pre": "PreToolUse", "post": "PostToolUse", "failure": "PostToolUseFailure"}


def hook_commands() -> dict[str, str]:
    import subprocess
    return {name: subprocess.list2cmdline([sys.executable, "-m", "labhq.hooks.control", name]) for name in EVENTS}


def add_control_hooks(settings: dict[str, Any]) -> dict[str, Any]:
    """Merge the control hooks for every tool (no matcher) with any configured Claude hooks."""
    merged = dict(settings)
    hooks = {key: list(value) for key, value in (merged.get("hooks") or {}).items()}
    commands = hook_commands()
    for name, event in EVENTS.items():
        hooks.setdefault(event, []).append({"hooks": [{"type": "command", "command": commands[name]}]})
    merged["hooks"] = hooks
    return merged


def main(argv: list[str] | None = None) -> int:
    """Fail open: a broken control file must not stop the staff run. JSON goes out as ASCII, whatever the console."""
    args = sys.argv[1:] if argv is None else argv
    event = EVENTS.get(args[0] if args else "post", "PostToolUse")
    with contextlib.suppress(Exception):
        sys.stdin.buffer.read()  # Claude writes the tool call; the decision does not depend on it
    path = os.environ.get(CONTROL_ENV)
    if not path:
        return 0
    try:
        out = pre_tool_use(path) if event == "PreToolUse" else post_tool_use(path, event)
    except Exception:
        return 0
    if out:
        sys.stdout.write(json.dumps(out))
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
