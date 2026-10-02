"""EDAM subset (#151): the committed table matches its pinned source, and the generator is safe and exact.

Parser cases run on small synthetic OWL text made here; the real EDAM.owl is never downloaded in CI.
"""

import hashlib
import socket
import urllib.request

import pytest
import yaml

from labhq import vocab
from scripts import edam_subset as es

E = "http://edamontology.org/"


def owl(classes: str, *, version: str = es.VERSION_INFO, license_: str = es.LICENSE, prolog: str = "") -> bytes:
    return (f'<?xml version="1.0"?>\n{prolog}'
            '<rdf:RDF xmlns="http://edamontology.org/" xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" '
            'xmlns:rdfs="http://www.w3.org/2000/01/rdf-schema#" xmlns:owl="http://www.w3.org/2002/07/owl#" '
            'xmlns:dcterms="http://purl.org/dc/terms/" xmlns:oboInOwl="http://www.geneontology.org/formats/oboInOwl#">'
            f'<owl:Ontology rdf:about="http://edamontology.org"><owl:versionInfo>{version}</owl:versionInfo>'
            f'<dcterms:license rdf:resource="{license_}"/></owl:Ontology>{classes}</rdf:RDF>').encode("utf-8")


def cls(local: str, label: str | None = None, *, parents=(), synonyms=(), deprecated=False, extra="") -> str:
    body = "".join(f'<rdfs:subClassOf rdf:resource="{E}{p}"/>' for p in parents)
    body += "".join(f"<oboInOwl:hasExactSynonym>{s}</oboInOwl:hasExactSynonym>" for s in synonyms)
    body += f"<rdfs:label>{label}</rdfs:label>" if label else ""
    body += '<owl:deprecated rdf:datatype="http://www.w3.org/2001/XMLSchema#boolean">true</owl:deprecated>' if deprecated else ""
    return f'<owl:Class rdf:about="{E}{local}">{body}{extra}</owl:Class>'


ROOTS = cls("data_9000", "Data") + cls("format_9000", "Format") + cls("operation_9000", "Operation")


def resolve(classes: str, branch: str, label: str | None) -> dict:
    return es.resolve(es.parse_owl(owl(ROOTS + classes)), branch, label)


# ---------------------------------------------------------------- the committed subset

def committed():
    return yaml.safe_load(es.SUBSET.read_text(encoding="utf-8"))


def test_the_committed_subset_names_its_pinned_source_and_current_map():
    subset = committed()
    assert subset["source"] == {"url": es.URL, "release": es.RELEASE, "version_info": es.VERSION_INFO,
                                "sha256": es.SHA256, "license": es.LICENSE}
    assert subset["generator"]["map_sha256"] == es.load_map()[1]  # map edited without a rerun fails here
    assert set(subset["terms"]) == set(es.load_map()[0]) == set(es.local_branches())


def test_the_committed_subset_stays_under_the_caps_and_loads():
    subset = committed()
    ids = {t["id"] for t in subset["terms"].values() if t["id"] != "unknown"}
    assert subset["counts"] == {"keys": len(subset["terms"]), "edam_terms": len(ids),
                                "unknown": sum(1 for t in subset["terms"].values() if t["id"] == "unknown")}
    assert subset["counts"]["keys"] <= 40 and subset["counts"]["edam_terms"] <= 40
    v = vocab.load()
    assert v.edam_problem is None and v.edam_ids == frozenset(ids)
    assert all(t["id"].startswith(t["branch"] + "_") for t in subset["terms"].values() if t["id"] != "unknown")


def test_the_map_holds_candidate_labels_only():
    candidates, _ = es.load_map()
    assert all(v is None or (isinstance(v, str) and not v.startswith(("data_", "format_", "operation_")))
               for v in candidates.values())


# ---------------------------------------------------------------- one rule per key

def test_an_exact_label_or_synonym_of_one_live_term_gives_its_id_and_ancestors():
    classes = (cls("data_9001", "Count matrix", parents=["data_9000"]) +
               cls("data_9002", "Other", parents=["data_9000"], synonyms=["Matrix of counts"]))
    assert resolve(classes, "data", "Count matrix") == {"branch": "data", "id": "data_9001", "label": "Count matrix",
                                                        "ancestors": ["data_9000"]}
    assert resolve(classes, "data", "Matrix of counts")["id"] == "data_9002"
    assert resolve(classes, "data", "count matrix") == {"branch": "data", "id": "unknown", "reason": "no_match",
                                                        "candidate": "count matrix"}


DEPRECATED_CLASS = "http://www.w3.org/2002/07/owl#DeprecatedClass"


@pytest.mark.parametrize("classes, label, reason", [
    (cls("data_9001", "Twin", parents=["data_9000"]) + cls("data_9002", "Twin", parents=["data_9000"]), "Twin",
     "ambiguous"),
    (cls("data_9001", "Gone", parents=["data_9000"], deprecated=True), "Gone", "deprecated"),
    (cls("data_9001", "Gone", parents=["data_9000"]).replace(f"{E}data_9000", DEPRECATED_CLASS), "Gone", "deprecated"),
    (cls("data_9001", "Gone", parents=["data_9000"], extra=f'<oboInOwl:inSubset rdf:resource="{E}obsolete"/>'),
     "Gone", "deprecated"),
    (cls("format_9001", "Elsewhere", parents=["format_9000"]), "Elsewhere", "wrong_branch"),
    (cls("data_9001", "Orphan", parents=["data_9999"]), "Orphan", "ancestor_missing"),
    (cls("data_9001", "Loop", parents=["data_9002"]) + cls("data_9002", "Loop2", parents=["data_9001"]), "Loop",
     "ancestor_cycle"),
    (cls("data_9001", "Crossed", parents=["format_9000"]), "Crossed", "wrong_branch"),
])
def test_anything_but_one_live_term_in_the_branch_stays_local(classes, label, reason):
    assert resolve(classes, "data", label) == {"branch": "data", "id": "unknown", "reason": reason, "candidate": label}


def test_a_deprecated_twin_does_not_block_the_live_term():
    classes = (cls("operation_9001", "DE analysis", parents=["operation_9000"], deprecated=True) +
               cls("operation_9002", "DE profiling", parents=["operation_9000"], synonyms=["DE analysis"]))
    assert resolve(classes, "operation", "DE analysis")["id"] == "operation_9002"


def test_several_parents_are_all_recorded_and_a_shared_ancestor_once():
    classes = (cls("data_9001", "A", parents=["data_9000"]) + cls("data_9002", "B", parents=["data_9000"]) +
               cls("data_9003", "C", parents=["data_9001", "data_9002"]))
    assert resolve(classes, "data", "C")["ancestors"] == ["data_9000", "data_9001", "data_9002"]


def test_a_chain_past_the_depth_limit_stays_local(monkeypatch):
    monkeypatch.setattr(es, "MAX_DEPTH", 2)
    classes = "".join(cls(f"data_90{i:02d}", f"L{i}", parents=[f"data_90{i - 1:02d}" if i > 1 else "data_9000"])
                      for i in range(1, 5))
    assert resolve(classes, "data", "L4")["reason"] == "ancestor_limit"
    assert resolve(classes, "data", "L1")["id"] == "data_9001"


def test_no_candidate_is_its_own_reason():
    assert resolve("", "data", None) == {"branch": "data", "id": "unknown", "reason": "no_candidate",
                                         "candidate": None}


def test_classes_nested_in_other_axioms_or_without_an_iri_are_not_terms():
    nested = (f'<owl:Class rdf:about="{E}data_9001"><rdfs:subClassOf rdf:resource="{E}data_9000"/>'
              '<rdfs:subClassOf><owl:Restriction><owl:someValuesFrom>'
              f'<owl:Class rdf:about="{E}data_9005"><rdfs:label>Hidden</rdfs:label></owl:Class>'
              '</owl:someValuesFrom></owl:Restriction></rdfs:subClassOf><rdfs:label>Shown</rdfs:label></owl:Class>'
              '<owl:Class><rdfs:label>Anonymous</rdfs:label></owl:Class>')
    onto = es.parse_owl(owl(ROOTS + nested))
    assert f"{E}data_9005" not in onto.terms and onto.terms[f"{E}data_9001"].parents == [f"{E}data_9000"]
    assert es.resolve(onto, "data", "Hidden")["reason"] == "no_match"
    assert es.resolve(onto, "data", "Anonymous")["reason"] == "no_match"


# ---------------------------------------------------------------- input safety

@pytest.mark.parametrize("prolog", [
    '<!DOCTYPE rdf:RDF [<!ENTITY owl "http://www.w3.org/2002/07/owl#">]>\n',
    '<!DOCTYPE rdf:RDF SYSTEM "http://example.invalid/evil.dtd">\n',
    '<!DOCTYPE rdf:RDF [<!ENTITY a "x"><!ENTITY b "&a;&a;&a;&a;">]>\n',
])
def test_doctype_and_entities_are_refused_before_anything_is_followed(prolog, monkeypatch):
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: pytest.fail("network"))
    with pytest.raises(es.SubsetError, match="DOCTYPE or an entity"):
        es.parse_owl(owl(ROOTS, prolog=prolog))


def test_broken_xml_and_oversized_input_are_refused(monkeypatch):
    with pytest.raises(es.SubsetError, match="not well-formed"):
        es.parse_owl(owl(ROOTS)[:-20])
    monkeypatch.setattr(es, "MAX_BYTES", 100)
    with pytest.raises(es.SubsetError, match="larger than"):
        es.parse_owl(owl(ROOTS))


def fixture_inputs(folder, *, crlf=False):
    folder.mkdir(parents=True, exist_ok=True)
    local = folder / "output_types.yaml"
    local.write_text("version: 1\nterms:\n  counts: {branch: data, definition: x}\n"
                     "  tsv: {branch: format, definition: y, extensions: ['.tsv']}\n", encoding="utf-8")
    text = "# candidates\nversion: 1\ncandidates:\n  counts: Count matrix\n  tsv: null\n"
    mapping = folder / "edam_map.yaml"
    mapping.write_bytes(text.replace("\n", "\r\n").encode() if crlf else text.encode())
    data = owl(ROOTS + cls("data_9001", "Count matrix", parents=["data_9000"]))
    return data, hashlib.sha256(data).hexdigest(), mapping, local


def test_generation_is_offline_deterministic_and_has_no_timestamp(tmp_path, monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("no network")

    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(urllib.request, "urlopen", blocked)
    data, sha, mapping, local = fixture_inputs(tmp_path)
    first = es.build(data, source_sha=sha, expect_sha=sha, map_path=mapping, local_path=local)
    again = es.build(data, source_sha=sha, expect_sha=sha, map_path=mapping, local_path=local)
    _, _, mapping_crlf, _ = fixture_inputs(tmp_path / "crlf", crlf=True)  # same meaning, other bytes
    crlf = es.build(data, source_sha=sha, expect_sha=sha, map_path=mapping_crlf, local_path=local)
    assert first == again == crlf
    body = yaml.safe_load(first)
    assert body["terms"]["counts"]["id"] == "data_9001" and body["terms"]["tsv"]["reason"] == "no_candidate"
    assert "generated_at" not in first and "time" not in body["generator"]


@pytest.mark.parametrize("change, match", [
    (dict(expect_sha="0" * 64), "SHA-256 differs"),
    (dict(owl_version="1.24"), "versionInfo"),
    (dict(owl_license="https://example.invalid/license"), "license"),
])
def test_another_file_than_the_pinned_release_is_refused(tmp_path, change, match):
    data, sha, mapping, local = fixture_inputs(tmp_path)
    if "owl_version" in change or "owl_license" in change:
        data = owl(ROOTS, version=change.get("owl_version", es.VERSION_INFO),
                   license_=change.get("owl_license", es.LICENSE))
        sha = hashlib.sha256(data).hexdigest()
    with pytest.raises(es.SubsetError, match=match):
        es.build(data, source_sha=sha, expect_sha=change.get("expect_sha", sha), map_path=mapping, local_path=local)


def test_map_keys_must_equal_the_local_keys(tmp_path):
    data, sha, mapping, local = fixture_inputs(tmp_path)
    mapping.write_text("version: 1\ncandidates:\n  counts: Count matrix\n", encoding="utf-8")
    with pytest.raises(es.SubsetError, match="keys differ"):
        es.build(data, source_sha=sha, expect_sha=sha, map_path=mapping, local_path=local)


def test_the_cli_reports_a_missing_file_without_a_traceback(tmp_path, capsys):
    assert es.main(["--owl", str(tmp_path / "absent.owl"), "--check"]) == 1
    err = capsys.readouterr().err
    assert "cannot read the release file" in err and str(tmp_path) not in err
