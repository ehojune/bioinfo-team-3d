"""Runner side of staff control (#59 ①): steer, pause, resume and halt a task the PI is watching.

Each running task gets a control file in ``<state_dir>/control/``, outside every staff write root; the gateway's
``task.control`` messages are applied there. A Claude task with hooks reads it on every tool call
(labhq.hooks.control). Other engines, and read-only Claude runs, which take no hooks, get the same requests at the
turn boundary instead:

- steer: the next turn that resumes the session starts with the messages
- pause: the next turn of the session is not started until the PI resumes
- halt: the CLI is ended at once, keeping its session id, and the result is ``halted``

Invariant (PR #532 review): a request stays on disk under ``runner.state_dir`` until it is consumed. A steer is
consumed when a hook delivered it or a prompt holding it reached a started CLI; a pause when the PI lifts it; a halt
when it produced a ``halted`` result. What a task leaves is kept for its session as ``control_carry``, keyed by
(agent, session); a carry is never taken at task start, only trimmed once its contents are consumed. A runner that
starts again rebuilds the carries from the task files its previous process left.
"""

from __future__ import annotations

import asyncio
import json
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
APPLY_LOCK_WAIT_S = 1.0  # the runner's message loop waits on the file lock this long per attempt


class ControlNotReady(RuntimeError):
    """The task was dispatched here but has not opened its control file yet: try again shortly."""


def halted_result(result: TaskResult, session_id: str | None = None) -> TaskResult:
    """Not a failure to retry or repair: the PI stopped it and can resume it."""
    return result.model_copy(update={"ok": False, "error_kind": "halted", "error": HALT_ERROR, "environment": None,
                                     "session_id": result.session_id or session_id})


def steer_prompt(steers: list[dict[str, Any]], dropped: int = 0) -> str:
    return hook.steer_text(steers, dropped)


def carry_key(agent_id: str, session_id: str) -> str:
    """Two staff members may report the same session string (an `engine: cli` tool's "default"): the carry is theirs
    together (PR #532 review)."""
    return json.dumps([agent_id, session_id], ensure_ascii=False)


def _steer_id(steer: dict[str, Any]) -> str:
    return str(steer.get("id") or f"{steer.get('at')}:{steer.get('text')}")


@dataclass
class Live:
    path: Path
    agent_id: str
    hooks: bool
    session_id: str | None
    halt: asyncio.Event = field(default_factory=asyncio.Event)
    resumed: asyncio.Event = field(default_factory=asyncio.Event)


class Controls:
    def __init__(self, root: Path, store: StateStore):
        self.root = root
        self.store = store
        self.live: dict[str, Live] = {}
        # Tasks of a previous process the PI halted: their interrupted result is `halted`, so the step parks.
        self.recovered_halts: dict[str, dict[str, Any]] = {}
        self._recover()

    def path(self, task_id: str) -> Path:
        return self.root / (re.sub(r"[^A-Za-z0-9_.-]", "_", task_id)[:120] + ".json")

    # ---------------- carry ----------------
    def _carry(self, key: str) -> dict[str, Any]:
        carry = self.store.get("control_carry", key) or {}
        return {"steers": [s for s in carry.get("steers") or [] if isinstance(s, dict) and s.get("text")],
                "paused": bool(carry.get("paused")), "toggle_seq": int(carry.get("toggle_seq") or 0),
                "dropped": int(carry.get("dropped") or 0)}

    def _put_carry(self, key: str, carry: dict[str, Any], task_id: str) -> None:
        if carry["steers"] or carry["paused"] or carry["dropped"]:
            self.store.put("control_carry", key, {**carry, "at": time.time(), "task_id": task_id})
        else:
            self.store.delete("control_carry", key)

    def _close(self, task_id: str, agent_id: str, session_id: str, state: dict[str, Any]) -> None:
        """Return a task file's unconsumed requests to its session carry, and record what it applied so a late resend
        is applied once. The carry is written before the caller removes the file."""
        key = carry_key(agent_id, session_id)
        carry = self._carry(key)
        known = {_steer_id(steer) for steer in carry["steers"]}
        carry["steers"] += [steer for steer in state["steers"] if _steer_id(steer) not in known]
        if int(state.get("toggle_seq") or 0) >= carry["toggle_seq"]:  # the file saw every toggle the carry had
            carry["paused"], carry["toggle_seq"] = bool(state["paused"]), int(state.get("toggle_seq") or 0)
        carry["dropped"] += max(0, int(state["dropped"]) - int(state["dropped_told"]))
        self._put_carry(key, carry, task_id)
        self.store.put("control_ended", task_id, {
            "agent_id": agent_id, "session_id": session_id, "at": time.time(),
            "applied_seqs": list(state.get("applied_seqs") or []), "toggle_seq": int(state.get("toggle_seq") or 0)})

    def _remove(self, path: Path) -> None:
        for leftover in (path, path.with_name(path.name + ".lock")):
            try:
                leftover.unlink()
            except OSError:
                pass

    def _recover(self) -> None:
        """A runner process that stopped left its tasks' files. Their unconsumed requests go back to the session carry;
        a file whose session never became known stays until it ages out."""
        if not self.root.is_dir():
            return
        now = time.time()
        for path in sorted(self.root.glob("*.json")):
            try:
                old = now - path.stat().st_mtime > CARRY_KEEP_S
            except OSError:
                continue
            state = hook.read_state(path)
            if state is None:
                if old:
                    self._remove(path)
                continue
            task_id = str(state.get("task_id") or path.stem)
            agent_id, session_id = str(state.get("agent_id") or ""), state.get("session_id")
            if state["halt"]:
                self.recovered_halts[task_id] = {"agent_id": agent_id, "session_id": session_id}
            if agent_id and session_id:
                self._close(task_id, agent_id, str(session_id), state)
                self._remove(path)
            elif old:
                self._remove(path)
        for path in self.root.glob("*.lock"):  # a lock its holder never released
            if not path.with_suffix("").exists():
                self._remove(path)
        self._prune(now)

    def _prune(self, now: float) -> None:
        for table in ("control_carry", "control_ended"):
            for key, entry in self.store.all(table).items():
                if now - float((entry or {}).get("at") or 0) > CARRY_KEEP_S:
                    self.store.delete(table, key)

    # ---------------- task life ----------------
    def start(self, task_id: str, agent_id: str, *, hooks: bool,
              session_id: str | None) -> tuple[list[dict[str, Any]], int, bool]:
        """Open the task's control file. Returns what the session carried: steer messages for the prompt, how many
        the queue dropped, and whether the session is paused (the turn then waits for resume before it starts).
        The carry stays until the CLI starts with those steers (``started``) or the pause is lifted."""
        carry = (self._carry(carry_key(agent_id, session_id)) if session_id else
                 {"steers": [], "paused": False, "toggle_seq": 0, "dropped": 0})
        self.root.mkdir(parents=True, exist_ok=True)
        state = hook.new_state(task_id)
        state.update(paused=carry["paused"], toggle_seq=carry["toggle_seq"], agent_id=agent_id, session_id=session_id,
                     prompt_steers=[_steer_id(steer) for steer in carry["steers"]])
        hook.write_state(self.path(task_id), state)
        live = Live(self.path(task_id), agent_id, hooks, session_id)
        if not carry["paused"]:
            live.resumed.set()
        self.live[task_id] = live
        return carry["steers"], carry["dropped"], carry["paused"]

    def started(self, task_id: str) -> None:
        """The CLI started with this turn's prompt: the carried steers in it are consumed."""
        live = self.live.get(task_id)
        state = hook.read_state(live.path) if live else None
        if not live or not state or not live.session_id or not state.get("prompt_steers"):
            return
        delivered = set(state["prompt_steers"])
        key = carry_key(live.agent_id, live.session_id)
        carry = self._carry(key)
        carry["steers"] = [steer for steer in carry["steers"] if _steer_id(steer) not in delivered]
        carry["dropped"] = 0  # the prompt told the drop count too
        self._put_carry(key, carry, task_id)

        def clear(current: dict[str, Any]) -> None:
            current["prompt_steers"] = []
        hook.update_state(live.path, clear)

    def note_session(self, task_id: str, session_id: str | None) -> None:
        """The engine reported its session: a restarted runner can return this file's leftovers to that carry."""
        live = self.live.get(task_id)
        if not live or not session_id or live.session_id == session_id:
            return
        live.session_id = session_id

        def note(current: dict[str, Any]) -> None:
            current["session_id"] = session_id
        try:
            hook.update_state(live.path, note)
        except (OSError, TimeoutError):
            pass  # finish() still has it in memory

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

    async def apply(self, message: dict[str, Any], *, starting: bool = False) -> dict[str, Any] | None:
        """Apply one gateway ``task.control``. Returns the control summary for the agent.control event, or None when
        the task is not here and left no session to carry the request. Raises (OSError, TimeoutError,
        ControlNotReady) when it could not be applied, so the caller keeps it unacknowledged. ``starting``: the task was
        dispatched to this runner and has not opened its file yet."""
        task_id, action = str(message.get("task_id") or ""), str(message.get("action") or "")
        text, seq = message.get("text"), message.get("seq")
        seq = seq if isinstance(seq, int) and not isinstance(seq, bool) else None
        live = self.live.get(task_id)
        if live is None:
            if starting:
                raise ControlNotReady(task_id)
            return self._carry_late(task_id, action, text, seq)

        def change(state: dict[str, Any]) -> bool:
            return hook.apply_control(state, action, text, seq)

        applied = await asyncio.to_thread(hook.update_state, live.path, change, create=task_id,
                                          wait_s=APPLY_LOCK_WAIT_S)
        state = hook.read_state(live.path)
        if applied:
            if action == "pause":
                live.resumed.clear()
            elif action in ("resume", "halt"):
                live.resumed.set()
            if action == "halt":
                live.halt.set()
            if action in ("resume", "halt") and live.session_id:
                # The carry's pause is lifted the moment the PI lifts it, not when the turn ends.
                key = carry_key(live.agent_id, live.session_id)
                carry = self._carry(key)
                if carry["paused"] and (seq is None or seq >= carry["toggle_seq"]):
                    carry["paused"], carry["toggle_seq"] = False, max(carry["toggle_seq"], seq or 0)
                    self._put_carry(key, carry, task_id)
        return {**hook.summary(state), "action": action, "applied": bool(applied),
                "mode": "hook" if live.hooks else "next_turn"}

    def _carry_late(self, task_id: str, action: str, text: Any, seq: int | None = None) -> dict[str, Any] | None:
        """A request for a task that just ended goes to its session's next turn, if it left one. The ended task's record
        keeps its owner and the numbers it applied, so a resend is applied once and only to that staff member's
        session."""
        ended = self.store.get("control_ended", task_id)
        agent_id, session = (ended or {}).get("agent_id"), (ended or {}).get("session_id")
        if not agent_id or not session or action not in ("steer", "pause", "resume"):
            return None
        key = carry_key(agent_id, session)
        carry = self._carry(key)
        state = hook.new_state(task_id)
        state.update(steers=carry["steers"], paused=carry["paused"], dropped=carry["dropped"],
                     applied_seqs=list(ended.get("applied_seqs") or []),
                     toggle_seq=max(carry["toggle_seq"], int(ended.get("toggle_seq") or 0)))
        state["next_seq"] = max([0, *(int(s.get("seq") or 0) for s in state["steers"])]) + 1
        applied = hook.apply_control(state, action, text if isinstance(text, str) else None, seq)
        self.store.put("control_ended", task_id, {**ended, "applied_seqs": state["applied_seqs"],
                                                  "toggle_seq": state["toggle_seq"]})
        if applied:
            self._put_carry(key, {"steers": state["steers"], "paused": state["paused"],
                                  "toggle_seq": state["toggle_seq"], "dropped": state["dropped"]}, task_id)
        return {**hook.summary(state), "action": action, "applied": applied, "mode": "next_turn", "ended": True}

    def finish(self, task_id: str, session_id: str | None) -> dict[str, Any] | None:
        """Close the task's file. Undelivered messages and a pause go back to the session carry first."""
        live = self.live.pop(task_id, None)
        if live is None:
            return None
        state = hook.read_state(live.path)
        session = session_id or live.session_id or (state or {}).get("session_id")
        if session and state:
            self._close(task_id, live.agent_id, str(session), state)
        self._remove(live.path)
        self._prune(time.time())
        return state

    def halted(self, task_id: str, result: TaskResult) -> TaskResult:
        """The halted result when the PI's halt is what ended the run: the hook stopped Claude, or the runner ended
        the CLI. A run that finished on its own before either keeps its result."""
        live = self.live.get(task_id)
        if live is None or not live.halt.is_set():
            return result
        state = hook.read_state(live.path)
        if result.error_kind == "halted" or (state and state.get("halted_at")):
            return halted_result(result, live.session_id)
        return result
