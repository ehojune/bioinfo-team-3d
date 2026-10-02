"""Fake Claude/Codex CLI that makes one real stdio MCP call with the CLI's own timeout rules (#276).

Usage (as engines.<engine>.prefix_args): fake_mcp_client.py ENGINE SERVER_NAME, then the adapter's argv.
It reads the MCP config labhq generated, starts that server, calls `slow_answer` once and prints the engine's
event stream. A call that outlives the CLI's timeout comes back as that engine's tool failure.

Timeout rules copied from the real CLIs:
- Claude Code 2.1.282: the server's `timeout` (ms) when >= 1000, else MCP_TOOL_TIMEOUT (ms), else 1e8 ms. The
  stdio idle limit (30 min unless the per-server timeout is longer) is far above the delays used here.
- codex-cli 0.159.2: `mcp_servers.<name>.tool_timeout_sec`, default 60 s.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from pathlib import Path

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

CLAUDE_DEFAULT_MS = 100_000_000
CODEX_DEFAULT_S = 60.0
TOML_PAIR = re.compile(r'([A-Za-z_][A-Za-z0-9_]*) = ("(?:[^"\\]|\\.)*")')


def _claude(argv: list[str], name: str) -> tuple[str, list[str], dict[str, str], float]:
    path = Path(argv[argv.index("--mcp-config") + 1])
    server = json.loads(path.read_text(encoding="utf-8"))["mcpServers"][name]
    timeout_ms = server.get("timeout")
    if not isinstance(timeout_ms, (int, float)) or timeout_ms < 1000:
        timeout_ms = float(os.environ.get("MCP_TOOL_TIMEOUT") or CLAUDE_DEFAULT_MS)
    return server["command"], list(server.get("args") or []), dict(server.get("env") or {}), timeout_ms / 1000


def _codex(argv: list[str], name: str) -> tuple[str, list[str], dict[str, str], float]:
    prefix = f"mcp_servers.{name}."
    values = {}
    for i, item in enumerate(argv[:-1]):
        if item == "-c" and argv[i + 1].startswith(prefix):
            key, value = argv[i + 1][len(prefix):].split("=", 1)
            values[key] = value
    env = {key: json.loads(value) for key, value in TOML_PAIR.findall(values.get("env", ""))}
    timeout_s = float(values.get("tool_timeout_sec", CODEX_DEFAULT_S))
    return json.loads(values["command"]), json.loads(values["args"]), env, timeout_s


async def _call(command: str, args: list[str], env: dict[str, str], timeout_s: float) -> str | None:
    params = StdioServerParameters(command=command, args=args, env={**os.environ, **env})
    async with stdio_client(params) as streams:
        async with ClientSession(streams[0], streams[1]) as session:
            await session.initialize()
            try:
                result = await asyncio.wait_for(session.call_tool("slow_answer", {}), timeout=timeout_s)
            except asyncio.TimeoutError:
                return None
    return result.content[0].text


def _claude_events(name: str, text: str | None) -> list[dict]:
    error = text is None
    out = "MCP error -32001: Request timed out" if error else text
    return [
        {"type": "system", "subtype": "init", "session_id": "delayed-claude", "model": "fake",
         "mcp_servers": [{"name": name, "status": "connected"}]},
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "toolu_1",
                                                       "name": f"mcp__{name}__slow_answer", "input": {}}]}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "toolu_1",
                                                  "content": out, "is_error": error}]}},
        {"type": "result", "subtype": "success", "is_error": False, "result": out, "session_id": "delayed-claude"},
    ]


def _codex_events(name: str, text: str | None) -> list[dict]:
    started = {"id": "item_1", "type": "mcp_tool_call", "server": name, "tool": "slow_answer", "arguments": {},
               "result": None, "error": None, "status": "in_progress"}
    if text is None:
        done = {**started, "error": {"message": "request timed out"}, "status": "failed"}
        final = "request timed out"
    else:
        done = {**started, "result": {"content": [{"type": "text", "text": text}]}, "status": "completed"}
        final = text
    return [
        {"type": "thread.started", "thread_id": "delayed-codex"},
        {"type": "item.started", "item": started},
        {"type": "item.completed", "item": done},
        {"type": "item.completed", "item": {"id": "item_2", "type": "agent_message", "text": final}},
        {"type": "turn.completed", "usage": {"input_tokens": 1, "output_tokens": 1}},
    ]


def main() -> None:
    engine, name, argv = sys.argv[1], sys.argv[2], sys.argv[3:]
    command, args, env, timeout_s = (_claude if engine == "claude_code" else _codex)(argv, name)
    text = asyncio.run(_call(command, args, env, timeout_s))
    if engine == "claude_code":
        events = _claude_events(name, text)
    else:
        events = _codex_events(name, text)
        Path(argv[argv.index("-o") + 1]).write_text(events[-2]["item"]["text"], encoding="utf-8")
    for event in events:
        print(json.dumps(event), flush=True)


if __name__ == "__main__":
    main()
