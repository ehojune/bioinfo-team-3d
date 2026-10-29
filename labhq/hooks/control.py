"""Steer, pause and halt a running Claude staff member through its hooks (#59 ①).

The runner keeps one control file per task outside every staff write root (``<runner state_dir>/control/``) and
names it in ``LABHQ_CONTROL_FILE``. Claude's PreToolUse, PostToolUse and PostToolUseFailure hooks run this module,
which reads that file and consumes what it delivers. No network. Hook contract:
https://code.claude.com/docs/en/hooks (PreToolUse decision control, PostToolUse decision control, JSON output).

- steer: a PostToolUse hook returns ``hookSpecificOutput.additionalContext`` once; delivered messages leave the file.
  The gateway caps what waits on a step (``STEER_LIMIT``) and passes on the count it dropped.
- pause: PreToolUse holds the call while paused, up to ``PAUSE_HOLD_S``, then denies it. A deny holds even when a
  permission rule or the approval gate would allow the tool.
- halt: PreToolUse or PostToolUse returns ``{"continue": false, "stopReason": ...}``. Claude stops; the session stays
  on disk, so the next turn resumes it with --resume.

The runner uses the same functions to write the file, always under its lock. The gateway owns what outlives a task
(labhq.gateway.server step controls); this file lives only while the runner holds the task. Standard library only: the
hook starts on every tool call.
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
STEER_LIMIT = 20  # steers a gateway step keeps waiting for a turn; the oldest go first (the file keeps all it gets)
STEER_CHARS = 2000  # one message, as the gateway accepts it
CONTEXT_CHARS = 9000  # one delivery; Claude caps additionalContext at 10,000 characters
PAUSE_HOLD_S = 300.0  # how long PreToolUse holds one call while paused; Claude's hook timeout is 600 s
PAUSE_POLL_S = 0.5
LOCK_WAIT_S = 5.0
LOCK_STALE_S = 10.0

PAUSE_REASON = ("PI가 일시 정지함: 지금은 도구를 쓸 수 없습니다. 재개되면 같은 도구를 다시 부르세요. "
                "그 전에 작업을 끝내거나 결과를 지어내지 마세요.")
HALT_REASON = "PI가 멈춤(halt): 여기서 멈춥니다. 세션은 보존되고, 재개하면 같은 세션으로 이어 갑니다."
STEER_HEADER = "[PI 방향 수정] 실행 중에 PI가 보낸 지시입니다. 지금 하던 일에 바로 반영하세요."


def new_state(task_id: str = "") -> dict[str, Any]:
    """``steers`` wait for delivery; ``prompt_steers`` went into the turn's prompt and count as delivered once the CLI
    starts; ``delivered_ids`` reached Claude (hook) or a started CLI. The runner reports the three to the gateway."""
    return {"version": 1, "task_id": task_id, "paused": False, "constrained": False, "halt": None, "halted_at": None, "steers": [],
            "next_seq": 1, "dropped": 0, "dropped_told": 0, "delivered": 0, "prompt_steers": [], "delivered_ids": []}


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


def locked_state(path: str | Path) -> dict[str, Any] | None:
    """Read the file under its lock (the runner; the hook's polling read relies on the atomic replace instead)."""
    with locked(path):
        return read_state(path)


def remove_state(path: str | Path) -> dict[str, Any] | None:
    """Read the file and delete it under its lock: nothing can land in it after this, and a writer that comes later
    finds no file (update_state without ``create``)."""
    with locked(path):
        state = read_state(path)
        with contextlib.suppress(OSError):
            os.unlink(path)
        return state


def add_steer(state: dict[str, Any], text: str, now: float | None = None, steer_id: str | None = None) -> None:
    """``steer_id`` (the gateway control number) lets a carry merge the same message from two files once."""
    state["steers"].append({"seq": state["next_seq"], "text": str(text)[:STEER_CHARS],
                            "at": time.time() if now is None else now,
                            "id": steer_id or f"local-{state['task_id']}-{state['next_seq']}"})
    state["next_seq"] += 1


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
    state["delivered_ids"] = [*state.get("delivered_ids", []), *(steer.get("id") for steer in taken)]
    dropped = max(0, int(state["dropped"]) - int(state["dropped_told"]))
    state["dropped_told"] = state["dropped"]
    return taken, dropped


def known_steer(state: dict[str, Any], steer_id: str) -> bool:
    return (steer_id in state.get("prompt_steers", []) or steer_id in state.get("delivered_ids", [])
            or any(steer.get("id") == steer_id for steer in state["steers"]))


def apply_control(state: dict[str, Any], action: str, text: str | None = None, steer_id: str | None = None,
                  dropped: int = 0, now: float | None = None) -> bool:
    """The runner applies one gateway message and returns whether it changed the state. A steer is known by the
    gateway's id, so a resend is applied once; pause and resume carry the step's state, so the last one sent wins."""
    if action == "steer" and text:
        if steer_id and known_steer(state, steer_id):
            return False
        add_steer(state, text, now, steer_id)
        state["dropped"] += max(0, int(dropped or 0))
    elif action == "pause":
        state["paused"] = True
    elif action == "resume":
        state["paused"] = False
    elif action == "constrain":
        state["constrained"] = True
    elif action == "release_constraint":
        state["constrained"] = False
    elif action == "halt":
        state["halt"] = {"requested_at": time.time() if now is None else now}
        state["paused"] = False  # resuming a halted task continues it; it does not start paused again
    else:
        return False
    return True


def summary(state: dict[str, Any] | None) -> dict[str, Any]:
    state = state or new_state()
    return {"paused": bool(state["paused"]), "constrained": bool(state["constrained"]), "halt": bool(state["halt"]), "halted": bool(state["halted_at"]),
            "steers_pending": len(state["steers"]), "steers_delivered": int(state["delivered"]),
            "steers_dropped": int(state["dropped"])}


def _halt(path: str | Path, state: dict[str, Any]) -> dict[str, Any]:
    if not state.get("halted_at"):
        def mark(current: dict[str, Any]) -> None:
            current["halted_at"] = current.get("halted_at") or time.time()
        update_state(path, mark)
    return {"continue": False, "stopReason": HALT_REASON}


RESTRICTED_HTTP_S = 40.0  # a little over the broker's own wait for the CSO (``runner.approvals.RESTRICTED_WAIT_S``)
READ_TOOLS = frozenset({"Read", "Glob", "Grep"})


def ask_restricted(tool_name: str, tool_input: Any, *, environ: dict[str, str] | None = None) -> dict[str, Any]:
    """Ask the CSO, through the runner's broker, whether this one call may run while the task is restricted (#1008).
    Anything but a clear yes is a no: no broker, a bad answer, a timeout. Standard library only."""
    import urllib.request

    env = os.environ if environ is None else environ
    url, token = env.get("LABHQ_BROKER_URL"), env.get("LABHQ_BROKER_TOKEN")
    if not url or not token:
        return {"approved": False, "note": "CSO에 물어볼 수 없어 제한 중에는 허용하지 않습니다"}
    try:
        body = json.dumps({"tool_name": tool_name, "tool_input": tool_input if isinstance(tool_input, dict) else {}})
        request = urllib.request.Request(url.rstrip("/") + "/restricted", data=body.encode("utf-8"), method="POST",
                                         headers={"Content-Type": "application/json", "X-Labhq-Token": token})
        with urllib.request.urlopen(request, timeout=RESTRICTED_HTTP_S) as reply:
            answer = json.loads(reply.read().decode("utf-8"))
        if isinstance(answer, dict):
            return {"approved": answer.get("approved") is True, "note": str(answer.get("note") or "")}
    except Exception:
        pass
    return {"approved": False, "note": "CSO의 답을 받지 못해 제한 중에는 허용하지 않습니다"}


def pre_tool_use(path: str | Path, *, tool_name: str = "", tool_input: Any = None,
                 ask: Callable[[str, Any], dict[str, Any]] = ask_restricted, hold_s: float = PAUSE_HOLD_S, poll_s: float = PAUSE_POLL_S,
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
    if state["constrained"] and tool_name not in READ_TOOLS:
        # Restricted to read tools, but the CSO may let one call through. A yes only stops objecting here: the normal
        # permission gate still judges the call, and the restriction stays until the PI lifts it.
        answer = ask(tool_name, tool_input)
        # The wait can take up to the CSO's whole answer time. A halt or pause the PI gave meanwhile wins over the
        # answer, a refusal included: the halt must still be applied and recorded, not just this one call denied.
        state = read_state(path) or state
        if state["halt"]:
            return _halt(path, state)
        if state["paused"]:
            return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                           "permissionDecisionReason": PAUSE_REASON}}
        if answer.get("approved") is not True:
            note = answer.get("note") or "CSO가 허용하지 않았습니다"
            return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                           "permissionDecisionReason":
                                               f"폭주 제한 중이라 읽기 도구만 쓸 수 있습니다. {note}"}}
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


def hook_command(argv: list[str], *, windows: bool | None = None) -> str:
    """One command string for a Claude Code hook (#1030).

    Claude Code hands it to bash, Git Bash on Windows. ``subprocess.list2cmdline`` leaves a space-free Windows path
    bare, and bash eats its backslashes (the path collapses into one word and the command is not found), so every hook
    labhq made never ran there. The executable is double-quoted with forward slashes, which Git Bash and cmd both
    take; the arguments are plain words.
    """
    import shlex
    import subprocess

    windows = os.name == "nt" if windows is None else windows
    executable, *rest = argv
    if windows:
        return " ".join(['"' + str(executable).replace("\\", "/").replace('"', "") + '"',
                         *([subprocess.list2cmdline(rest)] if rest else [])])
    return shlex.join([str(executable), *rest])


def hook_commands() -> dict[str, str]:
    return {name: hook_command([sys.executable, "-m", "labhq.hooks.control", name]) for name in EVENTS}


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
    tool_name, tool_input = "", None
    with contextlib.suppress(Exception):
        payload = json.load(sys.stdin.buffer)
        if isinstance(payload, dict) and isinstance(payload.get("tool_name"), str):
            tool_name, tool_input = payload["tool_name"], payload.get("tool_input")
    path = os.environ.get(CONTROL_ENV)
    if not path:
        return 0
    try:
        out = pre_tool_use(path, tool_name=tool_name, tool_input=tool_input) if event == "PreToolUse" else post_tool_use(path, event)
    except Exception:
        return 0
    if out:
        sys.stdout.write(json.dumps(out))
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
