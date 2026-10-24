"""labhq-job MCP server (stdio): long computations as local jobs the runner owns (#675). Launched per task by the runner.

Env (set by the runner): LABHQ_BROKER_URL, LABHQ_BROKER_TOKEN, LABHQ_TASK_ID, LABHQ_AGENT_ID, LABHQ_CONFIG.
The runner judges the command with the approval gate's rules, starts it, and ends it when the step or request is
cancelled. The agent submits, summarizes what it waits for, and ends its turn: it is resumed when the job finishes.
"""
from __future__ import annotations

import json
import os

import httpx

from ..settings import Settings
from ._mcpcompat import make_server, tool_failure

S = Settings.load(os.environ.get("LABHQ_CONFIG"))
BROKER = os.environ.get("LABHQ_BROKER_URL", f"http://127.0.0.1:{S.runner.broker_port}")
TOKEN = os.environ.get("LABHQ_BROKER_TOKEN", "")
TASK = os.environ.get("LABHQ_TASK_ID")
AGENT = os.environ.get("LABHQ_AGENT_ID")

MAYBE_STARTED = "; the job may have been started, check job_list before submitting it again"

server = make_server(
    "labhq-job",
    instructions=(
        "Run a computation that takes more than about 10 minutes as a labhq job on this PC. Never start it in the "
        "background yourself (nohup, a trailing &, setsid, Start-Process): labhq cannot stop such a process and the "
        "command is refused. After job_submit do not poll in a loop: summarize what you are waiting for and end your "
        "turn. You are resumed when the job ends. job_status shows the state and the end of its logs."
    ),
)


async def _broker(op: str, payload: dict, timeout: float = 120) -> dict:
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(f"{BROKER}/localjobs/{op}", json={"task_id": TASK, "agent_id": AGENT, **payload},
                                         headers={"X-Labhq-Token": TOKEN})
            response.raise_for_status()
            return response.json()
    except (httpx.ConnectError, ConnectionError) as exc:  # fail closed: nothing was started
        raise tool_failure(f"labhq broker unreachable; nothing was done: {exc}") from exc
    except httpx.HTTPStatusError as exc:
        raise tool_failure(f"labhq broker refused: HTTP {exc.response.status_code}"
                           f"{MAYBE_STARTED if op == 'submit' else ''}") from exc
    except httpx.HTTPError as exc:  # the runner may have started the job before the reply was lost
        raise tool_failure(f"labhq broker did not answer cleanly: {exc!r}{MAYBE_STARTED if op == 'submit' else ''}") from exc


@server.tool()
async def job_submit(command: str, name: str = "job", expected_minutes: float | None = None) -> str:
    """Start `command` (one shell command line, run in your workspace) as a local job and return its job_id.

    Use it for work that takes more than about 10 minutes. `expected_minutes` is your estimate. The command goes
    through the same approval rules as a shell command and may wait for the PI; a refusal is returned, not retried.
    Then summarize what you wait for and end your turn: you are resumed when the job ends."""
    out = await _broker("submit", {"command": command, "name": name, "expected_minutes": expected_minutes},
                        timeout=S.policy.approvals.timeout_s + 120)
    if out.get("error"):
        raise tool_failure(str(out["error"]))
    if not out.get("submitted"):
        return json.dumps({"submitted": False, "reason": out.get("reason") or "denied",
                           "note": "Do not resubmit this command unless the PI explicitly asks."})
    return json.dumps({**out, "note": "Do not poll in a loop. Summarize what you are waiting for and end your turn; "
                                      "you will be resumed when the job finishes."})


@server.tool()
async def job_status(job_id: str) -> str:
    """The state of one of your jobs (running/completed/failed/cancelled/unknown_finished), its exit status and the
    end of its stdout and stderr."""
    out = await _broker("status", {"job_id": job_id}, timeout=60)
    if out.get("error"):
        raise tool_failure(str(out["error"]))
    return json.dumps(out)


@server.tool()
async def job_list() -> str:
    """Your local jobs in this task or request."""
    return json.dumps(await _broker("list", {}, timeout=60))


@server.tool()
async def job_cancel(job_id: str) -> str:
    """Stop one of your running jobs and end its processes."""
    out = await _broker("cancel", {"job_id": job_id}, timeout=120)
    if out.get("error"):
        raise tool_failure(str(out["error"]))
    return json.dumps(out)


if __name__ == "__main__":
    server.run()
