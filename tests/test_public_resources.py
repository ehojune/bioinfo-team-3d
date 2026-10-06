"""PI 2026-10-06 (#435 C): every plan gets a public resource reference that does not limit the planner."""

from labhq.orchestrator.cso import planning_guidance
from labhq.vocab import public_resources


def test_packaged_resource_table_loads_and_groups_by_result_kind():
    rows = public_resources.load()
    assert {"Ensembl VEP", "gnomAD", "ClinVar", "AlphaGenome", "ChIP-Atlas", "ENCODE", "GTEx"} <= {
        row["resource"] for row in rows}
    rule = public_resources.prompt_rule(rows)
    assert "variants: Ensembl VEP, gnomAD, ClinVar" in rule and "regions: ChIP-Atlas, ENCODE" in rule
    assert "not a limit" in rule and "COSMIC" in rule and "DisGeNET" in rule
    assert "release or version" in rule


def test_planning_guidance_carries_resources_with_and_without_a_checklist():
    alone = planning_guidance({}, None)
    assert "Public resources" in alone
    assert "Public resources" in planning_guidance({}, {"status": "none"})


def test_a_bad_table_turns_the_rule_off_instead_of_breaking_planning(tmp_path):
    bad = tmp_path / "public_resources.tsv"
    bad.write_text("kind\tname\n", encoding="utf-8")
    assert public_resources.load(bad) == [] and public_resources.prompt_rule([]) == ""
    assert public_resources.load(tmp_path / "missing.tsv") == []
