"""#621 C: the variant_effect topic and its checklist. Each test fails on main, where the topic does not exist."""

from pathlib import Path

import yaml

from labhq import vocab
from labhq.vocab import topic_checklists, topics
from scripts import edam_subset as es
from scripts import vocab_tables

REPO = Path(__file__).resolve().parents[1]
KEY = "variant_effect"
IDS = ["population_frequency", "clinical_assertions", "consequence_annotation", "missense_prediction",
       "regulatory_splicing", "concordance_limits"]


def test_the_topic_is_approved_and_listed_in_the_cso_prompt():
    terms = vocab.load()
    assert terms.is_key("topic", KEY) and terms.terms[KEY].branch == "topic"
    rule = topics.prompt_rule(terms)
    assert f"{KEY} — interpreting the likely effect of specific variants (missense, regulatory, splicing)" in rule
    assert KEY in topics.prompt_rule(terms, include_definitions=False)  # the research prompt lists keys only
    assert topics.normalize([KEY, "gwas"], terms) == (sorted([KEY, "gwas"]), [])


def test_the_checklist_has_four_to_six_checks_each_with_a_why():
    loaded = topic_checklists.load(vocab=vocab.load())
    items = loaded[KEY]
    assert 4 <= len(items) <= 6 and [item.id for item in items] == IDS
    assert all(item.check and item.why and item.topic == KEY for item in items)
    text = " ".join(item.check for item in items)
    for resource in ("gnomAD", "ClinVar", "VEP", "MANE", "AlphaMissense", "AlphaGenome", "SpliceAI", "ACMG"):
        assert resource in text, resource
    by_id = {item.id: item.check for item in items}
    assert "임상 근거가 아니" in by_id["concordance_limits"] and "요청이 있을 때만" in by_id["concordance_limits"]


def test_declaring_the_topic_makes_every_check_required_and_other_topics_do_not():
    loaded = topic_checklists.load(vocab=vocab.load())
    required = topic_checklists.requirements([KEY], loaded)
    assert [item.id for item in required] == IDS
    errors = topic_checklists.answer_errors({}, required, ["A"])
    assert len(errors) == len(IDS) and all(f"checklist.{item}" in " ".join(errors) for item in IDS)
    answers = {item: "step:A" for item in IDS[:-1]}
    answers["concordance_limits"] = "not_applicable: 변이 하나뿐이라 서로 비교할 증거가 없다"
    assert topic_checklists.answer_errors(answers, required, ["A"]) == []
    assert topic_checklists.requirements(["gwas", "structural_variant_calling"], loaded) \
        and not {item.id for item in topic_checklists.requirements(["gwas"], loaded)} & set(IDS)


def test_its_check_ids_are_not_shared_with_another_topic():
    # A shared id joins the checks of every declared topic into one answer (#586, #592); this topic's checks are its own.
    loaded = topic_checklists.load(vocab=vocab.load())
    others = {item.id for topic, items in loaded.items() if topic != KEY for item in items}
    assert [item.id for item in loaded[KEY]] == IDS and not set(IDS) & others


def test_the_plan_reads_the_checks_from_the_catalog_table():
    loaded = topic_checklists.load(vocab=vocab.load())
    rows = topic_checklists.workspace_files(loaded)[topic_checklists.CHECKLIST_TSV].splitlines()
    ours = [row.split("\t") for row in rows if row.startswith(f"{KEY}\t")]
    assert [row[1] for row in ours] == IDS and all(len(row) == 4 and row[2] and row[3] for row in ours)


def test_edam_map_and_subset_keep_the_topic_local_and_match_each_other():
    candidates, map_sha = es.load_map()
    assert candidates[KEY] is None  # no single EDAM topic fits, like germline_wgs_wes
    subset = yaml.safe_load(es.SUBSET.read_text(encoding="utf-8"))
    assert subset["terms"][KEY] == {"branch": "topic", "id": "unknown", "reason": "no_candidate", "candidate": None}
    assert subset["generator"]["map_sha256"] == map_sha
    assert set(subset["terms"]) == set(candidates) == set(es.local_branches())
    terms = vocab.load()
    assert terms.edam_problem is None and terms.terms[KEY].edam_id is None


def test_the_vocabulary_table_and_sources_table_carry_the_topic():
    text = vocab_tables.render()
    assert f"| `{KEY}` |" in text and f"### {KEY}" in text and "| `missense_prediction` |" in text
    committed = (REPO / "docs" / "vocabulary.md").read_text(encoding="utf-8").replace("\r\n", "\n")
    assert committed == text
    sources = (REPO / "docs" / "reference" / "topic_checklists_sources.md").read_text(encoding="utf-8")
    row = next(line for line in sources.splitlines() if line.startswith(f"| {KEY} |"))
    assert "PMID 37733863" in row and "PMID 41606153" in row  # AlphaMissense and AlphaGenome papers
