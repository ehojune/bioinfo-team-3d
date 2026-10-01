"""One directory per task, reused by retries and revisions as the step notebook.

<root>/<YYYY-MM-DD>/<task_id>_<agent_id>/
  TASK.md          instruction + teammates' context, exactly as sent
  manifest.json    provenance: agent spec hash, engine/model, timings, cost, session, exit
  events.jsonl     every event (audit log)
  jobs.jsonl       HPC jobs submitted from this task
  jobs/            generated job scripts + logs/
  outputs/         deliverables (+ RESULT.md)
"""

from __future__ import annotations

import hashlib
import json
import logging
import platform
import shutil
import time
import uuid
from pathlib import Path, PurePath
from typing import Any

from .. import __version__
from ..adapters.owned import OwnedPathError, append_owned, plain_directory, write_owned
from ..adapters.owned import remove_entry as _remove_entry
from ..adapters.read_only import SKILL_DIRS
from ..models import AgentSpec, Task

INLINE_LIMIT = 48_000  # longer prompts are passed by reference to TASK.md (argv limits, cost)
log = logging.getLogger("labhq.runner")


class TaskWorkspace:
    def __init__(self, root: Path, task: Task, agent: AgentSpec, override: Path | None = None):
        self.dir = Path(override) if override else Path(root) / time.strftime("%Y-%m-%d") / f"{task.id}_{agent.id}"
        self.dir.mkdir(parents=True, exist_ok=True)
        for sub in ("outputs", "jobs/logs", ".labhq"):  # never through a link an earlier run left (#165)
            if plain_directory(self.dir, sub) is None:
                raise OwnedPathError(f"labhq does not write in {sub}: it is a link or not a folder in the workspace")
        self.task, self.agent = task, agent
        self._append_refused = False

    def write_task_md(self) -> str:
        t, a = self.task, self.agent
        body = f"# Task {t.id}\n\nAssigned to: {a.name} ({a.id}) — {a.role}\n\n## Instruction\n{t.prompt}\n"
        if t.context:
            body += f"\n## Context from teammates\n{t.context}\n"
        name = (f"TASK_wake_{t.id}.md" if t.resume_session_id else
                f"TASK_{t.id}.md" if (self.dir / "TASK.md").exists() else "TASK.md")
        write_owned(self.dir, name, body)
        if len(body) <= INLINE_LIMIT:
            return body
        return (f"Read {name} in the current directory (it is long) and carry out the instruction there.\n\n"
                f"Instruction summary: {t.prompt[:2000]}")

    def install_skill(self, skill_dir: Path) -> str | None:
        """Install a fresh contract skill copy for this run, without traversing workspace links.

        A link at the copy itself is replaced. A link above it (`.agents`, `.claude/skills`) refuses the run and
        stays as it is (#190): replacing it would delete what an earlier step put there, and labhq cannot tell
        whether that link was the PI's.
        """
        skill_dir = Path(skill_dir)
        if not skill_dir.name:
            return "contract skill source has no directory name; execution refused"
        destinations = []
        for base in SKILL_DIRS:
            dst = self.dir / base / skill_dir.name
            parent = plain_directory(self.dir, PurePath(base))
            if parent is None:
                return (f"contract skill destination {base} is a link or not a directory in the workspace; "
                        "execution refused")
            destinations.append((parent, dst))
        if not (skill_dir / "SKILL.md").is_file():
            for _, dst in destinations:
                _remove_entry(dst)
            return "contract skill source is missing or unreadable; execution refused"
        for parent, dst in destinations:  # every run replaces every engine's copy from the source
            fresh = parent / f".{skill_dir.name}.labhq-{uuid.uuid4().hex}"
            try:
                shutil.copytree(skill_dir, fresh)
                _remove_entry(dst)
                fresh.replace(dst)
            finally:
                _remove_entry(fresh)
        return None

    def _append(self, name: str, line: str) -> None:
        try:
            append_owned(self.dir, name, line)
        except OwnedPathError:  # the workspace folder itself changed under the run; the gateway still gets it
            if not self._append_refused:
                self._append_refused = True
                log.warning("task %s: local %s not written, a folder on the way is a link", self.task.id, name)

    def append_event(self, ev: dict[str, Any]) -> None:
        self._append("events.jsonl", json.dumps(ev, ensure_ascii=False, default=str) + "\n")

    def append_job(self, job: dict[str, Any]) -> None:
        self._append("jobs.jsonl", json.dumps(job, ensure_ascii=False) + "\n")

    def write_manifest(self, **fields: Any) -> None:
        p = self.dir / "manifest.json"
        data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {
            "labhq_version": __version__, "host": platform.node(),
            "task": self.task.model_dump(mode="json", exclude={"context"}),
            "agent_id": self.agent.id, "engine": self.agent.engine.value, "model": self.agent.model,
            "agent_spec_sha256": hashlib.sha256(self.agent.model_dump_json().encode()).hexdigest(),
        }
        data.update(fields)
        write_owned(self.dir, "manifest.json", json.dumps(data, indent=2, ensure_ascii=False, default=str))

    def provenance(self) -> dict[str, Any]:
        """The manifest fields round records need, sent with the result instead of read from this disk."""
        try:
            data = json.loads((self.dir / "manifest.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        keep = ("started_at", "ended_at", "engine", "model", "model_id", "engine_cli_version", "plugins", "turns")
        runs = {tid: {k: run[k] for k in keep if k in run}
                for tid, run in (data.get("runs") or {}).items() if isinstance(run, dict)}
        return {"engine": data.get("engine"), "model": data.get("model"), "runs": runs}

    def update_run(self, task_id: str, **fields: Any) -> None:
        """A workspace can host several runs (original + wake-ups after HPC jobs)."""
        p = self.dir / "manifest.json"
        if not p.exists():
            self.write_manifest()
        data = json.loads(p.read_text(encoding="utf-8"))
        data.setdefault("runs", {}).setdefault(task_id, {}).update(fields)
        write_owned(self.dir, "manifest.json", json.dumps(data, indent=2, ensure_ascii=False, default=str))
