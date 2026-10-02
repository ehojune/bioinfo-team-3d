"""#149 decision 15: deterministic research-plan advisory A/B, with B1 boundaries unchanged."""
from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import pytest

from labhq.orchestrator.cso import Orchestrator
from labhq.research import semantics_shadow as shadow
from tests.test_output_types_research import plan_with, research_hub
from tests.test_research_protocol import valid_pack_values, valid_plan
from tests.test_semantics_shadow_provenance import V, _typed_pair
from tests.semantics_shadow_lab import line_for


def _payload(task):
    return {"prompt": task.prompt, "schema": task.output_schema, "meta": task.meta,
            "agent": task.agent_id, "resume": task.resume_session_id}


def test_request_hash_assignment_is_stable_and_has_both_arms():
    first = {rid: shadow.ab_arm(rid) for rid in [f"req_{i}" for i in range(40)]}
    assert first == {rid: shadow.ab_arm(rid) for rid in first}
    assert set(first.values()) == {"advisory", "shadow"}


def test_b1_selector_returns_only_the_three_allowed_prompt_fields(tmp_path, monkeypatch):
    hub, requests = _typed_pair(tmp_path)
    observed = {}
    line_for(hub, "req_a", observed)
    paths = shadow.ShadowPaths(tmp_path / "semantics")
    paths.root.mkdir(parents=True)
    paths.observed.write_text(json.dumps(observed), encoding="utf-8")
    service = shadow.ShadowService(hub, shadow.ShadowConfig(mode="ab"), paths)
    monkeypatch.setattr(shadow, "ab_arm", lambda rid: "advisory")

    candidates = service.advisory_candidates("req_b", requests["req_b"]["plan"])

    assert len(candidates) == 1 and set(candidates[0]) == {"artifact_id", "data_type", "request_id"}
    assert candidates[0]["artifact_id"].startswith("sem:")
    encoded = json.dumps(candidates)
    assert "outputs/" not in encoded and "request text" not in encoded and str(tmp_path) not in encoded


@pytest.mark.asyncio
async def test_off_shadow_and_ab_shadow_arm_dispatch_the_same_plan_bytes():
    captures = []
    for service in (None, SimpleNamespace(advisory_candidates=lambda rid, plan: (
            [] if shadow.ab_arm(rid) == "shadow" else pytest.fail("request moved out of shadow arm")))):
        hub, _ = research_hub([plan_with([])], declare_on=False)
        hub.requests["r0"] = hub.requests.pop("r")
        if service is not None:
            hub.semantics_shadow = service
        await Orchestrator(hub).run_request("r0")
        captures.append(_payload(hub.calls[0]))
        assert [task.meta["kind"] for task in hub.calls] == ["plan"]
    assert captures[0] == captures[1]


@pytest.mark.asyncio
async def test_advisory_prompt_has_no_path_or_request_body_and_does_not_change_cp1_gate():
    refs = []

    def first(pack_refs):
        return plan_with([{"name": "result1.tsv", "data_type": "de_table"}])(pack_refs)

    def second(pack_refs):
        plan = copy.deepcopy(valid_plan(pack_refs, pack_values=valid_pack_values()))
        plan["steps"][0]["input_refs"] = ["sem:0123abcd"]
        return plan

    hub, _ = research_hub([first, second], declare_on=True)

    def candidates(rid, plan):
        refs.append(plan)
        return [{"artifact_id": "sem:0123abcd", "data_type": "de_table", "request_id": "req_prior"}]

    hub.semantics_shadow = SimpleNamespace(advisory_candidates=candidates)
    await Orchestrator(hub).run_request("r")

    plans = [task for task in hub.calls if task.meta["kind"] == "plan"]
    assert len(plans) == 2 and "Optional reusable artifacts" not in plans[0].prompt
    assert "artifact_id=sem:0123abcd data_type=de_table created_request_id=req_prior" in plans[1].prompt
    assert "outputs/" not in plans[1].prompt.split("Optional reusable artifacts", 1)[1]
    assert hub.requests["r"]["outcome"] == "plan_approved" and len(hub.approvals) == 1
    assert hub.requests["r"]["plan"]["steps"][0]["input_refs"] == ["sem:0123abcd"]


def test_ab_line_records_arm_reference_and_cost(tmp_path, monkeypatch):
    hub, requests = _typed_pair(tmp_path)
    observed = {}
    line_for(hub, "req_a", observed)
    candidate = line_for(hub, "req_b", observed)["provenance"]["candidate_refs"][0]
    requests["req_b"].update(cost_usd=1.25, cost_known=True)
    requests["req_b"]["plan"]["steps"][0]["input_refs"] = [candidate]
    monkeypatch.setattr(shadow, "ab_arm", lambda rid: "advisory")

    snap = shadow.take_snapshot(hub, "req_b", shadow.ShadowConfig(mode="ab"))
    line = shadow.compute_line(snap, observed, lambda: None, epoch=1)

    assert (line["arm"], line["referenced"], line["cost_usd"], line["cost_known"]) == (
        "advisory", True, 1.25, True)
