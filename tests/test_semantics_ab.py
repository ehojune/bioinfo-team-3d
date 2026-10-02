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

    offer = service.advisory_offer("req_b", requests["req_b"]["plan"])

    candidates = offer["candidates"]
    assert len(candidates) == 1 and set(candidates[0]) == {"artifact_id", "data_type", "request_id"}
    assert candidates[0]["artifact_id"].startswith("sem:")
    assert offer["arm"] == "advisory" and offer["offered"] == [candidates[0]["artifact_id"]]
    encoded = json.dumps(offer)
    assert "outputs/" not in encoded and "request text" not in encoded and str(tmp_path) not in encoded
    # the shadow arm gets the same selector run: the ids it would have been offered, and nothing for the prompt
    monkeypatch.setattr(shadow, "ab_arm", lambda rid: "shadow")
    assert service.advisory_offer("req_b", requests["req_b"]["plan"]) == {
        "arm": "shadow", "offered": offer["offered"], "candidates": []}
    service.cfg = shadow.ShadowConfig(mode="shadow")
    assert service.advisory_offer("req_b", requests["req_b"]["plan"]) is None


@pytest.mark.asyncio
async def test_off_shadow_and_ab_shadow_arm_dispatch_the_same_plan_bytes():
    captures = []
    ab_shadow_arm = SimpleNamespace(advisory_offer=lambda rid, plan: (  # a would-be offer, never shown
        {"arm": "shadow", "offered": ["sem:0123abcd"], "candidates": []} if shadow.ab_arm(rid) == "shadow"
        else pytest.fail("request moved out of shadow arm")))
    for service, frozen in ((None, None), (SimpleNamespace(advisory_offer=lambda rid, plan: None), None),
                            (ab_shadow_arm, {"arm": "shadow", "offered": ["sem:0123abcd"]})):
        hub, _ = research_hub([plan_with([])], declare_on=False)
        hub.requests["r0"] = hub.requests.pop("r")
        if service is not None:
            hub.semantics_shadow = service
        await Orchestrator(hub).run_request("r0")
        captures.append(_payload(hub.calls[0]))
        assert [task.meta["kind"] for task in hub.calls] == ["plan"]
        assert hub.requests["r0"].get("semantics_ab") == frozen  # off and shadow mode add nothing to the request
    assert captures[0] == captures[1] == captures[2]


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

    def offer(rid, plan):
        refs.append(plan)
        return {"arm": "advisory", "offered": ["sem:0123abcd"],
                "candidates": [{"artifact_id": "sem:0123abcd", "data_type": "de_table", "request_id": "req_prior"}]}

    hub.semantics_shadow = SimpleNamespace(advisory_offer=offer)
    await Orchestrator(hub).run_request("r")

    plans = [task for task in hub.calls if task.meta["kind"] == "plan"]
    assert len(plans) == 2 and "Optional reusable artifacts" not in plans[0].prompt
    assert "artifact_id=sem:0123abcd data_type=de_table created_request_id=req_prior" in plans[1].prompt
    assert "outputs/" not in plans[1].prompt.split("Optional reusable artifacts", 1)[1]
    assert hub.requests["r"]["outcome"] == "plan_approved" and len(hub.approvals) == 1
    assert hub.requests["r"]["plan"]["steps"][0]["input_refs"] == ["sem:0123abcd"]
    assert hub.requests["r"]["semantics_ab"] == {"arm": "advisory", "offered": ["sem:0123abcd"]}


def test_ab_line_records_arm_reference_and_cost(tmp_path, monkeypatch):
    hub, requests = _typed_pair(tmp_path)
    observed = {}
    line_for(hub, "req_a", observed)
    candidate = line_for(hub, "req_b", observed)["provenance"]["candidate_refs"][0]
    requests["req_b"].update(cost_usd=1.25, cost_known=True, intake={"work_kind": "research"},
                             semantics_ab={"arm": "advisory", "offered": [candidate]})
    requests["req_b"]["plan"]["steps"][0]["input_refs"] = [candidate]
    monkeypatch.setattr(shadow, "ab_arm", lambda rid: "advisory")

    snap = shadow.take_snapshot(hub, "req_b", shadow.ShadowConfig(mode="ab"))
    line = shadow.compute_line(snap, observed, lambda: None, epoch=1)

    assert (line["arm"], line["offered"], line["referenced"], line["cost_usd"], line["cost_known"]) == (
        "advisory", 1, True, 1.25, True)


AB_FIELDS = {"arm", "offered", "referenced", "cost_usd", "cost_known"}


def _end_line(hub, requests, observed, *, offered=None, input_refs=None):
    """req_b's end record as a research request whose plan-time offer was ``offered``."""
    req = requests["req_b"]
    req["intake"] = {"work_kind": "research"}
    if offered is not None:
        req["semantics_ab"] = {"arm": "advisory", "offered": offered}
    if input_refs is not None:
        req["plan"]["steps"][0]["input_refs"] = input_refs
    snap = shadow.take_snapshot(hub, "req_b", shadow.ShadowConfig(mode="ab"))
    return snap, shadow.compute_line(snap, observed, lambda: None, epoch=1)


def test_ab_records_an_arm_only_where_the_research_advisory_hook_applies(tmp_path, monkeypatch):
    hub, requests = _typed_pair(tmp_path)
    observed = {}
    line_for(hub, "req_a", observed)
    monkeypatch.setattr(shadow, "ab_arm", lambda rid: "advisory")
    for mode in ("orchestrate", "direct"):  # a general CSO plan or a direct request is never offered anything
        requests["req_b"]["mode"] = mode
        snap = shadow.take_snapshot(hub, "req_b", shadow.ShadowConfig(mode="ab"))
        assert not AB_FIELDS & set(shadow.compute_line(snap, observed, lambda: None, epoch=1))
        assert not AB_FIELDS & set(shadow.failed_line(snap, "timeout", TimeoutError(), epoch=1, ms=1.0))
    requests["req_b"]["mode"] = "orchestrate"
    snap, line = _end_line(hub, requests, observed)
    assert AB_FIELDS <= set(line) and line["arm"] == "advisory"
    assert AB_FIELDS <= set(shadow.failed_line(snap, "timeout", TimeoutError(), epoch=1, ms=1.0))


def test_reference_is_judged_against_the_ids_frozen_at_plan_time(tmp_path, monkeypatch):
    hub, requests = _typed_pair(tmp_path)
    observed = {}
    line_for(hub, "req_a", observed)
    live = line_for(hub, "req_b", observed)["provenance"]["candidate_refs"][0]
    monkeypatch.setattr(shadow, "ab_arm", lambda rid: "advisory")

    # offered at plan time, gone from the candidates recomputed at the end: still a reference
    _, line = _end_line(hub, requests, observed, offered=["sem:0123abcd"], input_refs=["sem:0123abcd"])
    assert "sem:0123abcd" not in line["provenance"]["candidate_refs"]
    assert (line["offered"], line["referenced"]) == (1, True)
    # a candidate only at the end (the plan-time selector found none): never counted as a reference
    _, line = _end_line(hub, requests, observed, offered=[], input_refs=[live])
    assert live in line["provenance"]["candidate_refs"]
    assert (line["offered"], line["referenced"]) == (0, False)
    assert "sem:0123abcd" not in json.dumps(line)  # the frozen ids stay on the request; the line keeps a count


def test_a_plan_that_uses_an_offered_id_is_recorded_without_tripping_the_info_boundary(tmp_path, monkeypatch):
    hub, requests = _typed_pair(tmp_path)
    hub.s.semantics = "ab"
    service = shadow.ShadowService.start(hub)
    assert service is not None and service.cfg.mode == "ab"
    monkeypatch.setattr(shadow, "ab_arm", lambda rid: "advisory")
    for rid in ("req_a", "req_b"):
        service.after_request(rid)
        assert service.drain(10)
    lines = shadow.read_lines(service.paths)[0]
    live = lines[-1]["provenance"]["candidate_refs"][0]
    requests["req_b"].update(intake={"work_kind": "research"}, semantics_ab={"arm": "advisory", "offered": [live]})
    requests["req_b"]["plan"]["steps"][0]["input_refs"] = [live]

    service.after_request("req_b")
    assert service.drain(10)

    assert service.latched is None
    last = shadow.read_lines(service.paths)[0][-1]
    assert (last["request_id"], last["arm"], last["offered"], last["referenced"]) == ("req_b", "advisory", 1, True)


def test_only_the_ids_frozen_for_this_request_leave_the_sensitive_set(tmp_path, monkeypatch):
    hub, requests = _typed_pair(tmp_path)
    observed = {}
    line_for(hub, "req_a", observed)
    live = line_for(hub, "req_b", observed)["provenance"]["candidate_refs"][0]
    monkeypatch.setattr(shadow, "ab_arm", lambda rid: "advisory")

    snap, line = _end_line(hub, requests, observed, offered=[live], input_refs=[live])
    assert shadow.boundary_problems(line, shadow.sensitive_values(snap)) == []
    snap, line = _end_line(hub, requests, observed, offered=[], input_refs=[live])  # not offered: still a plan value
    assert shadow.boundary_problems(line, shadow.sensitive_values(snap))
