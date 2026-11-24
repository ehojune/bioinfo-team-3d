"""Exercise the compact draft through the research request and report checks."""

from hashlib import sha256

import pytest

from labhq.orchestrator.cso import Orchestrator
from labhq.research import analysis_draft
from labhq.research.contract import plan_sha256
from tests.test_research_cp2 import _interrupt
from tests.test_research_protocol import valid_plan
from tests.test_research_report import REPORT, _hub, _kinds


def _draft_hub(tmp_path, *, problem=None):
    hub = _hub()
    req = hub.requests["r"]
    req["research_options"] = {"compact_workflow": True}
    reply = hub.reply
    approve = hub.request_approval

    async def with_draft(task):
        result = await reply(task)
        if task.meta["kind"] != "step":
            return result
        [path] = task.meta["optional_outputs"]
        assert path in task.meta["outputs"] and path not in task.meta["required_outputs"]
        assert path in task.prompt
        target = tmp_path / path
        target.parent.mkdir()
        table = result.outputs[0]
        (tmp_path / table).write_bytes(b"effect\n0.25\n")
        body = {
            "empty": " \n",
            "anchor": "Unknown claim [[claim:s1/wrong]].",
            "table": f"The [measured effect]({table}) was 0.75 [[claim:s1/c1]].",
            "table_missing": "The [measured effect](outputs/missing.tsv) was 0.25 [[claim:s1/c1]].",
        }.get(problem, REPORT)
        data = b"\xff" if problem == "encoding" else body.encode("utf-8")
        target.write_bytes(data)
        hashes = {**result.output_sha256, table: sha256(b"effect\n0.25\n").hexdigest()}
        if problem != "unhashed":
            hashes[path] = sha256(data).hexdigest()
        return result.model_copy(update={
            "workdir": str(tmp_path),
            "outputs": [*result.outputs, *([] if problem == "missing" else [path])],
            "output_sha256": hashes,
        })

    async def after_step(**kwargs):
        if problem == "changed" and kwargs["kind"] == "research_evidence":
            draft = analysis_draft.for_request(req)
            (tmp_path / draft.path).write_bytes(b"Changed after collection [[claim:s1/c1]].")
        return await approve(**kwargs)

    hub.reply = with_draft
    hub.request_approval = after_step
    return hub


@pytest.mark.asyncio
async def test_compact_report_uses_collected_analyst_draft_without_cso_writing(tmp_path):
    hub = _draft_hub(tmp_path)
    await Orchestrator(hub).run_request("r")

    req = hub.requests["r"]
    assert _kinds(hub) == ["plan", "step", "review"]
    assert req["status"] == "done" and req["outcome"] == "research_reported"
    assert hub.calls[1].agent_id == "worker"
    assert req["report"].startswith(REPORT)
    assert "no external cohort" in req["report"]
    assert req["research_contract"]["report_check"] == {"anchors": 1, "problems": []}
    assert req["research_contract"]["plan_sha256"] == plan_sha256(req["plan"])
    assert analysis_draft.for_request(req).path in req["results"]["s1"]["output_sha256"]


@pytest.mark.asyncio
@pytest.mark.parametrize(("problem", "error"), [
    ("missing", "did not deliver"),
    ("unhashed", "no collected SHA-256"),
    ("changed", "differs from the collected SHA-256"),
    ("empty", "is empty"),
    ("encoding", "unreadable as UTF-8"),
    ("anchor", None),
    ("table", None),
    ("table_missing", None),
])
async def test_compact_report_rejects_missing_or_invalid_draft(tmp_path, problem, error):
    hub = _draft_hub(tmp_path, problem=problem)
    await Orchestrator(hub).run_request("r")

    req = hub.requests["r"]
    assert req["status"] == "failed"
    assert "synthesis" not in _kinds(hub)
    if error:
        assert req["outcome"] == "research_failed"
        assert req["research_contract"]["failure"]["stage"] == "analysis_draft"
        assert error in req["error"]
    else:
        assert req["outcome"] == "report_incomplete"
        if problem == "anchor":
            assert any("claim wrong" in line for line in req["research_contract"]["report_check"]["problems"])
        elif problem == "table":
            assert req["research_contract"]["report_consistency"]["findings"][0]["kind"] == "number"
        else:
            assert req["research_contract"]["report_consistency"]["checked"]["unread_tables"] == [
                "outputs/missing.tsv"]


@pytest.mark.asyncio
async def test_compact_resumes_the_saved_draft_and_review(tmp_path):
    hub = _draft_hub(tmp_path)
    await Orchestrator(hub).run_request("r")
    req = hub.requests["r"]
    report, saved_review = req["report"], req["research_contract"]["review"]
    _interrupt(hub)
    calls, approvals = len(hub.calls), len(hub.approvals)

    await Orchestrator(hub).run_request("r", resume=True)

    req = hub.requests["r"]
    assert _kinds(hub, calls) == [] and hub.approvals[approvals:] == []
    assert req["status"] == "done" and req["report"] == report
    assert req["research_contract"]["review"] == saved_review


@pytest.mark.asyncio
@pytest.mark.parametrize("saved", [None, {"compact_workflow": False}])
async def test_legacy_report_and_plan_hash_follow_frozen_options(tmp_path, saved):
    compact = _draft_hub(tmp_path)
    await Orchestrator(compact).run_request("r")
    legacy = _hub()
    legacy.s.research.compact_workflow = True
    if saved is not None:
        legacy.requests["r"]["research_options"] = saved
    await Orchestrator(legacy).run_request("r")

    req = legacy.requests["r"]
    assert _kinds(legacy) == ["plan", "step", "review", "synthesis"]
    assert req["status"] == "done" and req["report"].startswith(REPORT)
    assert req["plan"] == compact.requests["r"]["plan"]
    assert req["research_contract"]["plan_sha256"] == compact.requests["r"]["research_contract"]["plan_sha256"]


def test_draft_owner_follows_the_frozen_dag_and_uses_a_new_path_per_round():
    plan = valid_plan(steps=3)
    preparation, analysis, validation = plan["steps"]
    preparation["phase"] = "evidence"
    analysis["depends_on"] = ["s1"]
    validation.update(phase="validation", depends_on=["s2"])
    plan["steps"] = [validation, analysis, preparation]
    req = {"plan": plan, "research_options": {"compact_workflow": True}, "research_contract": {"round": 1}}

    draft = analysis_draft.for_request(req)
    assert draft.step_id == "s2"
    req["research_contract"]["round"] = 2
    assert analysis_draft.for_request(req).path != draft.path
    assert list(analysis_draft.new_round_rerun(req, plan)) == ["s2"]
    analysis["phase"] = "evidence"
    assert analysis_draft.for_request(req).step_id == "s3"
    plan["branches"] = [{"id": "a"}]
    assert analysis_draft.for_request(req) is None
