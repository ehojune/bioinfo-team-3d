"""Round records are local first and remote publication is guarded."""

import json
import asyncio

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
        "meta": {"case_id": "bench-case"},
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
    assert record["request"]["meta"] == {"case_id": "bench-case"}
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


def test_round_environment_links_the_configured_labhq_commit(tmp_path):
    cfg = settings(tmp_path)
    cfg.dev_log.source_repo = "example/labhq"
    hub = Hub(cfg)
    round_request(hub, "req-link", "done")
    record = hub.rounds.write("req-link")
    sha = record["environment"]["git_commit"]
    md = (hub.rounds.directory / "req-link.md").read_text(encoding="utf-8")
    assert record["environment"]["source_repo"] == "example/labhq"
    assert f"https://github.com/example/labhq/commit/{sha}" in md


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
@pytest.mark.parametrize("method", ["GET", "POST", "PATCH"])
async def test_transient_publication_retries_without_restart(tmp_path, method, monkeypatch):
    class FailsOnce(FakeGitHub):
        failed = False

        def __call__(self, request):
            if request.method == method and not self.failed:
                self.failed = True
                return httpx.Response(500, json={"message": "temporarily unavailable"})
            return super().__call__(request)

    cfg = settings(tmp_path)
    cfg.dev_log.repo = "records/private"
    remote = FailsOnce()
    if method == "PATCH":
        remote.issue = {"number": 7, "body": "<!-- labhq round req-retry -->\nold"}
    hub = Hub(cfg, httpx.MockTransport(remote))
    monkeypatch.setattr(hub.rounds, "RETRY_BASE_S", 0.01, raising=False)
    round_request(hub, "req-retry", "done")
    hub.rounds.write("req-retry")
    hub.rounds.submit("req-retry")
    try:
        await asyncio.wait_for(hub.rounds.drain(), 2)
        assert remote.failed
        assert remote.issue is not None and "Finished" in remote.issue["body"]
        assert hub.store.get("round_delivery", "req-retry") is None
    finally:
        hub.rounds.worker.cancel()
        await asyncio.gather(hub.rounds.worker, return_exceptions=True)


@pytest.mark.asyncio
async def test_403_reset_requires_exhausted_remaining_and_waits_until_reset(tmp_path, monkeypatch):
    class PositiveRemainingThenSuccess(FakeGitHub):
        def __call__(self, request):
            if not self.calls:
                self.calls.append((request.method, request.url.path, None))
                return httpx.Response(403, headers={"X-RateLimit-Remaining": "3",
                                                    "X-RateLimit-Reset": "4102444800"},
                                      json={"message": "forbidden"})
            return super().__call__(request)

    cfg = settings(tmp_path)
    cfg.dev_log.repo = "records/private"
    forbidden = PositiveRemainingThenSuccess()
    hub = Hub(cfg, httpx.MockTransport(forbidden))
    round_request(hub, "req-forbidden", "done")
    hub.rounds.write("req-forbidden")
    hub.rounds.submit("req-forbidden")
    try:
        await asyncio.wait_for(hub.rounds.drain(), 2)
        delivery = hub.store.get("round_delivery", "req-forbidden")
        assert delivery["state"] == "failed" and delivery["attempts"] == 1
        assert len(forbidden.calls) == 1
    finally:
        hub.rounds.worker.cancel()
        await asyncio.gather(hub.rounds.worker, return_exceptions=True)

    class ExhaustedOnce(FakeGitHub):
        limited = False

        def __call__(self, request):
            if not self.limited:
                self.limited = True
                return httpx.Response(403, headers={"X-RateLimit-Remaining": "0",
                                                    "X-RateLimit-Reset": "4102444800"},
                                      json={"message": "rate limited"})
            return super().__call__(request)

    waited = []

    async def fake_sleep(seconds):
        waited.append(seconds)

    remote = ExhaustedOnce()
    hub = Hub(cfg, httpx.MockTransport(remote))
    monkeypatch.setattr(hub.rounds, "_sleep", fake_sleep, raising=False)
    round_request(hub, "req-limited", "done")
    hub.rounds.write("req-limited")
    hub.rounds.submit("req-limited")
    try:
        await asyncio.wait_for(hub.rounds.drain(), 2)
        assert waited and waited[0] > 1_000_000_000
        assert remote.issue is not None
        assert hub.store.get("round_delivery", "req-limited") is None
    finally:
        hub.rounds.worker.cancel()
        await asyncio.gather(hub.rounds.worker, return_exceptions=True)


@pytest.mark.asyncio
async def test_rate_limit_retry_after_is_a_lower_bound_and_does_not_exhaust_attempts(tmp_path, monkeypatch):
    class RateLimitedOnce(FakeGitHub):
        limited = False

        def __call__(self, request):
            if not self.limited:
                self.limited = True
                return httpx.Response(429, headers={"Retry-After": "60"}, json={"message": "slow down"})
            return super().__call__(request)

    cfg = settings(tmp_path)
    cfg.dev_log.repo = "records/private"
    remote = RateLimitedOnce()
    hub = Hub(cfg, httpx.MockTransport(remote))
    monkeypatch.setattr(hub.rounds, "MAX_ATTEMPTS", 1, raising=False)
    slept = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(hub.rounds, "_sleep", fake_sleep, raising=False)
    round_request(hub, "req-rate", "done")
    hub.rounds.write("req-rate")
    hub.rounds.submit("req-rate")
    try:
        await asyncio.wait_for(hub.rounds.drain(), 2)
        assert slept == [60]
        assert remote.issue is not None
        assert hub.store.get("round_delivery", "req-rate") is None
    finally:
        hub.rounds.worker.cancel()
        await asyncio.gather(hub.rounds.worker, return_exceptions=True)


@pytest.mark.asyncio
async def test_missing_token_stays_pending_and_recovers_after_restart(tmp_path, monkeypatch):
    cfg = settings(tmp_path)
    cfg.dev_log.repo = "records/private"
    monkeypatch.delenv(cfg.github.token_env, raising=False)
    first = Hub(cfg)
    round_request(first, "req-token", "done")
    first.rounds.write("req-token")
    first.rounds.submit("req-token")
    try:
        await asyncio.wait_for(first.rounds.drain(), 2)
        assert first.store.get("round_delivery", "req-token")["state"] == "pending"
    finally:
        first.rounds.worker.cancel()
        await asyncio.gather(first.rounds.worker, return_exceptions=True)

    monkeypatch.setenv(cfg.github.token_env, "restored-token")
    remote = FakeGitHub()
    restarted = Hub(cfg, httpx.MockTransport(remote))
    restarted.rounds.recover()
    try:
        await asyncio.wait_for(restarted.rounds.drain(), 2)
        assert remote.issue is not None
        assert restarted.store.get("round_delivery", "req-token") is None
    finally:
        restarted.rounds.worker.cancel()
        await asyncio.gather(restarted.rounds.worker, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["public", 401, 403, 422, 500])
async def test_publication_stops_on_permanent_failure_or_retry_limit(tmp_path, failure, monkeypatch):
    calls = []

    def remote(request):
        calls.append(request.method)
        if failure == "public":
            return httpx.Response(200, json=[] if request.url.path.endswith("/issues") else {"private": False})
        return httpx.Response(failure, json={"message": "failed"})

    cfg = settings(tmp_path)
    cfg.dev_log.repo = "records/private"
    hub = Hub(cfg, httpx.MockTransport(remote))
    monkeypatch.setattr(hub.rounds, "RETRY_BASE_S", 0.01, raising=False)
    monkeypatch.setattr(hub.rounds, "MAX_ATTEMPTS", 3, raising=False)
    round_request(hub, "req-stop", "done")
    hub.rounds.write("req-stop")
    hub.rounds.submit("req-stop")
    try:
        await asyncio.wait_for(hub.rounds.drain(), 2)
        assert len(calls) == (2 if failure == "public" else 3 if failure == 500 else 1)
        assert not any(method in {"POST", "PATCH"} for method in calls)
        assert hub.store.get("round_delivery", "req-stop")["state"] == "failed"
        before = len(calls)
        hub.rounds.recover()
        await hub.rounds.drain()
        assert len(calls) == before
    finally:
        hub.rounds.worker.cancel()
        await asyncio.gather(hub.rounds.worker, return_exceptions=True)


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
async def test_public_repo_refused_and_visibility_rechecked_before_each_write(tmp_path):
    cfg = settings(tmp_path)
    cfg.dev_log.repo = "records/private"
    remote = FakeGitHub(public=True)
    hub = Hub(cfg, httpx.MockTransport(remote))
    round_request(hub, "req-one", "done")
    hub.rounds.write("req-one")
    assert not await hub.rounds.publish("req-one")
    assert not await hub.rounds.publish("req-one")
    assert sum(method == "GET" and path == "/repos/records/private" for method, path, _ in remote.calls) == 2
    assert not any(method in {"POST", "PATCH"} for method, _, _ in remote.calls)


@pytest.mark.asyncio
async def test_repo_made_public_after_first_post_stops_later_posts(tmp_path):
    cfg = settings(tmp_path)
    cfg.dev_log.repo = "records/private"
    remote = FakeGitHub()
    hub = Hub(cfg, httpx.MockTransport(remote))
    round_request(hub, "req-one", "interrupted")
    hub.rounds.write("req-one")
    assert await hub.rounds.publish("req-one")
    remote.public = True  # someone flips the records repo to public
    hub.requests["req-one"]["status"] = "done"
    hub.rounds.write("req-one")
    writes = sum(method in {"POST", "PATCH"} for method, _, _ in remote.calls)
    assert not await hub.rounds.publish("req-one")
    assert sum(method in {"POST", "PATCH"} for method, _, _ in remote.calls) == writes


@pytest.mark.asyncio
async def test_recovery_rebuilds_existing_records_and_skips_unchanged_publish(tmp_path):
    cfg = settings(tmp_path)
    cfg.dev_log.repo = "records/private"
    remote = FakeGitHub()
    hub = Hub(cfg, httpx.MockTransport(remote))
    round_request(hub, "req-one", "interrupted")
    hub.rounds.write("req-one")
    assert await hub.rounds.publish("req-one")
    hub.requests["req-one"]["status"] = "done"  # finished, but the process died before the record was rewritten
    stale = (hub.rounds.directory / "req-one.json").read_text(encoding="utf-8")
    hub.rounds.recover()
    await hub.rounds.drain()
    fresh = (hub.rounds.directory / "req-one.json").read_text(encoding="utf-8")
    assert fresh != stale and json.loads(fresh)["result"]["status"] == "done"
    assert (hub.rounds.directory / "req-one.md").exists()
    before = len(remote.calls)
    hub.rounds.recover()
    await hub.rounds.drain()
    assert len(remote.calls) == before  # same record already published: no API calls



@pytest.mark.asyncio
async def test_recovery_keeps_the_environment_the_round_ran_with(tmp_path, monkeypatch):
    from labhq.integrations import rounds
    cfg = settings(tmp_path)
    hub = Hub(cfg)
    hub.runner_capabilities = {"runner-1": {"engine_cli_versions": {"claude_code": "1.0.0"}}}
    round_request(hub, "req-env", "done")
    first = hub.rounds.write("req-env")["environment"]
    assert first["engine_cli_versions"] == {"runner-1": {"claude_code": "1.0.0"}}
    # The gateway restarts on an upgraded checkout with other runners attached.
    monkeypatch.setattr(rounds, "__version__", "99.0.0")
    hub.runner_capabilities = {"runner-2": {"engine_cli_versions": {"claude_code": "9.9.9"}}}
    hub.rounds.recover()
    rebuilt = json.loads((hub.rounds.directory / "req-env.json").read_text(encoding="utf-8"))["environment"]
    assert rebuilt["labhq_version"] == first["labhq_version"] != "99.0.0"
    assert rebuilt["engine_cli_versions"] == first["engine_cli_versions"]
    assert hub.store.get("request", "req-env")["environment"]["labhq_version"] == first["labhq_version"]


@pytest.mark.asyncio
async def test_legacy_request_takes_its_environment_from_the_first_record(tmp_path, monkeypatch):
    from labhq.integrations import rounds
    cfg = settings(tmp_path)
    hub = Hub(cfg)
    round_request(hub, "req-old", "done")
    hub.rounds.write("req-old")
    hub.requests["req-old"].pop("environment")  # written by a build that did not snapshot
    monkeypatch.setattr(rounds, "__version__", "99.0.0")
    hub.rounds.recover()
    rebuilt = json.loads((hub.rounds.directory / "req-old.json").read_text(encoding="utf-8"))["environment"]
    assert rebuilt["labhq_version"] != "99.0.0"


@pytest.mark.asyncio
async def test_record_uses_the_provenance_a_remote_runner_sent(tmp_path):
    hub = Hub(settings(tmp_path))
    round_request(hub, "req-remote", "done")
    hub.requests["req-remote"]["results"]["a"].update(
        workdir="/runner-only/disk/task-a",  # not on the gateway's disk
        provenance={"engine": "codex", "model": "model-remote", "runs": {"t1": {
            "started_at": 10.0, "ended_at": 25.0, "turns": 3, "engine_cli_version": "2.0.1",
            "model_id": "model-resolved", "plugins": [{"name": "remote-plugin", "version": "1.0"}]}}})
    record = hub.rounds.write("req-remote")
    step = next(s for s in record["steps"] if s["id"] == "a")
    assert (step["duration_s"], step["turns"], step["engine_cli_version"]) == (15.0, 3, "2.0.1")
    assert "model-remote" in record["environment"]["model_ids"]
    assert "model-resolved" in record["environment"]["model_ids"]
    assert record["environment"]["plugin_provenance"][0]["name"] == "remote-plugin"


def test_runner_probes_engine_versions_and_sends_the_manifest_summary(tmp_path):
    import sys
    from labhq.models import AgentSpec, Engine, Task
    from labhq.runner.versions import engine_cli_versions
    from labhq.runner.workspace import TaskWorkspace
    fake = tmp_path / "fake_cli.py"
    fake.write_text("import sys\nprint('codex-cli 9.8.7' if '--version' in sys.argv else 'hi')\n", encoding="utf-8")
    cfg = Settings()
    cfg.engines.codex.bin, cfg.engines.codex.prefix_args = sys.executable, [str(fake)]
    assert engine_cli_versions(cfg, {"codex", "mock"}) == {"codex": "9.8.7"}
    agent = AgentSpec(id="a", name="A", role="r", engine=Engine.codex, builtin_mcp=[])
    ws = TaskWorkspace(tmp_path / "runs", Task(agent_id="a", prompt="x"), agent)
    ws.update_run("t1", started_at=1.0, ended_at=4.0, engine_cli_version="9.8.7", usage={"secret": 1})
    prov = ws.provenance()
    assert prov["engine"] == "codex" and prov["runs"]["t1"] == {"started_at": 1.0, "ended_at": 4.0,
                                                              "engine_cli_version": "9.8.7"}


@pytest.mark.asyncio
async def test_every_write_directly_follows_a_visibility_check_even_after_paging(tmp_path):
    class TurnsPublicWhilePaging(FakeGitHub):
        def __call__(self, request):
            response = super().__call__(request)
            if request.method == "GET" and request.url.path.endswith("/issues") and self.flip:
                self.public = True
            return response

    cfg = settings(tmp_path)
    cfg.dev_log.repo = "records/private"
    remote = TurnsPublicWhilePaging()
    remote.flip = False
    hub = Hub(cfg, httpx.MockTransport(remote))
    round_request(hub, "req-a", "done")
    hub.rounds.write("req-a")
    assert await hub.rounds.publish("req-a")  # create
    hub.requests["req-a"]["report"] = "updated"
    hub.rounds.write("req-a")
    assert await hub.rounds.publish("req-a")  # edit
    writes = [i for i, (method, _, _) in enumerate(remote.calls) if method in {"POST", "PATCH"}]
    assert len(writes) == 2
    assert all(remote.calls[i - 1][:2] == ("GET", "/repos/records/private") for i in writes)
    remote.flip = True  # the repository turns public while the issue list is being read
    hub.requests["req-a"]["report"] = "updated again"
    hub.rounds.write("req-a")
    before = len([c for c in remote.calls if c[0] in {"POST", "PATCH"}])
    assert not await hub.rounds.publish("req-a")
    assert len([c for c in remote.calls if c[0] in {"POST", "PATCH"}]) == before


@pytest.mark.asyncio
async def test_round_records_drop_url_reference_queries_before_posting(tmp_path):
    cfg = settings(tmp_path)
    cfg.dev_log.repo = "records/private"
    remote = FakeGitHub()
    hub = Hub(cfg, httpx.MockTransport(remote))
    round_request(hub, "req-url", "done")
    legacy = "https://share.example.org/f/cohort.tsv?dl=opaqueSHAREcode99&token=tok123456"
    hub.requests["req-url"].update(references=[{"kind": "url", "value": legacy, "source": "request"}],
                                   report=f"Finished from {legacy}")
    hub.save_request("req-url")
    hub.rounds.write("req-url")
    assert await hub.rounds.publish("req-url")
    body = remote.issue["body"]
    assert "opaqueSHAREcode99" not in body and "tok123456" not in body
    assert "https://share.example.org/f/cohort.tsv" in body


@pytest.mark.asyncio
async def test_round_records_mask_reference_paths_like_project_reports(tmp_path):
    # #130: the round cleaner only dropped URL queries and zones; a private reference folder quoted in the
    # plan, review or report went to the records repository as is.
    from labhq.intake import Reference

    cfg = settings(tmp_path)
    cfg.dev_log.repo = "records/private"
    cfg.pi_profile.references = [Reference(kind="path", value=r"C:\Users\pi\Yuan")]
    remote = FakeGitHub()
    hub = Hub(cfg, httpx.MockTransport(remote))
    round_request(hub, "req-ref", "done")
    request = hub.requests["req-ref"]
    request["references"] = [{"kind": "path", "value": "/srv/private-notes/cohort", "source": "request"}]
    request["plan"]["steps"][0]["instruction"] = "Read /srv/private-notes/cohort/summary.md first"
    request["review"]["issues"] = [{"step_id": "a", "problem": "missed c:/users/pi/yuan/lessons.md"}]
    request["report"] = r"Used /srv/private-notes/cohort/a.tsv and C:\Users\pi\Yuan\b.md."
    hub.save_request("req-ref")
    hub.rounds.write("req-ref")
    assert await hub.rounds.publish("req-ref")
    body = remote.issue["body"]
    assert "private-notes" not in body and "yuan" not in body.casefold()
    assert body.count("<reference-path>") >= 4
    text = "see /srv/private-notes/cohort/x.md"
    assert hub.rounds.client.clean(text) == hub.reporter._clean(text) == "see <reference-path>/x.md"
