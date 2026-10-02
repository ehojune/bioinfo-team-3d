"""Local stdio MCP server for the long-call timeout test and probe (#276). It contacts nothing.

`slow_answer` sleeps LABHQ_PROBE_DELAY_S seconds and returns a fixed marker. When LABHQ_PROBE_CALLS names a file,
each call appends one line there, so a test can count calls the server actually received.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from labhq.tools._mcpcompat import make_server  # noqa: E402

MARKER = "LABHQ_DELAY_OK"
server = make_server("labhq-delayed", instructions="slow_answer returns one marker after a fixed delay.")


@server.tool()
async def slow_answer() -> str:
    """Wait the configured delay, then return a fixed marker."""
    delay_s = float(os.environ["LABHQ_PROBE_DELAY_S"])
    if not 0 <= delay_s <= 600:
        raise ValueError("LABHQ_PROBE_DELAY_S must be between 0 and 600")
    calls = os.environ.get("LABHQ_PROBE_CALLS")
    if calls:
        with open(calls, "a", encoding="utf-8") as handle:
            handle.write(f"{time.time():.3f}\n")
    await asyncio.sleep(delay_s)
    return MARKER


if __name__ == "__main__":
    server.run()
