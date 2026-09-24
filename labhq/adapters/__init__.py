from __future__ import annotations

from ..models import Engine
from ..settings import Settings
from .base import AgentAdapter, RunContext
from .claude_code import ClaudeCodeAdapter
from .cli import CliAdapter
from .codex import CodexAdapter
from .gemini import GeminiAdapter
from .mock import MockAdapter

_ADAPTERS: dict[Engine, type[AgentAdapter]] = {
    Engine.claude_code: ClaudeCodeAdapter,
    Engine.codex: CodexAdapter,
    Engine.gemini: GeminiAdapter,
    Engine.cli: CliAdapter,
    Engine.mock: MockAdapter,
}


def get_adapter(engine: Engine, settings: Settings) -> AgentAdapter:
    return _ADAPTERS[Engine(engine)](settings)


__all__ = ["get_adapter", "AgentAdapter", "RunContext"]
