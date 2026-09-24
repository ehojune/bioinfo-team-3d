"""정규직(agents/core) + 파견직(agents/contract) + 인재풀(talent_dir).

A contract agent's YAML lives in agents/contract while employed. On expiry or release it moves
to talent_dir/<slug>/contract.yaml (with its skill + MCP build), so rehiring is instant.
"""

from __future__ import annotations

import time
from pathlib import Path

import yaml

from .models import AgentSpec, Employment


def _dump(spec: AgentSpec) -> str:
    return yaml.safe_dump(spec.model_dump(mode="json", exclude_none=True), allow_unicode=True, sort_keys=False)


class Registry:
    def __init__(self, agents_dir: Path, talent_dir: Path):
        self.agents_dir = Path(agents_dir)
        self.talent_dir = Path(talent_dir)
        self.agents: dict[str, AgentSpec] = {}
        self.paths: dict[str, Path] = {}

    def load(self) -> None:
        self.agents.clear()
        self.paths.clear()
        for sub in ("core", "contract"):
            for p in sorted((self.agents_dir / sub).glob("*.yaml")):
                if p.name.startswith("_"):
                    continue
                spec = AgentSpec.model_validate(yaml.safe_load(p.read_text()) or {})
                if spec.employment == Employment.contract and not self._still_employed(spec, p):
                    continue
                self.agents[spec.id] = spec
                self.paths[spec.id] = p

    def _still_employed(self, spec: AgentSpec, path: Path) -> bool:
        c = spec.contract
        if c is None or c.status in ("expired", "archived"):
            return False
        if time.time() > c.expires_at:
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
        (dst / "contract.yaml").write_text(_dump(spec))
        if path and path.exists():
            path.unlink()
        self.agents.pop(spec.id, None)
        self.paths.pop(spec.id, None)

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
            out.append(AgentSpec.model_validate(yaml.safe_load(p.read_text())))
        return out

    # ----- contract lifecycle -----
    def save_contract(self, spec: AgentSpec) -> Path:
        assert spec.employment == Employment.contract and spec.contract
        p = self.agents_dir / "contract" / f"{spec.id}.yaml"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(_dump(spec))
        tdir = self.talent_dir / self._slug(spec)
        tdir.mkdir(parents=True, exist_ok=True)
        (tdir / "contract.yaml").write_text(_dump(spec))
        self.agents[spec.id] = spec
        self.paths[spec.id] = p
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

    def rehire(self, slug: str, days: float) -> AgentSpec:
        p = self.talent_dir / slug / "contract.yaml"
        spec = AgentSpec.model_validate(yaml.safe_load(p.read_text()))
        assert spec.contract
        now = time.time()
        spec.contract.status = "active"
        spec.contract.hired_at = now
        spec.contract.expires_at = now + days * 86400
        self.save_contract(spec)
        return spec
