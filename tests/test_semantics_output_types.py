"""The provenance model reads typed output declarations (#221 todo 2) through the one core reader."""

import pytest

from labhq import vocab as output_vocab
from labhq.research import semantics as sem
from labhq.vocab import declare

V = output_vocab.current()
MODEL = sem.load_model()


def rows(meta=None, records=None, *, outputs=("outputs/a.tsv",), extra=None):
    task = {"request_id": "req_a", "step_id": "s1", "kind": "step", "attempt": 1, "revision": 0,
            "payload": {"id": "task_a1", "agent_id": "analyst", "meta": {"outputs": list(outputs), **(meta or {})}},
            "result": {"task_id": "task_a1", "agent_id": "analyst", "ok": True, "workdir": "/w/task_a1_analyst",
                       "workdir_id": "task_a1_analyst", "outputs": list(outputs),
                       **({"output_types": records} if records is not None else {})}}
    tasks = {"task_a1": task, **(extra or {})}
    records_, invalid = sem.records_from_rows({"req_a": {"id": "req_a", "project_id": "p1"}}, tasks)
    assert invalid == 0
    return sem.project(MODEL, records_, types_vocab=V)


def artifact(p):
    (row,) = [r for r in p.artifacts.values() if r["path"] == "outputs/a.tsv"]
    return row


def typed(decl, version=None):
    meta = {"output_types_vocab": version or V.sha256, "output_types": decl}
    return meta, declare.runner_records(["outputs/a.tsv"], meta, V)


def test_a_runner_record_types_the_artifact_and_draws_a_means_edge():
    p = rows(*typed({"outputs/a.tsv": {"data_type": "raw_counts"}}))
    row = artifact(p)
    assert row["data_type"] == "raw_counts" and "data_type" not in (row.get("unknown") or {})
    assert any(e["rel"] == "means" and e["dst"] == "type:raw_counts" for e in p.edges)
    assert row["_types"]["format"].value == "tsv" and row["_types"]["format"].basis == "inferred"


def test_meta_alone_counts_when_the_runner_predates_records():
    meta, _ = typed({"outputs/a.tsv": {"data_type": "de_table"}})
    assert artifact(rows(meta))["data_type"] == "de_table"


@pytest.mark.parametrize("meta_records, reason", [
    (typed({"outputs/a.tsv": {"data_type": "raw_counts"}}, "0" * 64), "vocab_changed"),
    ((typed({"outputs/a.tsv": {"data_type": "raw_counts"}})[0],
      typed({"outputs/a.tsv": {"data_type": "table"}})[1]), "declaration_conflict"),
    (({"output_types_vocab": V.sha256, "output_types": {"outputs/a.tsv": {"data_type": "nonsense"}}}, None),
     "invalid_declaration"),
    (({}, None), "not_declared"),
])
def test_unknown_types_carry_a_declared_reason(meta_records, reason):
    row = artifact(rows(*meta_records))
    assert row["data_type"] == sem.UNKNOWN and row["unknown"]["data_type"] == reason
    assert reason in MODEL.spec.reasons.unknown


def test_legacy_bare_keys_keep_the_model_s_own_vocabulary():
    assert artifact(rows({"output_types": {"outputs/a.tsv": "raw_counts"}}))["data_type"] == "raw_counts"
    legacy_outside = artifact(rows({"output_types": {"outputs/a.tsv": "table"}}))
    assert legacy_outside["data_type"] == sem.UNKNOWN and legacy_outside["unknown"]["data_type"] == "not_declared"


def test_without_a_vocabulary_typed_declarations_are_unknown():
    records_, _ = sem.records_from_rows({"req_a": {"id": "req_a"}}, {"task_a1": {
        "request_id": "req_a", "step_id": "s1", "payload": {"meta": typed({"outputs/a.tsv": {"data_type": "table"}})[0]},
        "result": {"ok": True, "workdir_id": "w1", "outputs": ["outputs/a.tsv"]}}})
    row = artifact(sem.project(MODEL, records_, types_vocab=None))
    assert row["unknown"]["data_type"] == "no_vocab"
