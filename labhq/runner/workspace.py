"""One directory per task = the lab notebook page for that task.

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
import platform
import shutil
import time
from pathlib import Path
from typing import Any

from .. import __version__
from ..models import AgentSpec, Task

INLINE_LIMIT = 48_000  # longer prompts are passed by reference to TASK.md (argv limits, cost)


class TaskWorkspace:
    def __init__(self, root: Path, task: Task, agent: AgentSpec, override: Path | None = None):
        self.dir = Path(override) if override else Path(root) / time.strftime("%Y-%m-%d") / f"{task.id}_{agent.id}"
        for sub in ("outputs", "jobs/logs", ".labhq"):
            (self.dir / sub).mkdir(parents=True, exist_ok=True)
        self.task, self.agent = task, agent

    def write_task_md(self) -> str:
        t, a = self.task, self.agent
        body = f"# Task {t.id}\n\nAssigned to: {a.name} ({a.id}) — {a.role}\n\n## Instruction\n{t.prompt}\n"
        if t.context:
            body += f"\n## Context from teammates\n{t.context}\n"
        name = "TASK.md" if not t.resume_session_id else f"TASK_wake_{t.id}.md"
        (self.dir / name).write_text(body)
        if len(body) <= INLINE_LIMIT:
            return body
        return (f"Read {name} in the current directory (it is long) and carry out the instruction there.\n\n"
                f"Instruction summary: {t.prompt[:2000]}")

    def install_skill(self, skill_dir: Path) -> None:
        """Contract agents carry their paper skill; project-level skill dirs for Claude Code and Codex."""
        skill_dir = Path(skill_dir)
        if not (skill_dir / "SKILL.md").exists():
            return
        for base in (".claude/skills", ".agents/skills"):
            dst = self.dir / base / skill_dir.name
            if not dst.exists():
                shutil.copytree(skill_dir, dst)

    def append_event(self, ev: dict[str, Any]) -> None:
        with open(self.dir / "events.jsonl", "a") as f:
            f.write(json.dumps(ev, ensure_ascii=False, default=str) + "\n")

    def append_job(self, job: dict[str, Any]) -> None:
        with open(self.dir / "jobs.jsonl", "a") as f:
            f.write(json.dumps(job, ensure_ascii=False) + "\n")

    def write_manifest(self, **fields: Any) -> None:
        p = self.dir / "manifest.json"
        data = json.loads(p.read_text()) if p.exists() else {
            "labhq_version": __version__, "host": platform.node(),
            "task": self.task.model_dump(mode="json", exclude={"context"}),
            "agent_id": self.agent.id, "engine": self.agent.engine.value, "model": self.agent.model,
            "agent_spec_sha256": hashlib.sha256(self.agent.model_dump_json().encode()).hexdigest(),
        }
        data.update(fields)
        p.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str))

    def update_run(self, task_id: str, **fields: Any) -> None:
        """A workspace can host several runs (original + wake-ups after HPC jobs)."""
        p = self.dir / "manifest.json"
        if not p.exists():
            self.write_manifest()
        data = json.loads(p.read_text())
        data.setdefault("runs", {}).setdefault(task_id, {}).update(fields)
        p.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str))
