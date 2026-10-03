from __future__ import annotations

from pathlib import Path

from ..models import AgentSpec, Engine, Task
from ..settings import Settings
from .base import AgentAdapter, RunContext
from .antigravity import AntigravityAdapter
from .claude_code import ClaudeCodeAdapter
from .cli import CliAdapter
from .codex import CodexAdapter
from .gemini import GeminiAdapter
from .mock import MockAdapter
from .read_only import READ_ONLY_OVERRIDES, is_read_only_task, read_only_profile

_ADAPTERS: dict[Engine, type[AgentAdapter]] = {
    Engine.claude_code: ClaudeCodeAdapter,
    Engine.codex: CodexAdapter,
    Engine.gemini: GeminiAdapter,
    Engine.antigravity: AntigravityAdapter,
    Engine.cli: CliAdapter,
    Engine.mock: MockAdapter,
}


def get_adapter(engine: Engine, settings: Settings) -> AgentAdapter:
    return _ADAPTERS[Engine(engine)](settings)


def adapter_preflight_error(settings: Settings, agent: AgentSpec, workdir: Path | None = None) -> str | None:
    """The adapter refusal used by doctor, the runner roster and the actual staff launch."""
    ctx = RunContext(task=Task(agent_id=agent.id, prompt=""), agent=agent,
                     workdir=Path(workdir or settings.path(settings.runner.workspace_root)), settings=settings,
                     mcp_servers=[], env={}, emit=lambda *_: None, prompt="")
    adapter = get_adapter(agent.engine, settings)
    return adapter.preflight_error(ctx, adapter.staff_env(ctx))


def enforces_read_only(engine: Engine | str | None) -> bool:
    """Whether a read-only task (consult, follow-up) stays read-only on this engine. The gateway, the
    orchestrator and the runner all decide with this, so no path trusts a prompt to keep an agent from writing."""
    try:
        return _ADAPTERS[Engine(engine)].enforces_read_only
    except ValueError:
        return False


def read_only_refusal(agent_id: str, engine: Engine | str | None) -> str | None:
    """None when `engine` can run a read-only task; otherwise the reason shown to the PI or the asking agent."""
    if enforces_read_only(engine):
        return None
    name = getattr(engine, "value", engine) or "unknown"
    return (f"읽기 전용 정책: {agent_id}의 엔진({name})은 sandbox·도구 제한을 적용하지 않아 읽기 전용을 강제할 수 "
            "없습니다. 이어 묻기·상담은 claude_code·codex 직원에게 하세요 (read-only policy)")


__all__ = ["get_adapter", "adapter_preflight_error", "enforces_read_only", "read_only_refusal",
           "read_only_profile", "is_read_only_task", "READ_ONLY_OVERRIDES", "AgentAdapter", "RunContext"]
