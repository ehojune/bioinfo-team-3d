"""Runner wait targets survive the bounded reconnect replay, without new storage."""

import json
from pathlib import Path
import subprocess

import pytest

from labhq.gateway.server import create_app
from labhq.settings import Settings
from tests.support.node_binary import node_binary


@pytest.mark.parametrize("later_events", [199, 200, 201])
def test_runner_wait_snapshot_survives_replay_boundary(tmp_path, later_events):
    node = node_binary()
    if not node:
        pytest.skip("Node is optional")
    settings = Settings()
    settings.gateway.state_dir = str(tmp_path / "state")
    hub = create_app(settings).state.hub
    hub.agents = {a: {"id": a} for a in ("analyst", "engineer", "cso")}
    hub.requests["r"] = {
        "id": "r", "mode": "orchestrate", "status": "waiting_for_runner",
        "plan": {"steps": [{"id": "a", "agent_id": "analyst"},
                           {"id": "b", "agent_id": "engineer"}]},
        "runner_waits": {"task-a": {"agent_id": "analyst", "since": 1, "deadline_at": 1000}},
    }
    hub.events.append({"type": "request.runner_wait", "request_id": "r",
                       "data": {"agent_id": "analyst"}})
    for n in range(later_events):
        hub.events.append({"type": "request.status", "request_id": "other",
                           "data": {"status": "running"}, "ts": n + 2})
    snap = hub.snapshot()
    assert len(snap["data"]["recent_events"]) == 200
    assert any(e["type"] == "request.runner_wait" for e in snap["data"]["recent_events"]) == (later_events < 200)
    script = """
const assert = require('node:assert/strict');
require('./labhq/web/state.js');
const o = LabHQState.createOfficeState();
o.apply(JSON.parse(require('node:fs').readFileSync(0, 'utf8')));
assert.equal(o.stateText(o.ag('analyst')), '러너 기다림');
assert.equal(o.stateText(o.ag('engineer')), '차례 대기');
assert.equal(o.stateText(o.ag('cso')), '차례 대기');
"""
    subprocess.run([node, "-e", script], input=json.dumps(snap), text=True,
                   encoding="utf-8", cwd=Path(__file__).resolve().parents[1], check=True, timeout=15)
