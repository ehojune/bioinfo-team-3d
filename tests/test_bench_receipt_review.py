import asyncio
import hashlib

import pytest

from labhq import bench, reproduction_receipt, result_storage
from labhq.gateway import server
from labhq.settings import Settings
from test_bench_review3 import harness  # shared in-process Hub/Runner/Server doubles


@pytest.fixture
def receipt_harness(harness, monkeypatch):
    case, _, _, _ = harness
    hub = case.pop("_harness_hub")
    monkeypatch.setattr(reproduction_receipt, "_version", lambda *args: "fixture")
    monkeypatch.setattr(bench, "free_ports", lambda n: [19001 + i for i in range(n)])  # server/runner are doubles; never bind
    return case, hub


def test_receipt_does_not_require_hub_save_request(tmp_path, receipt_harness, monkeypatch):
    """P1 4315296432: an existing Hub contract has no persistence method."""
    case, hub = receipt_harness
    create = hub.create_request

    def create_request(request):
        rid = create(request)
        hub.requests[rid]["id"] = rid
        return rid

    hub.create_request = create_request
    receipt = {"fixture": "collected"}
    monkeypatch.setattr(result_storage, "collect_reproduction_receipt", lambda *args: receipt)
    run = asyncio.run(bench._run_labhq(case, tmp_path, "real", Settings()))
    assert run["reproduction_receipt"] == receipt
    assert run["unscripted_approvals"] == 0


def _bundle(settings, rid):
    bundle = settings.path(settings.runner.workspace_root) / "requests" / rid
    relative = "steps/analysis/outputs/run.py"
    source = "raise RuntimeError('syntax only; never execute')\n"
    path = bundle / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    (bundle / "MANIFEST.tsv").write_text(
        f"relative_path\tstatus\n{relative}\tcopied\n", encoding="utf-8")
    return hashlib.sha256(source.encode()).hexdigest()


def test_receipt_uses_returned_id_without_mutating_request(tmp_path, receipt_harness, monkeypatch):
    """P1 4315630179: the Hub request dictionary need not contain an id."""
    case, hub = receipt_harness
    saved = []
    hub.save_request = saved.append
    collect = result_storage.collect_reproduction_receipt

    def collect_fixture(request, settings):
        expected_hash = _bundle(settings, "request")
        receipt = collect(request, settings)
        assert receipt["files"][0]["sha256"] == expected_hash
        return receipt

    monkeypatch.setattr(result_storage, "collect_reproduction_receipt", collect_fixture)
    run = asyncio.run(bench._run_labhq(case, tmp_path, "real", Settings()))
    assert run["reproduction_receipt"]["files"][0]["execution"]["status"] == "not_rerun"
    assert "id" not in hub.requests["request"]
    assert saved == ["request"]


@pytest.mark.parametrize("outcome", ["ready", "unavailable", "stalled"])
def test_receipt_waits_for_bundle_outcome(tmp_path, receipt_harness, monkeypatch, outcome):
    """P1 4315630166: terminal status precedes the asynchronous bundle outcome."""
    case, hub = receipt_harness
    saved = []
    hub.save_request = saved.append
    case["timeout_s"] = 0.15
    expected = []
    collect = result_storage.collect_reproduction_receipt

    def create_request(request):
        hub.requests["request"] = {"id": "request", "status": "done", "bundle_pending": True}

        async def finish_bundle():
            await asyncio.sleep(0.02)
            if outcome == "stalled":
                return
            if outcome == "ready":
                expected.append(_bundle(settings_used[0], "request"))
            # The gateway may replace a request; use its latest record after the wait.
            hub.requests["request"] = {"id": "request", "status": "done",
                                       "bundle_warning": "fixture" if outcome == "unavailable" else ""}

        asyncio.create_task(finish_bundle())
        return "request"

    def collect_fixture(request, settings):
        assert not request.get("bundle_pending"), "receipt collected before bundle outcome"
        return collect(request, settings)

    settings_used = []
    original_create_app = server.create_app

    def create_app(settings):
        settings_used.append(settings)
        return original_create_app(settings)

    hub.create_request = create_request
    monkeypatch.setattr("labhq.gateway.server.create_app", create_app)
    monkeypatch.setattr(result_storage, "collect_reproduction_receipt", collect_fixture)
    if outcome == "stalled":
        with pytest.raises(TimeoutError, match="bundle"):
            asyncio.run(bench._run_labhq(case, tmp_path, "real", Settings()))
        assert saved == []
        assert "reproduction_receipt" not in hub.requests["request"]
    else:
        run = asyncio.run(bench._run_labhq(case, tmp_path, "real", Settings()))
        receipt = run["reproduction_receipt"]
        if outcome == "ready":
            assert receipt["files"][0]["sha256"] == expected[0]
            assert receipt["files"][0]["execution"]["status"] == "not_rerun"
        else:
            assert receipt["files"] == []
        assert saved == ["request"]
