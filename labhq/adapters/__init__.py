from __future__ import annotations

from ..models import Engine
from ..settings import Settings
from .base import AgentAdapter, RunContext
from .antigravity import AntigravityAdapter
from .claude_code import ClaudeCodeAdapter
from .cli import CliAdapter
from .codex import CodexAdapter
from .gemini import GeminiAdapter
from .mock import MockAdapter

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


# Consults and follow-ups answer from existing work; they never write or submit. MCP servers run outside the
# engine's sandbox and plan mode, so a read-only task gets none of them, labhq's or the agent's own (#36).
READ_ONLY_OVERRIDES = {"sandbox": "read-only", "permission_mode": "plan", "builtin_mcp": [], "mcp": [],
                       "builtin_tools": "Read,Glob,Grep", "tools": []}


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


__all__ = ["get_adapter", "enforces_read_only", "read_only_refusal", "READ_ONLY_OVERRIDES", "AgentAdapter",
           "RunContext"]
