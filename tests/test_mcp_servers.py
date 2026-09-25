import json
import sys
from pathlib import Path

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from labhq.models import McpServerSpec
from labhq.tools._mcpcompat import list_tools

REPO = Path(__file__).resolve().parents[1]
ENV = {"PYTHONPATH": str(REPO), "LABHQ_BROKER_URL": "http://127.0.0.1:9", "LABHQ_BROKER_TOKEN": "x"}


async def test_builtin_servers_expose_tools(tmp_path):
    env = {**ENV, "LABHQ_WORKDIR": str(tmp_path)}
    hpc = await list_tools(McpServerSpec(name="hpc", command=sys.executable, args=["-m", "labhq.tools.hpc_mcp"], env=env))
    assert {"hpc_submit", "hpc_status", "hpc_queue", "hpc_cancel"} <= set(hpc)
    appr = await list_tools(McpServerSpec(name="a", command=sys.executable, args=["-m", "labhq.tools.approval_mcp"], env=env))
    assert appr == ["approval_prompt"]


async def test_approval_prompt_contract(tmp_path):
    import os

    params = StdioServerParameters(command=sys.executable, args=["-m", "labhq.tools.approval_mcp"],
                                   env={**os.environ, **ENV, "LABHQ_WORKDIR": str(tmp_path)})
    async with stdio_client(params) as streams:
        async with ClientSession(streams[0], streams[1]) as session:
            await session.initialize()
            ok = await session.call_tool("approval_prompt", {"tool_name": "Read", "input": {"file_path": "README.md"}})
            # Claude Code rejects anything but a single text block (P1 real-CLI finding)
            assert len(ok.content) == 1 and ok.content[0].type == "text"
            assert (getattr(ok, "structured_content", None) or getattr(ok, "structuredContent", None)) is None
            payload = json.loads(ok.content[0].text)
            assert payload == {"behavior": "allow", "updatedInput": {"file_path": "README.md"}}
            # "ask" with an unreachable broker must fail closed
            risky = await session.call_tool("approval_prompt", {"tool_name": "Bash", "input": {"command": "rm -rf /tmp/x"}})
            assert json.loads(risky.content[0].text)["behavior"] == "deny"
