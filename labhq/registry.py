"""정규직(agents/core) + 파견직(agents/contract) + 인재풀(talent_dir).

A contract agent's YAML lives in agents/contract while employed. On expiry or release it moves
to talent_dir/<slug>/contract.yaml (with its skill + MCP build), so rehiring is instant.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Callable, Iterator

import yaml

from .models import AgentSpec, Employment
from . import durable
from .util import atomic_write_text

log = logging.getLogger(__name__)


def _dump(spec: AgentSpec) -> str:
    return yaml.safe_dump(spec.model_dump(mode="json", exclude_none=True), allow_unicode=True, sort_keys=False)


def iter_agent_specs(agents_dir: Path, contract_dir: Path | None = None, *,
                     check_path: Callable[[Path], object] | None = None) -> Iterator[tuple[AgentSpec, Path]]:
    """Read definitions in runner precedence order; never change contract files."""
    for folder in (agents_dir / "core", contract_dir if contract_dir else agents_dir / "contract"):
        if check_path:
            check_path(folder)
        for path in sorted(folder.glob("*.yaml")):
            if path.name.startswith("_"):
                continue
            if check_path:
                check_path(path)
            yield AgentSpec.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")) or {}), path


def is_employed(spec: AgentSpec, now: float) -> bool:
    """Core staff stay active; contract staff need an unexpired, non-archived contract."""
    if spec.employment != Employment.contract:
        return True
    c = spec.contract
    return c is not None and c.status not in ("expired", "archived") and not now > c.expires_at


class Registry:
    def __init__(self, agents_dir: Path, talent_dir: Path, contract_dir: Path | None = None):
        self.agents_dir = Path(agents_dir)
        self.talent_dir = Path(talent_dir)
        # Hiring, renewal and release write here; instances keep their own so one lab's contracts don't leak into another's.
        self.contract_dir = Path(contract_dir) if contract_dir else self.agents_dir / "contract"
        self.agents: dict[str, AgentSpec] = {}
        self.paths: dict[str, Path] = {}
        self._rehire_lock = asyncio.Lock()
        self._sync_publications: dict[str, object] = {}

    def load(self) -> None:
        # Read and expiry/archive belong to the same FILES job. Otherwise a reload can read an expired copy,
        # wait behind an in-flight rehire, then archive its newly committed files using the old snapshot.
        staged = Registry(self.agents_dir, self.talent_dir, self.contract_dir)
        durable.call(durable.FILES, staged._load_now)
        # Keep live dictionaries on the caller's thread, matching the synchronous API's publication contract.
        self.agents.clear()
        self.agents.update(staged.agents)
        self.paths.clear()
        self.paths.update(staged.paths)
        self._sync_publications = {**self._sync_publications, **staged._sync_publications}

    def _load_now(self) -> None:
        self.agents.clear()
        self.paths.clear()
        for spec, p in iter_agent_specs(self.agents_dir, self.contract_dir):
            if spec.employment == Employment.contract and not self._still_employed(spec, p):
                continue
            self.agents[spec.id] = spec
            self.paths[spec.id] = p

    def _still_employed(self, spec: AgentSpec, path: Path) -> bool:
        now = time.time()
        if is_employed(spec, now):
            return True
        c = spec.contract
        if c is None or c.status in ("expired", "archived"):
            return False
        if now > c.expires_at:
            self._archive(spec, path, "expired")
            return False
        return True

    def _slug(self, spec: AgentSpec) -> str:
        if spec.contract and spec.contract.talent_dir:
            return Path(spec.contract.talent_dir).name
        return spec.id.removeprefix("c_")

    def _archive(self, spec: AgentSpec, path: Path | None, reason: str) -> None:
        assert spec.contract
        spec.contract.status = "archived"
        spec.contract.verification = {**spec.contract.verification, "archived_reason": reason,
                                      "archived_at": time.time()}
        dst = self.talent_dir / self._slug(spec)
        dst.mkdir(parents=True, exist_ok=True)
        atomic_write_text(dst / "contract.yaml", _dump(spec))
        if path and path.exists():
            path.unlink()
        self.agents.pop(spec.id, None)
        self.paths.pop(spec.id, None)
        self._sync_publications = {**self._sync_publications, spec.id: object()}

    # ----- queries -----
    def get(self, agent_id: str) -> AgentSpec:
        if agent_id not in self.agents:
            raise KeyError(f"unknown agent: {agent_id}")
        return self.agents[agent_id]

    def roster(self) -> list[dict]:
        return [s.summary() for s in self.agents.values()]

    def talent_pool(self) -> list[AgentSpec]:
        out = []
        for p in sorted(self.talent_dir.glob("*/contract.yaml")):
            out.append(AgentSpec.model_validate(yaml.safe_load(p.read_text(encoding="utf-8"))))
        return out

    # ----- contract lifecycle -----
    async def asave_contract(self, spec: AgentSpec) -> Path:
        """`save_contract` for a coroutine (#606): both copies, then the roster update, are ONE durable job, so a
        cancel cannot leave one copy without the other or files without the roster. The files are written on the
        writer thread; the roster dicts change on the loop, in a callback the job itself schedules before it ends."""
        assert spec.employment == Employment.contract and spec.contract
        text, loop = _dump(spec), asyncio.get_running_loop()

        def job() -> Path:
            p = self._write_contract_copies(spec, text)
            loop.call_soon_threadsafe(self._remember, spec, p)
            return p

        return await durable.run(durable.FILES, job)

    def _write_contract_copies(self, spec: AgentSpec, text: str) -> Path:
        p = self.contract_dir / f"{spec.id}.yaml"
        p.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(p, text)
        tdir = self.talent_dir / self._slug(spec)
        tdir.mkdir(parents=True, exist_ok=True)
        atomic_write_text(tdir / "contract.yaml", text)
        return p

    def _remember(self, spec: AgentSpec, path: Path) -> None:
        self.agents[spec.id] = spec
        self.paths[spec.id] = path

    def save_contract(self, spec: AgentSpec) -> Path:
        assert spec.employment == Employment.contract and spec.contract
        p = self._write_contract_copies(spec, _dump(spec))
        self._remember(spec, p)
        # Replace the map so an isolated copy.copy Registry cannot mark its live parent's sync publication.
        self._sync_publications = {**self._sync_publications, spec.id: object()}
        return p

    def extend(self, agent_id: str, days: float) -> AgentSpec:
        spec = self.get(agent_id)
        assert spec.contract, "only contract agents can be extended"
        spec.contract.expires_at = max(spec.contract.expires_at, time.time()) + days * 86400
        self.save_contract(spec)
        return spec

    def activate(self, agent_id: str) -> AgentSpec:
        spec = self.get(agent_id)
        assert spec.contract
        spec.contract.status = "active"
        self.save_contract(spec)
        return spec

    def release(self, agent_id: str) -> None:
        spec = self.get(agent_id)
        self._archive(spec, self.paths.get(agent_id), "released")

    async def arehire(self, slug: str, days: float, *, on_commit: Callable[[], None] | None = None,
                     validate: Callable[[AgentSpec], None] | None = None) -> AgentSpec:
        """Read, verify and save on FILES; publish on the loop before announcing a rehire.

        Once submitted, settle both the write and publication despite repeated cancellation.
        A storage/publication error takes precedence over cancellation. Sync callers keep `rehire`.
        """
        async with self._rehire_lock:
            # Flush earlier writes. Their loop callbacks may still be queued when this waiter resumes.
            await durable.run(durable.FILES, lambda: None)
            previous = self._sync_publications
            staged = Registry(self.agents_dir, self.talent_dir, self.contract_dir)
            loop = asyncio.get_running_loop()
            published = loop.create_future()

            def publish(spec: AgentSpec) -> None:
                try:
                    # A later sync rehire can finish on the loop before this queued callback runs (#977).
                    # Earlier async callbacks are FIFO predecessors, not newer synchronous publications.
                    if self._sync_publications.get(spec.id) is previous.get(spec.id):
                        self._remember(spec, staged.paths[spec.id])
                    if on_commit is not None:
                        on_commit()
                except BaseException as exc:
                    published.set_exception(exc)
                else:
                    published.set_result(spec)

            def job() -> None:
                spec = staged.rehire(slug, days, validate=validate)
                loop.call_soon_threadsafe(publish, spec)

            write = asyncio.wrap_future(durable.submit(durable.FILES, job))

            async def commit() -> AgentSpec:
                await write
                return await published

            pending = asyncio.create_task(commit())
            cancelled = None
            while not pending.done():
                try:
                    await asyncio.shield(pending)
                except asyncio.CancelledError as exc:
                    cancelled = exc
            spec = pending.result()
            if cancelled is not None:
                raise cancelled
            return spec

    def rehire(self, slug: str, days: float, *, validate: Callable[[AgentSpec], None] | None = None) -> AgentSpec:
        p = self.talent_dir / slug / "contract.yaml"
        spec = AgentSpec.model_validate(yaml.safe_load(p.read_text(encoding="utf-8")))
        assert spec.contract
        if validate is not None:
            validate(spec)
        from .recruit.provenance import provenance_changes

        # The skill and MCP folders are hashed at hiring (#424): a rehire says which ones changed since, and the
        # roster card shows it until a later rehire finds them back as hired.
        changed = provenance_changes(spec)
        verification = {k: v for k, v in spec.contract.verification.items() if k != "rehire_changed"}
        if changed:
            verification["rehire_changed"] = changed
            log.warning("rehire %s: %s changed since hiring (sha256 differs)", spec.id, ", ".join(changed))
        now = time.time()
        spec.contract.verification = verification
        spec.contract.status = "active"
        spec.contract.hired_at = now
        spec.contract.expires_at = now + days * 86400
        self.save_contract(spec)
        return spec
