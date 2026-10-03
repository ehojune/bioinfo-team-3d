"""Output type declarations on the general lane (#221 todo 2): normalize, carry, record, and stay off by default."""

import hashlib
import itertools
import json

import pytest

from labhq import vocab
from labhq.evidence.claims import normalize_artifact_path
from labhq.intake import QUESTION_RULE
from labhq.models import Task, TaskResult
from labhq.orchestrator import cso
from labhq.orchestrator.cso import Orchestrator, validate_steps
from labhq.runner.daemon import Runner
from labhq.settings import Settings
from labhq.vocab import declare
from tests.test_cso import PENGUINS_PLAN, penguins_hub

V = vocab.load()
CANARY = "CANARY-7f3a-secret"
# main before #221 (1b73b5d): the plan schema and the rendered plan prompt with fixed arguments. The prompt hash
# moved on purpose since: #84 added the phone-card length rule to the plan questions; the 2nd mock trial added the
# one-environment-step rule (ENV_STEP_RULE); #36 added the scope verdict (schema, was 62d09433…) and the lab scope
# rule (prompt, was f0053d96…). Off still adds nothing.
PLAN_SCHEMA_SHA = "f8f30b831ff2aa79018ceed6185184a82c959d4885ca71a8a0fe678c10ee4eb9"
PLAN_PROMPT_SHA = "8829c2160710490a2985ea02db5f5ce3b93b21e91518be9f828936fda43f2817"
PROMPT_ARGS = dict(request="REQ", roster="ROSTER", capabilities="CAPS", briefing="BRIEF", max_steps=3,
                   question_rule=QUESTION_RULE, lab_scope="LAB")


def sha(value):
    return hashlib.sha256((value if isinstance(value, str) else json.dumps(value, sort_keys=True)).encode()).hexdigest()


# ---------------------------------------------------------------- normalization contract

def test_valid_entries_pair_with_outputs_and_carry_the_version():
    entries, issues = declare.normalize_entries(
        ["outputs/counts.tsv", "outputs/report.md"],
        [{"name": "counts.tsv", "data_type": "raw_counts", "format": "tsv"}, {"name": "./outputs/report.md",
                                                                             "data_type": "report"}], V)
    assert issues == {}
    assert entries == [{"name": "outputs/counts.tsv", "data_type": "raw_counts", "format": "tsv", "vocab": V.sha256},
                       {"name": "outputs/report.md", "data_type": "report", "vocab": V.sha256}]


@pytest.mark.parametrize("raw, code", [
    ("raw_counts", "bad_shape"),
    ([{"name": "outputs/a.tsv", "data_type": "counts_of_reads"}], "unknown_key"),
    ([{"name": "outputs/a.tsv", "data_type": "tsv"}], "wrong_branch"),
    ([{"name": "outputs/a.tsv", "format": "raw_counts"}], "wrong_branch"),
    ([{"name": "outputs/a.tsv", "data_type": 7}], "bad_value"),
    ([{"name": "outputs/a.tsv", "data_type": "x" * 65}], "too_long"),
    ([{"name": "outputs/other.tsv", "data_type": "table"}], "unpaired_name"),
    ([{"name": "../a.tsv", "data_type": "table"}], "unpaired_name"),
    ([{"name": "outputs/a.tsv", "data_type": "table", "path": "/etc"}], "bad_entry"),
    (["outputs/a.tsv"], "bad_entry"),
    ([{"data_type": "table"}], "bad_name"),
    ([{"name": "outputs/a.tsv", "data_type": "table"}] * 2, "too_many"),
    ([{"name": "outputs/a.tsv", "data_type": "table", "format": "x" * 9000}], "over_budget"),
])
def test_bad_declarations_are_dropped_with_a_fixed_code(raw, code):
    entries, issues = declare.normalize_entries(["outputs/a.tsv"], raw, V)
    assert entries == [] and set(issues) == {code}


def test_duplicate_declarations_of_one_file_are_unknown_in_any_order():
    raw = [{"name": "a.tsv", "data_type": "raw_counts", "format": "tsv"},
           {"name": "outputs/a.tsv", "data_type": "normalized_counts"},
           {"name": "b.tsv", "data_type": "nonsense"}, {"name": "outputs/b.tsv", "data_type": "table"}]
    outcomes = {json.dumps(declare.normalize_entries(["a.tsv", "b.tsv", "c.tsv", "d.tsv"], list(order), V),
                           sort_keys=True) for order in itertools.permutations(raw)}
    assert len(outcomes) == 1
    entries, issues = declare.normalize_entries(["a.tsv", "b.tsv", "c.tsv", "d.tsv"], raw, V)
    assert entries == [{"name": "outputs/a.tsv", "format": "tsv", "vocab": V.sha256}]
    assert issues == {"duplicate_declaration": 2, "unknown_key": 1}


def test_warnings_carry_codes_and_counts_never_the_declared_text():
    raw = [{"name": f"outputs/{CANARY}.tsv", "data_type": CANARY}, {"name": "outputs/a.tsv", "format": CANARY}]
    entries, issues = declare.normalize_entries(["outputs/a.tsv", "outputs/b.tsv"], raw, V)
    warning = declare.issue_warning("s1", issues)
    assert entries == [] and warning == "step s1: output_types ignored (unknown_key 1, unpaired_name 1)"
    assert CANARY not in json.dumps([entries, warning])


def test_prompt_rule_lists_local_keys_only():
    rule = declare.prompt_rule(V)
    assert "raw_counts" in rule and "fastq" in rule and "statistical_analysis" not in rule
    assert "Gene expression matrix" not in rule and "edamontology" not in rule
    assert len(rule) < 900  # about 200 tokens; the real per-engine usage is measured before turning it on


# ---------------------------------------------------------------- off: today's plan, byte for byte

def test_off_keeps_the_plan_schema_and_prompt_of_main():
    assert sha(cso.PLAN_SCHEMA) == PLAN_SCHEMA_SHA and cso.plan_schema(False) is cso.PLAN_SCHEMA
    assert sha(cso.PLAN_PROMPT.format(**PROMPT_ARGS, output_types_rule="")) == PLAN_PROMPT_SHA
    assert Settings().plan.declare_output_types is False


def test_on_adds_an_optional_free_string_field_and_one_rule():
    schema = cso.plan_schema(True)
    step = schema["properties"]["steps"]["items"]
    assert "output_types" in step["properties"] and "output_types" not in step["required"]
    assert "enum" not in json.dumps(step["properties"]["output_types"])
    assert sha(cso.PLAN_SCHEMA) == PLAN_SCHEMA_SHA  # the off schema object is never mutated


def test_validate_steps_without_a_vocabulary_drops_declarations():
    raw = [{"id": "a", "agent_id": "analyst", "instruction": "x", "depends_on": [], "outputs": ["outputs/t.tsv"],
            "output_types": [{"name": "outputs/t.tsv", "data_type": "table"}]}]
    steps, warnings = validate_steps(raw, {"analyst"}, 10)
    assert "output_types" not in steps[0] and warnings == []


def test_validate_steps_pairs_names_after_moving_outputs_under_outputs():
    raw = [{"id": "a", "agent_id": "analyst", "instruction": "Write ./answer.md", "depends_on": [],
            "outputs": ["answer.md", "outputs/calc.tsv"],
            "output_types": [{"name": "answer.md", "data_type": "report", "format": "markdown"},
                             {"name": "calc.tsv", "data_type": "nope"}]}]
    stats: dict = {}
    steps, warnings = validate_steps(raw, {"analyst"}, 10, vocab=V, stats=stats)
    assert steps[0]["outputs"] == ["outputs/answer.md", "outputs/calc.tsv"]
    assert steps[0]["output_types"] == [{"name": "outputs/answer.md", "data_type": "report", "format": "markdown",
                                         "vocab": V.sha256}]
    assert "step a: output_types ignored (unknown_key 1)" in warnings
    assert stats == {"outputs": 2, "data_declared": 1, "format_declared": 1, "issues": {"unknown_key": 1}}


# ---------------------------------------------------------------- through the CSO

def declared_penguins(extra=None):
    plan = json.loads(PENGUINS_PLAN.read_text(encoding="utf-8"))["plan"]
    by = {s["id"]: s for s in plan["steps"]}
    by["compute_qc_metrics"]["output_types"] = [
        {"name": "answer.md", "data_type": "report", "format": "markdown"},
        {"name": "outputs/qc_calculations.md", "data_type": "qc_report"}, *(extra or [])]
    return plan


@pytest.mark.asyncio
async def test_cso_off_sends_main_schema_and_dispatches_no_type_meta():
    hub = penguins_hub([declared_penguins()])
    await Orchestrator(hub).run_request("r")
    req = hub.requests["r"]
    assert req["status"] == "done", req.get("report")
    plan_task = next(t for t in hub.calls if t.meta["kind"] == "plan")
    assert sha(plan_task.output_schema) == PLAN_SCHEMA_SHA and "output_types" not in plan_task.prompt
    assert all("output_types" not in s for s in req["plan"]["steps"]) and "output_types_stats" not in req
    assert all("output_types" not in t.meta and "output_types_vocab" not in t.meta for t in hub.calls)


@pytest.mark.asyncio
async def test_cso_on_carries_normalized_declarations_to_dispatch():
    plan = declared_penguins()
    plan["steps"][0]["output_types"] = [{"name": "outputs/data_manifest.md", "data_type": CANARY},
                                        {"name": f"outputs/{CANARY}.md", "data_type": "report"}]
    hub = penguins_hub([plan])
    hub.s.plan.declare_output_types = True
    await Orchestrator(hub).run_request("r")
    req = hub.requests["r"]
    assert req["status"] == "done", req.get("report")
    plan_task = next(t for t in hub.calls if t.meta["kind"] == "plan")
    assert "output_types" in plan_task.output_schema["properties"]["steps"]["items"]["properties"]
    assert "raw_counts" in plan_task.prompt
    by = {t.meta.get("step_id"): t for t in hub.calls if t.meta["kind"] == "step"}
    assert by["compute_qc_metrics"].meta["output_types"] == {
        "outputs/answer.md": {"data_type": "report", "format": "markdown"},
        "outputs/qc_calculations.md": {"data_type": "qc_report"}}
    assert all(t.meta["output_types_vocab"] == V.sha256 for t in by.values())
    assert "output_types" not in by["materialize_data"].meta  # every declaration of that step was invalid
    outputs = sum(len(s["outputs"]) for s in req["plan"]["steps"])
    assert req["output_types_stats"] == {"outputs": outputs, "data_declared": 2, "format_declared": 1,
                                         "issues": {"unknown_key": 1, "unpaired_name": 1}, "vocab": V.sha256}
    assert "step materialize_data: output_types ignored (unknown_key 1, unpaired_name 1)" in req["plan"]["warnings"]
    public = json.dumps([req["plan"], hub.events, [t.meta for t in hub.calls], req["output_types_stats"]],
                        ensure_ascii=False, default=str)
    assert CANARY not in public


@pytest.mark.asyncio
async def test_cso_vocab_loader_exception_falls_back_to_the_off_contract(monkeypatch):
    def broken_load(*_args, **_kwargs):
        raise RuntimeError("unexpected loader failure")

    monkeypatch.setattr(vocab, "load", broken_load)
    vocab.reset_cache()
    try:
        hub = penguins_hub([declared_penguins()])
        hub.s.plan.declare_output_types = True
        await Orchestrator(hub).run_request("r")
        req = hub.requests["r"]
        plan_task = next(t for t in hub.calls if t.meta["kind"] == "plan")
        assert req["status"] == "done"
        assert sha(plan_task.output_schema) == PLAN_SCHEMA_SHA and "output_types" not in plan_task.prompt
        assert all(not (TYPE_META & set(t.meta)) for t in hub.calls)
    finally:
        vocab.reset_cache()


@pytest.mark.asyncio
async def test_off_after_planning_keeps_stored_declarations_and_stops_using_them():
    hub = penguins_hub([declared_penguins()])
    hub.s.plan.declare_output_types = True
    orch = Orchestrator(hub)
    stored = {"id": "s", "agent_id": "analyst", "instruction": "x", "depends_on": [], "outputs": ["outputs/a.tsv"],
              "output_types": [{"name": "outputs/a.tsv", "data_type": "table", "vocab": "0" * 64}]}
    assert orch._type_meta(stored) == {"output_types_vocab": "0" * 64,
                                       "output_types": {"outputs/a.tsv": {"data_type": "table"}}}
    hub.s.plan.declare_output_types = False
    assert orch._type_meta(stored) == {}
    assert stored["output_types"][0]["vocab"] == "0" * 64  # nothing rewritten


# ---------------------------------------------------------------- runner records

def test_runner_records_copy_declarations_and_infer_formats_by_name():
    meta = {"output_types_vocab": V.sha256,
            "output_types": {"outputs/a.tsv": {"data_type": "raw_counts"}, "outputs/b.md": {"format": "markdown"}}}
    records = declare.runner_records(["outputs/a.tsv", "outputs/b.md", "outputs/c.log"], meta, V)
    assert records["outputs/a.tsv"]["data_type"] == {"value": "raw_counts", "basis": "declared", "source": "plan"}
    assert records["outputs/a.tsv"]["format"] == {"value": "tsv", "basis": "inferred", "source": "extension"}
    assert records["outputs/b.md"]["data_type"]["reason"] == "not_declared"
    assert records["outputs/b.md"]["format"]["basis"] == "declared"
    assert records["outputs/c.log"]["format"] == {"value": "unknown", "basis": "unknown", "reason": "not_declared"}
    assert all(r["vocab"] == V.sha256 for r in records.values())


@pytest.mark.parametrize("vocab_arg, version, reason", [(None, V.sha256, "no_vocab"), (V, "0" * 64, "vocab_mismatch")])
def test_runner_infers_nothing_under_another_vocabulary(vocab_arg, version, reason):
    records = declare.runner_records(["outputs/a.tsv"], {"output_types_vocab": version}, vocab_arg)
    assert records["outputs/a.tsv"]["format"]["reason"] == reason


@pytest.mark.asyncio
async def test_runner_attaches_records_only_to_collected_outputs(tmp_path):
    from labhq.models import AgentSpec, Engine

    settings = Settings()
    settings.gateway.state_dir = settings.runner.state_dir = str(tmp_path / "state")
    settings.runner.workspace_root = str(tmp_path / "runs")
    settings.runner.agents_dir = str(tmp_path / "agents")
    settings.runner.talent_dir = str(tmp_path / "talent")
    settings.hpc.scheduler = "none"
    runner = Runner(settings)
    runner.registry.agents["worker"] = AgentSpec(id="worker", name="worker", role="test", engine=Engine.mock)
    meta = {"kind": "step", "outputs": ["artifact.txt", "missing.tsv"], "output_types_vocab": V.sha256,
            "output_types": {"outputs/artifact.txt": {"data_type": "report"}, "outputs/missing.tsv": {"data_type": "table"}}}
    typed = await runner.run_task(Task(agent_id="worker", request_id="r", prompt="Your step: make [artifact]", meta=meta))
    assert typed.outputs == ["outputs/artifact.txt"] and list(typed.output_types) == ["outputs/artifact.txt"]
    assert typed.output_types["outputs/artifact.txt"]["data_type"]["value"] == "report"
    plain = await runner.run_task(Task(agent_id="worker", request_id="r", prompt="Your step: make [artifact]",
                                       meta={"kind": "step", "outputs": ["artifact.txt"]}))
    assert plain.output_types == {} and "output_types" not in plain.model_dump(mode="json")


@pytest.mark.asyncio
async def test_runner_record_exception_keeps_the_successful_task_result(tmp_path, monkeypatch):
    from labhq.models import AgentSpec, Engine

    settings = Settings()
    settings.gateway.state_dir = settings.runner.state_dir = str(tmp_path / "state")
    settings.runner.workspace_root = str(tmp_path / "runs")
    settings.runner.agents_dir = str(tmp_path / "agents")
    settings.runner.talent_dir = str(tmp_path / "talent")
    settings.hpc.scheduler = "none"
    runner = Runner(settings)
    runner.registry.agents["worker"] = AgentSpec(id="worker", name="worker", role="test", engine=Engine.mock)

    def broken_records(*_args, **_kwargs):
        raise RuntimeError("record failure")

    monkeypatch.setattr(declare, "runner_records", broken_records)
    result = await runner.run_task(Task(
        agent_id="worker", request_id="r", prompt="Your step: make [artifact]",
        meta={"kind": "step", "outputs": ["artifact.txt"], "output_types_vocab": V.sha256}))
    assert result.ok and result.outputs == ["outputs/artifact.txt"] and result.output_types == {}


def test_task_result_drops_a_malformed_value_and_keeps_the_result():
    for bad in ([1, 2], "x", {f"o{i}": {} for i in range(declare.MAX_RECORDS + 1)}):
        parsed = TaskResult.model_validate({"task_id": "t", "agent_id": "a", "ok": True, "output_types": bad})
        assert parsed.ok and parsed.output_types == {}
    assert "output_types" not in TaskResult(task_id="t", agent_id="a", ok=True).model_dump(mode="json")


# ---------------------------------------------------------------- one reader for both models

def read(meta, result=None, staff=None, v=V):
    return declare.read(meta, result, v, normalize=normalize_artifact_path, staff=staff)


def test_reader_takes_runner_records_and_meta_under_the_same_version():
    meta = {"output_types_vocab": V.sha256, "output_types": {"outputs/a.tsv": {"data_type": "raw_counts"}}}
    records = declare.runner_records(["outputs/a.tsv"], meta, V)
    fields = read(meta, {"output_types": records})["outputs/a.tsv"]
    assert (fields["data_type"].value, fields["data_type"].basis) == ("raw_counts", "declared")
    assert (fields["format"].value, fields["format"].basis) == ("tsv", "inferred")
    assert read(meta)["outputs/a.tsv"]["format"].reason == "not_declared"


def test_reader_never_reinterprets_another_version():
    meta = {"output_types_vocab": "0" * 64, "output_types": {"outputs/a.tsv": {"data_type": "raw_counts"}}}
    assert read(meta)["outputs/a.tsv"]["data_type"].reason == "vocab_changed"
    records = {"outputs/a.tsv": {"v": 1, "vocab": "0" * 64, "data_type": {"value": "raw_counts", "basis": "declared",
                                                                          "source": "plan"}}}
    assert read({}, {"output_types": records})["outputs/a.tsv"]["data_type"].reason == "vocab_changed"
    assert read(meta, v=None)["outputs/a.tsv"]["data_type"].reason == "no_vocab"


def test_reader_marks_conflicts_and_malformed_records_unknown():
    meta = {"output_types_vocab": V.sha256, "output_types": {"outputs/a.tsv": {"data_type": "raw_counts"},
                                                             "a.tsv": {"data_type": "raw_counts"}}}
    assert read({**meta, "output_types": {"outputs/a.tsv": {"data_type": "raw_counts"}, "outputs/./a.tsv": {}}}
                )["outputs/a.tsv"]["data_type"].reason == "declaration_conflict"
    swapped = declare.runner_records(["outputs/a.tsv"], {**meta, "output_types": {"outputs/a.tsv": {"data_type": "table"}}}, V)
    assert read({**meta, "output_types": {"outputs/a.tsv": {"data_type": "raw_counts"}}},
                {"output_types": swapped})["outputs/a.tsv"]["data_type"].reason == "declaration_conflict"
    inferred_data = {"outputs/a.tsv": {"v": 1, "vocab": V.sha256,
                                       "data_type": {"value": "table", "basis": "inferred", "source": "extension"}}}
    assert read({}, {"output_types": inferred_data})["outputs/a.tsv"]["data_type"].reason == "invalid_declaration"
    assert read({}, {"output_types": {"outputs/a.tsv": "table"}})["outputs/a.tsv"]["format"].reason == "invalid_declaration"


def test_reader_judges_legacy_strings_once_against_the_local_vocabulary():
    fields = read({"output_types": {"outputs/a.tsv": "raw_counts", "outputs/b.tsv": "nonsense"}})
    assert fields["outputs/a.tsv"]["data_type"].legacy
    assert fields["outputs/a.tsv"]["data_type"].value == "raw_counts"
    assert fields["outputs/b.tsv"]["data_type"].reason == "not_declared"


def test_inferred_runner_record_cannot_override_a_plan_declaration():
    meta = {"output_types_vocab": V.sha256, "output_types": {"outputs/a.csv": {"format": "tsv"}}}
    inferred = declare.runner_records(
        ["outputs/a.csv"], {"output_types_vocab": V.sha256, "output_types": {}}, V)
    field = read(meta, {"output_types": inferred})["outputs/a.csv"]["format"]
    assert field.basis == "unknown" and field.reason == "declaration_conflict"


def test_staff_declarations_count_only_under_the_dispatch_version_and_conflicts_are_unknown():
    meta = {"output_types_vocab": V.sha256, "output_types": {"outputs/a.tsv": {"data_type": "raw_counts"}}}
    staff = {"outputs/a.tsv": {"data_type": "normalized_counts"}, "outputs/b.tsv": {"data_type": "table"}}
    fields = read(meta, staff=staff)
    assert fields["outputs/a.tsv"]["data_type"].reason == "declaration_conflict"
    assert (fields["outputs/b.tsv"]["data_type"].value, fields["outputs/b.tsv"]["data_type"].source) == ("table", "staff")
    assert read({}, staff=staff)["outputs/b.tsv"]["data_type"].reason == "vocab_changed"
    assert read({"output_types_vocab": V.sha256}, staff=staff, v=None)["outputs/b.tsv"]["data_type"].reason == "no_vocab"


# ---------------------------------------------------------------- review follow-ups (one cap, staff duplicates)

def test_one_entry_cap_for_the_normalizer_and_the_research_contract():
    from labhq.research.contract import OutputTypeEntry, ResearchStep

    outputs = [f"outputs/o{i}.tsv" for i in range(declare.MAX_ENTRIES + 1)]
    raw = [{"name": o, "format": "tsv"} for o in outputs]
    assert declare.normalize_entries(outputs, raw, V) == ([], {"too_many": 1})
    entries, issues = declare.normalize_entries(outputs[:-1], raw[:-1], V)
    assert len(entries) == declare.MAX_ENTRIES and issues == {}
    step = {"id": "s1", "agent_id": "worker", "instruction": "x", "phase": "analysis", "claim_ids": [],
            "input_refs": [], "outputs": outputs[:-1], "checks": ["qc"], "evidence_slots": [], "depends_on": [],
            "output_types": entries}
    assert len(ResearchStep.model_validate(step).output_types) == declare.MAX_ENTRIES
    assert OutputTypeEntry.model_fields["name"].metadata[-1].max_length == declare.MAX_NAME


class Ref:
    def __init__(self, path, data_type=None, format=None):
        self.path, self.data_type, self.format = path, data_type, format


def test_staff_duplicates_for_one_file_conflict_in_any_order():
    refs = [Ref("outputs/a.tsv", "raw_counts"), Ref("./outputs/a.tsv", "normalized_counts"),
            Ref("outputs/b.tsv", "table", "tsv"), Ref("outputs/b.tsv", "table")]
    outcomes = {json.dumps(declare.staff_declarations(order, normalize_artifact_path), sort_keys=True)
                for order in itertools.permutations(refs)}
    assert len(outcomes) == 1
    staff = declare.staff_declarations(refs, normalize_artifact_path)
    assert staff["outputs/a.tsv"]["data_type"] == declare.CONFLICT and staff["outputs/b.tsv"]["format"] == "tsv"
    assert staff["outputs/b.tsv"]["data_type"] == declare.CONFLICT  # the same value twice is still two declarations
    fields = read({"output_types_vocab": V.sha256}, staff=staff)
    assert fields["outputs/a.tsv"]["data_type"].reason == "declaration_conflict"
    assert fields["outputs/b.tsv"]["format"].value == "tsv"


# ---------------------------------------------------------------- declarations change no execution decision

TYPE_META = {"output_types", "output_types_vocab"}


def dispatched(hub):
    """What execution depends on: who runs what, with which prompt, schema, session, folders and outputs."""
    return [(t.agent_id, t.prompt, json.dumps(t.output_schema, sort_keys=True), t.resume_session_id,
             json.dumps({k: v for k, v in t.meta.items() if k not in TYPE_META}, sort_keys=True, default=str))
            for t in hub.calls if t.meta["kind"] != "plan"]


def outcome(hub):
    req = hub.requests["r"]
    steps = [{k: v for k, v in s.items() if k != "output_types"} for s in req["plan"]["steps"]]
    warnings = [w for w in req["plan"]["warnings"] if "output_types" not in w]
    return (req["status"], req.get("report"), steps, warnings, hub.approvals,
            {sid: {k: v for k, v in r.items() if k not in ("output_types", "task_id")}  # task ids are random
             for sid, r in (req.get("results") or {}).items()})


@pytest.mark.asyncio
async def test_declaring_types_changes_no_dispatch_approval_or_result():
    plain = json.loads(PENGUINS_PLAN.read_text(encoding="utf-8"))["plan"]
    runs = []
    for declare_on, plan in ((False, plain), (True, plain), (True, declared_penguins())):
        hub = penguins_hub([plan])
        hub.s.plan.declare_output_types = declare_on
        await Orchestrator(hub).run_request("r")
        runs.append(hub)
    assert all(h.requests["r"]["status"] == "done" for h in runs)
    assert dispatched(runs[0]) == dispatched(runs[1]) == dispatched(runs[2])
    assert outcome(runs[0]) == outcome(runs[1]) == outcome(runs[2])
    assert any("output_types" in t.meta for t in runs[2].calls)  # the declarations did travel
