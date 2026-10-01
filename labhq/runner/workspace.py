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
import os
import platform
import shutil
import time
import uuid
from pathlib import Path, PurePath
from typing import Any

from .. import __version__
from ..adapters.held_dir import HeldDir, NotPlainFolder
from ..adapters.owned import OwnedPathError, append_owned, plain_directory, read_owned, write_owned
from ..adapters.owned import is_link as _is_link
from ..adapters.owned import remove_entry as _remove_entry
from ..adapters.read_only import SKILL_DIRS
from ..intake import overlaps_zone
from ..models import AgentSpec, Task

INLINE_LIMIT = 48_000  # longer prompts are passed by reference to TASK.md (argv limits, cost)
# A direct run lists at most this many files: the shadow hashes no more per request (HASH_MAX_FILES, #221).
OUTPUT_SCAN_MAX_FILES = 200


def _utf8_name(name: str) -> bool:
    """False for a name the OS decoded with surrogates (non-UTF-8 bytes on POSIX, an unpaired surrogate on Windows)."""
    try:
        name.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _is_mount(path: Path) -> bool:
    """Another file system mounted here. On Windows a mount point is a reparse point, which ``_is_link`` sees."""
    return os.name != "nt" and os.path.ismount(path)


log = logging.getLogger("labhq.runner")


class TaskWorkspace:
    def __init__(self, root: Path, task: Task, agent: AgentSpec, override: Path | None = None):
        self.dir = Path(override) if override else Path(root) / time.strftime("%Y-%m-%d") / f"{task.id}_{agent.id}"
        self.dir.mkdir(parents=True, exist_ok=True)
        for sub in ("outputs", "jobs/logs", ".labhq"):  # never through a link an earlier run left (#165)
            if plain_directory(self.dir, sub) is None:
                raise OwnedPathError(f"labhq does not write in {sub}: it is a link or not a folder in the workspace")
        self.task, self.agent = task, agent
        self.prompt_pointer: str | None = None
        self._append_refused = False

    def write_task_md(self) -> str:
        t, a = self.task, self.agent
        body = f"# Task {t.id}\n\nAssigned to: {a.name} ({a.id}) — {a.role}\n\n## Instruction\n{t.prompt}\n"
        if t.context:
            body += f"\n## Context from teammates\n{t.context}\n"
        name = (f"TASK_wake_{t.id}.md" if t.resume_session_id else
                f"TASK_{t.id}.md" if (self.dir / "TASK.md").exists() else "TASK.md")
        write_owned(self.dir, name, body)
        # The adapter falls back to this when the inline prompt would overflow the command line (#222).
        self.prompt_pointer = (f"Read {name} in the current directory (it is long) and carry out the instruction "
                               f"there.\n\nInstruction summary: {t.prompt[:2000]}")
        return body if len(body) <= INLINE_LIMIT else self.prompt_pointer

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

    def _manifest(self) -> dict[str, Any]:
        """The manifest as labhq wrote it. A link in its place is never read: its target would be copied into the
        workspace by the next write (#165); a fresh manifest replaces the link instead."""
        text = read_owned(self.dir, "manifest.json")
        return json.loads(text) if text is not None else {
            "labhq_version": __version__, "host": platform.node(),
            "task": self.task.model_dump(mode="json", exclude={"context"}),
            "agent_id": self.agent.id, "engine": self.agent.engine.value, "model": self.agent.model,
            "agent_spec_sha256": hashlib.sha256(self.agent.model_dump_json().encode()).hexdigest(),
        }

    def write_manifest(self, **fields: Any) -> None:
        data = self._manifest()
        data.update(fields)
        write_owned(self.dir, "manifest.json", json.dumps(data, indent=2, ensure_ascii=False, default=str))

    def provenance(self) -> dict[str, Any]:
        """The manifest fields round records need, sent with the result instead of read from this disk."""
        try:
            data = json.loads(read_owned(self.dir, "manifest.json") or "")
        except ValueError:
            return {}
        if not isinstance(data, dict):
            return {}
        keep = ("started_at", "ended_at", "engine", "model", "model_id", "engine_cli_version", "plugins", "turns")
        runs = {tid: {k: run[k] for k in keep if k in run}
                for tid, run in (data.get("runs") or {}).items() if isinstance(run, dict)}
        return {"engine": data.get("engine"), "model": data.get("model"), "runs": runs}

    def scan_outputs(self, zones: list[Path], max_entries: int, max_depth: int,
                     max_files: int | None = None) -> tuple[list[str], str | None]:
        """Files a direct run left under outputs/, as `outputs/...`, and why the list is incomplete, if it is (#221).

        A direct run declares no outputs, so its folder is listed instead, by the rules a declared output and
        the shadow's hash already follow: nothing reached through a symlink, junction or mount, regular files
        only, nothing in a restricted zone, at most ``max_files`` files within the reference scan caps.
        labhq's own RESULT copies are not the agent's output.

        Every folder is listed through a handle opened without following a link, and each subfolder is opened
        relative to its parent's handle (#230): a process the agent left running may swap a folder for a link after
        it was checked, and opening its path a second time would follow that link.
        """
        root = self.dir / "outputs"
        if not os.path.lexists(root):
            return [], None
        try:
            if _is_link(root) or not root.is_dir():
                return [], None
        except OSError:
            return [], "outputs 폴더를 확인할 수 없어 산출 목록을 만들지 않았습니다"
        try:
            top = HeldDir.hold(root)
        except OSError:  # gone, or swapped for a link or a file since the check
            return [], "outputs 폴더를 확인할 수 없어 산출 목록을 만들지 않았습니다"
        with top:
            try:
                real_root = root.resolve()
                if not top.same_as(real_root):  # its real path must name the folder held open
                    return [], "outputs 폴더를 확인할 수 없어 산출 목록을 만들지 않았습니다"
                # Its files would live on another file system or in a zone; a zone inside it is left out per entry.
                if (_is_mount(root) or (top.dev is not None and top.dev != os.stat(real_root.parent).st_dev)
                        or any(real_root.is_relative_to(zone) for zone in zones)):
                    return [], "outputs 폴더가 다른 파일 시스템이거나 통제 구역 안이라 산출 목록을 만들지 않았습니다"
            except (OSError, RuntimeError, ValueError):
                return [], "outputs 폴더를 확인할 수 없어 산출 목록을 만들지 않았습니다"
            return self._list_outputs(top, root, real_root, zones, max_entries, max_depth,
                                      OUTPUT_SCAN_MAX_FILES if max_files is None else max_files)

    def _list_outputs(self, top: HeldDir, root: Path, real_root: Path, zones: list[Path], max_entries: int,
                      max_depth: int, max_files: int) -> tuple[list[str], str | None]:
        try:  # never through a link or FIFO the agent put in its place (#165): no runs read then
            text = read_owned(self.dir, "manifest.json")
            runs = json.loads(text).get("runs") if text is not None else None
        except (ValueError, AttributeError):
            runs = None
        runs = runs if isinstance(runs, dict) else {}
        own = {"RESULT.md", f"RESULT_{self.task.id}.md", *(f"RESULT_{tid}.md" for tid in runs)}
        found: list[str] = []
        note: str | None = None  # a folder left out; the listing goes on without it
        seen = 0

        def walk(folder: HeldDir, relative: PurePath, depth: int) -> str | None:
            """List one held folder, then its subfolders in name order; the reason the listing stops, if it does."""
            nonlocal note, seen
            shown = PurePath("outputs", relative).as_posix()
            try:
                entries = sorted(folder.entries(max_entries - seen + 1), key=lambda e: e.name)
            except OSError:
                note = note or f"{shown} 폴더를 읽을 수 없어 산출 목록이 불완전합니다"
                return None
            folders = []
            for entry in entries:
                seen += 1
                if seen > max_entries:
                    return f"outputs 아래 항목이 상한 {max_entries}개를 넘어 산출 목록이 불완전합니다"
                if not _utf8_name(entry.name):
                    # A CP949 name unpacked on Linux: the result could not be sent as JSON text and the run would fail.
                    note = note or "UTF-8로 읽을 수 없는 이름의 파일·폴더는 산출 목록에서 뺐습니다"
                    continue
                if entry.kind in (None, "link"):
                    continue  # gone, or a link or junction leading elsewhere: not this run's output
                if overlaps_zone(real_root / relative / entry.name, zones):
                    continue  # inside a restricted zone, or a folder holding one
                if entry.kind == "dir":
                    if _is_mount(root / relative / entry.name) or (
                            entry.ident is not None and entry.ident[0] != top.dev):
                        continue
                    if depth + 1 > max_depth:
                        note = note or f"outputs 폴더 깊이가 상한 {max_depth}단계를 넘어 산출 목록이 불완전합니다"
                        continue
                    folders.append(entry)
                elif entry.kind == "file" and not (depth == 0 and entry.name in own):
                    if len(found) >= max_files:
                        return f"산출 파일이 상한 {max_files}개를 넘어 앞의 {max_files}개만 기록합니다"
                    found.append(PurePath("outputs", relative, entry.name).as_posix())
            for entry in folders:  # a folder's files first, then its subfolders in name order
                sub = relative / entry.name
                try:
                    child = folder.child(entry.name, expect=entry.ident)
                except NotPlainFolder:
                    note = note or (f"{PurePath('outputs', sub).as_posix()} 폴더가 목록을 만드는 사이 링크나 다른 "
                                    "폴더로 바뀌어 산출 목록에서 뺐습니다")
                    continue
                except OSError:
                    note = note or f"{PurePath('outputs', sub).as_posix()} 폴더를 읽을 수 없어 산출 목록이 불완전합니다"
                    continue
                with child:
                    stop = walk(child, sub, depth + 1)
                if stop:
                    return stop
            return None

        stop = walk(top, PurePath(), 0)
        return found, stop or note

    def update_run(self, task_id: str, **fields: Any) -> None:
        """A workspace can host several runs (original + wake-ups after HPC jobs)."""
        data = self._manifest()
        data.setdefault("runs", {}).setdefault(task_id, {}).update(fields)
        write_owned(self.dir, "manifest.json", json.dumps(data, indent=2, ensure_ascii=False, default=str))
