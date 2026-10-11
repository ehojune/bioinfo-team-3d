"""Runner side of staff control (#59 ①): steer, pause, resume and halt a task the PI is watching.

Each running task gets a control file in ``<state_dir>/control/``, outside every staff write root; the gateway's
``task.control`` messages are applied there. A Claude task with hooks reads it on every tool call
(labhq.hooks.control). Other engines, and read-only Claude runs, which take no hooks, get the same requests at the
turn boundary instead:

- steer: the next turn that resumes the session starts with the messages
- pause: the next turn of the session is not started until the PI resumes
- halt: the CLI is ended at once, keeping its session id, and the result is ``halted``

What a task leaves undelivered when it ends is kept for its session (``control_carry``), so a wake, retry or
resume turn picks it up.
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..hooks import control as hook
from ..models import TaskResult
from ..store import StateStore

HALT_GRACE_S = 60.0  # a hooked Claude stops at its next tool call; past this the runner ends the CLI itself
CARRY_KEEP_S = 2 * 86400  # like a kept task cancel: longer than any runner turn
HALT_ERROR = "PI가 멈춤(halt): 세션을 보존했습니다. 재개하면 같은 세션으로 이어 갑니다"


def halted_result(result: TaskResult, session_id: str | None = None) -> TaskResult:
    """Not a failure to retry or repair: the PI stopped it and can resume it."""
    return result.model_copy(update={"ok": False, "error_kind": "halted", "error": HALT_ERROR, "environment": None,
                                     "session_id": result.session_id or session_id})


def steer_prompt(steers: list[dict[str, Any]], dropped: int = 0) -> str:
    return hook.steer_text(steers, dropped)


@dataclass
class Live:
    path: Path
    agent_id: str
    hooks: bool
    session_id: str | None
    halt: asyncio.Event = field(default_factory=asyncio.Event)
    resumed: asyncio.Event = field(default_factory=asyncio.Event)
    killed: bool = False  # the runner ended the CLI for a halt


class Controls:
    def __init__(self, root: Path, store: StateStore):
        self.root = root
        self.store = store
        self.live: dict[str, Live] = {}
        # A runner that stopped mid-task left its files; an older process of the same runner may still use recent ones.
        now = time.time()
        for path in root.glob("*.json*") if root.is_dir() else []:
            try:
                if now - path.stat().st_mtime > CARRY_KEEP_S:
                    path.unlink()
            except OSError:
                pass

    def path(self, task_id: str) -> Path:
        return self.root / (re.sub(r"[^A-Za-z0-9_.-]", "_", task_id)[:120] + ".json")

    def start(self, task_id: str, agent_id: str, *, hooks: bool,
              session_id: str | None) -> tuple[list[dict[str, Any]], int, bool]:
        """Open the task's control file. Returns what the session carried: steer messages for the prompt, how many
        the queue dropped, and whether the session is paused (the turn then waits for resume before it starts)."""
        carry = self.store.get("control_carry", session_id) if session_id else None
        if carry is not None:
            self.store.delete("control_carry", session_id)
        carry = carry or {}
        paused = bool(carry.get("paused"))
        self.root.mkdir(parents=True, exist_ok=True)
        state = hook.new_state(task_id)
        state["paused"] = paused
        hook.write_state(self.path(task_id), state)
        live = Live(self.path(task_id), agent_id, hooks, session_id)
        if not paused:
            live.resumed.set()
        self.live[task_id] = live
        steers = [s for s in carry.get("steers") or [] if isinstance(s, dict) and s.get("text")]
        return steers, int(carry.get("dropped") or 0), paused

    def halt_event(self, task_id: str) -> asyncio.Event | None:
        live = self.live.get(task_id)
        return live.halt if live else None

    def agent_of(self, task_id: str) -> str | None:
        live = self.live.get(task_id)
        return live.agent_id if live else None

    async def wait_resumed(self, task_id: str) -> bool:
        """Hold a paused turn before it starts. False when the PI halted it instead."""
        live = self.live[task_id]
        while not live.resumed.is_set() and not live.halt.is_set():
            waiters = [asyncio.ensure_future(live.resumed.wait()), asyncio.ensure_future(live.halt.wait())]
            try:
                await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for waiter in waiters:
                    waiter.cancel()
        return not live.halt.is_set()

    async def apply(self, message: dict[str, Any]) -> dict[str, Any] | None:
        """Apply one gateway ``task.control``. Returns the control summary for the agent.control event, or None when
        the task is not running here and left no session to carry the request."""
        task_id, action = str(message.get("task_id") or ""), str(message.get("action") or "")
        text, seq = message.get("text"), message.get("seq")
        live = self.live.get(task_id)
        if live is None:
            return self._carry_late(task_id, action, text)

        def change(state: dict[str, Any]) -> bool:
            return hook.apply_control(state, action, text, seq if isinstance(seq, int) else None)

        applied = await asyncio.to_thread(hook.update_state, live.path, change, create=task_id)
        state = hook.read_state(live.path)
        if applied:
            if action == "pause":
                live.resumed.clear()
            elif action in ("resume", "halt"):
                live.resumed.set()
            if action == "halt":
                live.halt.set()
        return {**hook.summary(state), "action": action, "applied": bool(applied),
                "mode": "hook" if live.hooks else "next_turn"}

    def _carry_late(self, task_id: str, action: str, text: Any) -> dict[str, Any] | None:
        """A request for a task that just ended goes to its session's next turn, if it left one."""
        ended = self.store.get("control_ended", task_id)
        session = (ended or {}).get("session_id")
        if not session or action not in ("steer", "pause", "resume"):
            return None
        carry = self.store.get("control_carry", session) or {"steers": [], "paused": False, "dropped": 0}
        state = hook.new_state(task_id)
        state.update(steers=list(carry.get("steers") or []), paused=bool(carry.get("paused")),
                     dropped=int(carry.get("dropped") or 0))
        state["next_seq"] = max([0, *(int(s.get("seq") or 0) for s in state["steers"])]) + 1
        hook.apply_control(state, action, text if isinstance(text, str) else None)
        self.store.put("control_carry", session, {"steers": state["steers"], "paused": state["paused"],
                                                   "dropped": state["dropped"], "at": time.time(),
                                                   "task_id": task_id})
        return {**hook.summary(state), "action": action, "applied": True, "mode": "next_turn", "ended": True}

    def finish(self, task_id: str, session_id: str | None) -> dict[str, Any] | None:
        """Close the task's file. Undelivered messages and a pause stay with the session for its next turn."""
        live = self.live.pop(task_id, None)
        if live is None:
            return None
        state = hook.read_state(live.path)
        for path in (live.path, live.path.with_name(live.path.name + ".lock")):
            try:
                path.unlink()
            except OSError:
                pass
        now = time.time()
        session = session_id or live.session_id
        if session:
            self.store.put("control_ended", task_id, {"session_id": session, "at": now})
            if state and (state["steers"] or state["paused"]):
                self.store.put("control_carry", session, {
                    "steers": state["steers"], "paused": state["paused"],
                    "dropped": max(0, int(state["dropped"]) - int(state["dropped_told"])),
                    "at": now, "task_id": task_id})
        for table in ("control_carry", "control_ended"):
            for key, entry in self.store.all(table).items():
                if now - float((entry or {}).get("at") or 0) > CARRY_KEEP_S:
                    self.store.delete(table, key)
        return state

    def halted(self, task_id: str, result: TaskResult) -> TaskResult:
        """The halted result when the PI's halt is what ended the run: the hook stopped Claude, or the runner ended
        the CLI. A run that finished on its own before either keeps its result."""
        live = self.live.get(task_id)
        if live is None or not live.halt.is_set():
            return result
        state = hook.read_state(live.path)
        if result.error_kind == "halted" or live.killed or (state and state.get("halted_at")):
            return halted_result(result, live.session_id)
        return result
