"""Round records are local first and remote publication is guarded."""

import json

import httpx
import pytest

from labhq.gateway.server import Hub
from labhq.settings import DataZone, Settings


def settings(tmp_path):
    cfg = Settings()
    cfg.gateway.state_dir = str(tmp_path / "state")
    cfg.policy.data_zones = [DataZone(path="/data/restricted")]
    return cfg


def round_request(hub, rid, status):
    hub.requests[rid] = {
        "id": rid, "text": "Inspect /data/restricted/sample and ghp_" + "x" * 30,
        "mode": "orchestrate", "project_id": "demo", "status": status,
        "created_at": 1.0, "finished_at": 3.0 if status != "interrupted" else None,
        "clarifications": [{"questions": ["scope?"], "answer": "one sample"}],
        "step_decisions": [{"step_id": "b", "approved": False}],
        "plan": {"steps": [
            {"id": "a", "agent_id": "analyst", "depends_on": [], "outputs": ["a.txt"]},
            {"id": "b", "agent_id": "reviewer", "depends_on": ["a"], "outputs": ["b.txt"]},
        ], "warnings": ["mock warning"]},
        "results": {"a": {"status": "done", "attempts": 2, "usage": {"input_tokens": 8},
                           "cost_usd": 0.01, "cost_known": True, "outputs": ["a.txt"],
                           "workdir_id": "task-a"}},
        "review": {"verdict": "accept", "scores": {"evidence": 4}},
        "review_progress": {"last_completed_revision": 1}, "usage": {"input_tokens": 8},
        "cost_usd": 0.01, "cost_known": True,
        "report": "Cause: analyst result; reviewer skipped" if status == "failed" else "Finished",
    }
    if status == "failed":
        hub.requests[rid]["results"]["b"] = {"status": "skipped", "attempts": 0,
                                               "error_kind": "dependency_failed", "error": "skipped: a"}
    hub.save_request(rid)


@pytest.mark.parametrize("status", ["done", "failed", "interrupted"])
def test_local_round_schema_and_status(tmp_path, status):
    hub = Hub(settings(tmp_path))
    round_request(hub, "req-one", status)
    record = hub.rounds.write("req-one")
    assert set(record) == {"schema_version", "request_id", "request", "environment", "plan",
                           "steps", "review", "pi_decisions", "result"}
    assert record["schema_version"] == 1
    assert record["request"]["clarifications"][0]["answer"] == "one sample"
    assert record["request"]["step_decisions"][0]["approved"] is False
    assert record["steps"][0]["attempts"] == 2
    assert record["result"]["status"] == status
    if status == "failed":
        assert record["steps"][1]["status"] == "skipped"
        assert "reviewer skipped" in record["result"]["root_cause_summary"]
    md = (hub.rounds.directory / "req-one.md").read_text(encoding="utf-8")
    assert "<details><summary>JSON schema v1</summary>" in md
    assert json.loads((hub.rounds.directory / "req-one.json").read_text(encoding="utf-8")) == record
    assert "/data/restricted/sample" in md


@pytest.mark.asyncio
async def test_terminal_commit_and_restart_recovery_write_files(tmp_path):
    cfg = settings(tmp_path)
    hub = Hub(cfg)
    round_request(hub, "req-done", "done")
    hub.commit_terminal("req-done", "request.completed", {"ok": True})
    assert (hub.rounds.directory / "req-done.json").exists()
    round_request(hub, "req-running", "running")
    restarted = Hub(cfg)
    record = json.loads((restarted.rounds.directory / "req-running.json").read_text(encoding="utf-8"))
    assert record["result"]["status"] == "interrupted"
    assert any(d["kind"] == "resume" and d["state"] == "pending" for d in record["pi_decisions"])


def test_missing_outputs_and_manifest_provenance(tmp_path):
    hub = Hub(settings(tmp_path))
    round_request(hub, "req-one", "failed")
    workdir = tmp_path / "run"
    workdir.mkdir()
    (workdir / "manifest.json").write_text(json.dumps({
        "model": "model-from-run", "runs": {"task-a": {"started_at": 1, "ended_at": 2.5,
            "plugins": [{"name": "example-plugin", "revision": "abc"}]}}}), encoding="utf-8")
    hub.requests["req-one"]["results"]["a"].update(workdir=str(workdir), missing_outputs=["a.txt"])
    record = hub.rounds.write("req-one")
    assert record["steps"][0]["status"] == "INCOMPLETE"
    assert record["steps"][0]["duration_s"] == 1.5
    assert "model-from-run" in record["environment"]["model_ids"]
    assert record["environment"]["plugin_provenance"][0]["name"] == "example-plugin"


class FakeGitHub:
    def __init__(self, public=False):
        self.public, self.calls, self.issue = public, [], None

    def __call__(self, request):
        body = json.loads(request.content) if request.content else None
        self.calls.append((request.method, request.url.path, body))
        path = request.url.path
        if request.method == "GET" and path == "/repos/records/private":
            return httpx.Response(200, json={"private": not self.public,
                                             "visibility": "public" if self.public else "private"})
        if request.method == "GET" and path.endswith("/issues"):
            return httpx.Response(200, json=[self.issue] if self.issue else [])
        if request.method == "POST" and path.endswith("/issues"):
            self.issue = {"number": 7, "body": body["body"]}
            return httpx.Response(201, json=self.issue)
        if request.method == "PATCH" and path.endswith("/issues/7"):
            self.issue["body"] = body["body"]
            return httpx.Response(200, json=self.issue)
        return httpx.Response(404, json={})


@pytest.mark.asyncio
async def test_private_issue_redaction_restart_and_update(tmp_path):
    cfg = settings(tmp_path)
    cfg.dev_log.repo = "records/private"
    remote = FakeGitHub()
    hub = Hub(cfg, httpx.MockTransport(remote))
    round_request(hub, "req-one", "interrupted")
    hub.rounds.write("req-one")
    assert await hub.rounds.publish("req-one")
    assert remote.issue is not None
    assert "/data/restricted" not in remote.issue["body"]
    assert "ghp_" not in remote.issue["body"]
    assert "<restricted-zone>" in remote.issue["body"]
    first = remote.issue["body"]
    assert first.count("<!-- labhq round req-one -->") == 1

    restarted = Hub(cfg, httpx.MockTransport(remote))
    restarted.requests["req-one"].update(status="done", report="Finished after resume", finished_at=8.0)
    restarted.save_request("req-one")
    restarted.rounds.write("req-one")
    assert await restarted.rounds.publish("req-one")
    assert first != remote.issue["body"]
    assert "Finished after resume" in remote.issue["body"]
    assert await restarted.rounds.publish("req-one")
    assert sum(method == "POST" for method, _, _ in remote.calls) == 1
    assert sum(method == "PATCH" for method, _, _ in remote.calls) == 1
    assert sum(method == "GET" and path == "/repos/records/private" for method, path, _ in remote.calls) == 2


@pytest.mark.asyncio
async def test_public_repo_refused_and_visibility_cached(tmp_path):
    cfg = settings(tmp_path)
    cfg.dev_log.repo = "records/private"
    remote = FakeGitHub(public=True)
    hub = Hub(cfg, httpx.MockTransport(remote))
    round_request(hub, "req-one", "done")
    hub.rounds.write("req-one")
    assert not await hub.rounds.publish("req-one")
    assert not await hub.rounds.publish("req-one")
    assert sum(method == "GET" for method, _, _ in remote.calls) == 1
    assert not any(method in {"POST", "PATCH"} for method, _, _ in remote.calls)
