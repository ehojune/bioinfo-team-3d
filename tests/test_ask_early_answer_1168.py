"""An answer that arrives while the runner is still handing the ask to the gateway is not dropped (#1168)."""
import asyncio

import pytest

from labhq.models import AskRequest
from labhq.runner.approvals import Broker


async def _noop(_request):
    return None


def _ask(to, wait="short", question="Which output format should the table use?"):
    return AskRequest(task_id="task_a", agent_id="analyst", request_id="req_a", to=to, wait=wait,
                      question=question, why_blocked="A method choice is needed")


def _broker(on_ask):
    return Broker(0, _noop, _noop, _noop, on_ask=on_ask)


@pytest.mark.asyncio
@pytest.mark.parametrize("to,wait", [("cso", "hibernate"), ("cso", "short")])
async def test_answer_given_during_on_ask_is_returned_not_left_pending(to, wait):
    ask = _ask(to, wait)
    holder = {}

    async def on_ask(req):
        assert holder["broker"].resolve_ask(req.id, {"status": "answered", "answer": "TSV"})

    broker = holder["broker"] = _broker(on_ask)
    result = await asyncio.wait_for(broker.request_ask(ask), 2)
    assert result.get("answer") == "TSV" and result.get("status") == "answered"
    assert ask.id not in broker.hibernate_asks and ask.id not in broker.pending_asks
    assert broker.pending_for_task("task_a") == []


@pytest.mark.asyncio
async def test_failed_on_ask_leaves_no_pending_future():
    async def on_ask(_req):
        raise RuntimeError("gateway down")

    broker = _broker(on_ask)
    ask = _ask("cso")
    with pytest.raises(RuntimeError):
        await broker.request_ask(ask)
    assert ask.id not in broker.pending_asks and ask.id not in broker.hibernate_asks


@pytest.mark.asyncio
async def test_hibernate_without_early_answer_still_registers():
    broker = _broker(_noop)
    ask = _ask("cso", "hibernate")
    result = await broker.request_ask(ask)
    assert result["status"] == "pending" and broker.pending_for_task("task_a") == [ask.id]
