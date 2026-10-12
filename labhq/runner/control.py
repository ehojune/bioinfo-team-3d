"""Runner side of staff control (#59 ①): steer, pause, resume and halt a task this runner holds.

The gateway owns what outlives a task: each step's steer queue, pause flag and halt request (labhq.gateway.server).
The runner applies a control only to a task it holds now (running, waiting for a run slot, or held at start) and
answers ``control.nack`` for any other, so the gateway keeps it for the step's next dispatch. It keeps no cross-turn
state of its own.

Each held task has a control file in ``<state_dir>/control/``, outside every staff write root. Every read and write of
it, the hook's and the runner's, goes under the file's lock. A Claude task with hooks reads it on every tool call
(labhq.hooks.control); for every engine the steers waiting when the CLI starts go into its prompt. When the task ends
the runner removes the file and reports in ``task.result`` which steers were delivered and which were not; the gateway
puts the undelivered ones back on the step.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..hooks import control as hook
from ..models import TaskResult

log = logging.getLogger("labhq.runner")
HALT_GRACE_S = 60.0  # a hooked Claude stops at its next tool call; past this the runner ends the CLI itself
HALT_ERROR = "PI가 멈춤(halt): 세션을 보존했습니다. 재개하면 같은 세션으로 이어 갑니다"
APPLY_LOCK_WAIT_S = 1.0  # the runner's message loop waits on the file lock this long per attempt


class ControlNotReady(RuntimeError):
    """The task was dispatched here but has not opened its control file yet: try again shortly."""


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
    started: bool = False  # the CLI process exists: steers in its prompt were delivered


class Controls:
    def __init__(self, root: Path):
        self.root = root
        self.live: dict[str, Live] = {}
        # A previous process left these. What they did not deliver is still on the gateway's step (its task.result
        # report, or the interrupted result that returns the step's steers), so they are removed, never merged.
        for path in root.glob("*") if root.is_dir() else []:
            with contextlib.suppress(OSError):
                path.unlink()

    def path(self, task_id: str) -> Path:
        return self.root / (re.sub(r"[^A-Za-z0-9_.-]", "_", task_id)[:120] + ".json")

    def start(self, task_id: str, agent_id: str, *, hooks: bool, session_id: str | None = None,
              steers: list[dict[str, Any]] | None = None, dropped: int = 0) -> None:
        """Open the task's control file with the step's undelivered steers the gateway attached to this dispatch."""
        self.root.mkdir(parents=True, exist_ok=True)
        state = hook.new_state(task_id)
        for steer in steers or []:
            if isinstance(steer, dict) and steer.get("text"):
                hook.apply_control(state, "steer", str(steer["text"]), str(steer.get("id") or "") or None)
        state["dropped"] = max(0, int(dropped or 0))
        path = self.path(task_id)
        with hook.locked(path):
            hook.write_state(path, state)
        live = Live(path, agent_id, hooks, session_id)
        live.resumed.set()
        self.live[task_id] = live

    def holds(self, task_id: str) -> bool:
        return task_id in self.live

    def halt_event(self, task_id: str) -> asyncio.Event | None:
        live = self.live.get(task_id)
        return live.halt if live else None

    def agent_of(self, task_id: str) -> str | None:
        live = self.live.get(task_id)
        return live.agent_id if live else None

    def paused(self, task_id: str) -> bool:
        live = self.live.get(task_id)
        return bool(live and not live.resumed.is_set())

    async def wait_ready(self, task_id: str) -> bool:
        """Hold a turn that has not started while the PI keeps it paused. False when the PI halted it instead."""
        live = self.live[task_id]
        while not live.resumed.is_set() and not live.halt.is_set():
            waiters = [asyncio.ensure_future(live.resumed.wait()), asyncio.ensure_future(live.halt.wait())]
            try:
                await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for waiter in waiters:
                    waiter.cancel()
        return not live.halt.is_set()

    async def apply(self, message: dict[str, Any], *, starting: bool = False) -> dict[str, Any] | None:
        """Apply one gateway ``task.control`` to a held task. None: this runner does not hold it (the caller NACKs, and the
        gateway keeps the control for the step). Raises (OSError, TimeoutError, ControlNotReady) when it could not be
        applied. ``starting``: the task was dispatched here and has not opened its file yet."""
        task_id, action = str(message.get("task_id") or ""), str(message.get("action") or "")
        steer = message.get("steer") if isinstance(message.get("steer"), dict) else {}
        live = self.live.get(task_id)
        if live is None:
            if starting:
                raise ControlNotReady(task_id)
            return None

        def change(state: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
            applied = hook.apply_control(state, action, steer.get("text"), steer.get("id"),
                                         message.get("dropped") or 0)
            return applied, hook.summary(state)

        outcome = await asyncio.to_thread(hook.update_state, live.path, change, wait_s=APPLY_LOCK_WAIT_S)
        if outcome is None or self.live.get(task_id) is not live:
            # The task ended while the change waited for the lock: finish() took the file, so nothing here holds it.
            return None
        applied, summary = outcome
        if applied:
            if action == "pause":
                live.resumed.clear()
            elif action in ("resume", "halt"):
                live.resumed.set()
            if action == "halt":
                live.halt.set()
        return {**summary, "action": action, "applied": bool(applied), "started": live.started,
                "mode": "hook" if live.hooks else "next_turn"}

    def take_prompt_steers(self, task_id: str) -> tuple[list[dict[str, Any]], int]:
        """The steers waiting now go into the prompt the CLI starts with; they count as delivered once it starts."""
        live = self.live.get(task_id)
        if live is None:
            return [], 0

        def take(state: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
            steers = list(state["steers"])
            state["steers"] = []
            state["prompt_steers"] = [*state.get("prompt_steers", []), *(s.get("id") for s in steers)]
            dropped = max(0, int(state["dropped"]) - int(state["dropped_told"]))
            state["dropped_told"] = state["dropped"]
            return steers, dropped

        return hook.update_state(live.path, take) or ([], 0)

    def started(self, task_id: str) -> None:
        """The CLI process exists: the steers in its prompt were delivered."""
        live = self.live.get(task_id)
        if live is None:
            return
        live.started = True

        def deliver(state: dict[str, Any]) -> None:
            state["delivered_ids"] = [*state.get("delivered_ids", []), *state.get("prompt_steers", [])]
            state["prompt_steers"] = []
        try:
            hook.update_state(live.path, deliver)
        except (OSError, TimeoutError):  # finish() counts prompt steers of a started CLI as delivered anyway
            log.warning("task %s: control file not updated at CLI start", task_id, exc_info=True)

    def finish(self, task_id: str) -> dict[str, Any] | None:
        """Close the task's file under its lock and report, for the gateway, which steers were delivered and which were
        not, and whether the task ended paused."""
        live = self.live.pop(task_id, None)
        if live is None:
            return None
        try:
            state = hook.remove_state(live.path)
        except (OSError, TimeoutError):
            log.warning("task %s: control file not closed", task_id, exc_info=True)
            state = None
        if state is None:
            return {"delivered": [], "undelivered": [], "paused": not live.resumed.is_set(), "unreadable": True}
        prompt = list(state.get("prompt_steers") or [])
        delivered = [*(state.get("delivered_ids") or []), *(prompt if live.started else [])]
        undelivered = [{"id": steer.get("id"), "text": steer.get("text")} for steer in state["steers"]]
        if not live.started and prompt:
            undelivered = [{"id": steer_id} for steer_id in prompt] + undelivered
        return {"delivered": [d for d in delivered if d], "undelivered": undelivered, "paused": bool(state["paused"])}

    def halted(self, task_id: str, result: TaskResult) -> TaskResult:
        """The halted result when the PI's halt is what ended the run: the hook stopped Claude, or the runner ended
        the CLI. A run that finished on its own before either keeps its result."""
        live = self.live.get(task_id)
        if live is None or not live.halt.is_set():
            return result
        try:
            state = hook.locked_state(live.path)
        except (OSError, TimeoutError):
            state = None
        if result.error_kind == "halted" or (state and state.get("halted_at")):
            return halted_result(result, live.session_id)
        return result
