"""Semantics pilot (#127): fixed expected answers, model B, baseline A and their isolation."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from scripts import semantics_pilot as pilot

FIXTURE = Path(__file__).parent / "fixtures" / "semantics"
RECORDS = FIXTURE / "records"

# Fixed after the one independent review and correction 1. Changing either is a pilot correction
# (a separate commit with its reason) or an experiment failure, never a routine update.
EXPECTED_SHA256 = "e8f094595f3e4c19056df37ac2ee54425c8d5d20499e312e6f59671e05671c02"
FIXTURE_SHA256 = "24dce1ed83a06b78d000606111a785e6eb388e0c39c262ecad4442b1700cafeb"


def inventory_sha256(root: Path) -> str:
    """sha256 over 'relative posix path LF file sha256 LF' for every file under root, in path order."""
    digest = hashlib.sha256()
    for path in sorted((p for p in root.rglob("*") if p.is_file()), key=lambda p: p.relative_to(root).as_posix()):
        digest.update(f"{path.relative_to(root).as_posix()}\n{hashlib.sha256(path.read_bytes()).hexdigest()}\n"
                      .encode())
    return digest.hexdigest()


def test_expected_answers_are_the_pinned_version():
    assert hashlib.sha256((FIXTURE / "expected.yaml").read_bytes()).hexdigest() == EXPECTED_SHA256


def test_fixture_records_are_the_pinned_version():
    assert inventory_sha256(RECORDS) == FIXTURE_SHA256


# ---------------------------------------------------------------- model B


MODEL_SHA256 = "7e26aa271226c7027e606346f995154ead095ce1c105d5907ec2a678a59ca909"


@pytest.fixture(scope="module")
def expected():
    return pilot.load_expected()


@pytest.fixture(scope="module")
def fixture_paths():
    with pilot.fixture_copy() as paths:
        yield paths


def outputs_of(name, fixture_paths, expected):
    impl = pilot.IMPLS[name]()
    records = fixture_paths.read(overlay=impl.state == "change2")
    return impl, pilot.run_queries(impl, impl.project(records), expected)


def test_model_file_is_the_pinned_version():
    from labhq.research.semantics import load_model
    model = load_model()
    assert (model.name, model.sha256) == ("labhq.provenance@1", MODEL_SHA256)


@pytest.mark.parametrize("edit, message", [
    (lambda s: s.replace("model: labhq.provenance\n", "model: labhq.provenance\nmodel: again\n"), "duplicate key"),
    (lambda s: s.replace("  probe_not_validation:", "  probe_not_validated:"), "without a judge"),
    (lambda s: s.replace("judge: judge_hash,", "judge: judge_hashes,"), "names judge_hashes"),
    (lambda s: s.replace("from: run, to: [method]", "from: run, to: [procedure]"), "undeclared concept procedure"),
    (lambda s: s.replace("  inherit_broader: false", "  inherit_broader: true"), "inherit_broader"),
])
def test_model_load_rejects_bad_models(tmp_path, edit, message):
    from labhq.research.semantics import MODEL_PATH, ModelError, load_model
    text = MODEL_PATH.read_text(encoding="utf-8")
    changed = edit(text)
    assert changed != text
    path = tmp_path / "semantics_v1.yaml"
    path.write_text(changed, encoding="utf-8")
    with pytest.raises(ModelError, match=message):
        load_model(path)


def test_model_rejects_an_edge_the_model_does_not_declare(fixture_paths):
    from labhq.research.semantics import ModelError, load_model, project
    p = project(load_model(), fixture_paths.read(overlay=False))
    with pytest.raises(ModelError, match="does not connect"):
        p.add_edge("ev:ws/t/E1", "used", "art:r/ws/x.tsv", "declared")
    with pytest.raises(ModelError, match="basis"):
        p.add_edge("run:ws/t", "used", "art:r/ws/x.tsv", "reported")


@pytest.mark.parametrize("qid", [f"q{n:02d}" for n in range(1, 18)])
def test_model_b_answers(qid, fixture_paths, expected):
    impl, outputs = outputs_of("B", fixture_paths, expected)
    scored = pilot.score_queries(expected, impl.state, outputs)
    assert scored[qid]["ok"], scored[qid]["diff"]


def test_model_b_answers_are_advisory(fixture_paths):
    from labhq.research.semantics import audit_lineage, find_reusable, load_model, project
    model = load_model()
    p = project(model, fixture_paths.read(overlay=False))
    for answer in (find_reusable(p, key="outputs/scan_extra.tsv"),
                   audit_lineage(p, evidence="ev:task_q3scan_ag_seqtool/task_q3scan/E4")):
        assert (answer.status, answer.model, answer.model_sha256) == ("advisory", "labhq.provenance@1", MODEL_SHA256)
    a8 = find_reusable(p, key="outputs/scan_extra.tsv").result["candidates"]
    assert [c["recommend"] for c in a8.values()] == [False]
