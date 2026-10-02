"""Per-task question tool for CSO, facilities, colleague and PI answers."""

from __future__ import annotations

import json
import os
from typing import Literal

import httpx

from ..models import ASK_MAX_WAIT_S
from ..ask_results import ask_result
from ..settings import Settings
from ._mcpcompat import make_server


S = Settings.load(os.environ.get("LABHQ_CONFIG"))
BROKER = os.environ.get("LABHQ_BROKER_URL", f"http://127.0.0.1:{S.runner.broker_port}")
TOKEN = os.environ.get("LABHQ_BROKER_TOKEN", "")
TASK = os.environ.get("LABHQ_TASK_ID")
AGENT = os.environ.get("LABHQ_AGENT_ID")

server = make_server(
    "labhq-ask",
    instructions=(
        "Ask one bounded blocking question. Use cso for method or scope, facilities for environment or tool "
        "failures, colleague:<agent_id> for facts only that colleague can answer, and pi only for hard stops. "
        "A pending response means end the turn; labhq resumes the same session when the answer arrives."
    ),
)


@server.tool()
async def ask(to: str, question: str, why_blocked: str, tried: list[str] | None = None,
              options: list[str] | None = None, refs: list[str] | None = None,
              wait: Literal["short", "hibernate"] = "short") -> str:
    """Ask a single question and return one terminal answer or a hibernate instruction."""
    payload = {"task_id": TASK, "agent_id": AGENT, "to": to, "question": question,
               "why_blocked": why_blocked, "tried": tried or [], "options": options or [],
               "refs": refs or [], "wait": wait}
    try:
        async with httpx.AsyncClient(timeout=ASK_MAX_WAIT_S + 30) as client:
            response = await client.post(f"{BROKER}/ask", headers={"X-Labhq-Token": TOKEN}, json=payload)
            if response.status_code == 400:  # the question itself is malformed: say how to fix it (#331)
                return json.dumps(ask_result(reason=response.json().get("detail") or response.text),
                                  ensure_ascii=False)
            response.raise_for_status()
            return json.dumps(response.json(), ensure_ascii=False)
    except Exception as exc:
        return json.dumps(ask_result(reason=f"질의 broker에 연결하지 못했습니다: {exc}"),
                          ensure_ascii=False)


if __name__ == "__main__":
    server.run()
