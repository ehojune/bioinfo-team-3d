"""Statistics and review rules in the core staff prompts (agents/core, #84)."""

from pathlib import Path

import yaml

from labhq.models import AgentSpec

ROOT = Path(__file__).resolve().parents[1]


def _prompt(agent_id: str) -> str:
    raw = yaml.safe_load((ROOT / "agents" / "core" / f"{agent_id}.yaml").read_text(encoding="utf-8"))
    return " ".join(AgentSpec.model_validate(raw).system_prompt.split())  # YAML line wraps fold to single spaces


def test_analyst_models_donor_structure_fixes_seeds_and_writes_associations():
    text = _prompt("analyst")
    assert "same subject or donor" in text and "paired tests, mixed models or pseudobulk per donor" in text
    assert "Fix the random seed of every stochastic step" in text and "record each seed" in text
    assert "as associations, not causes" in text
    assert text.count("multiple-testing correction") == 1  # already asked for; not repeated


def test_qc_reviewer_flags_the_same_statistical_faults():
    text = _prompt("qc_reviewer")
    assert "same subject or donor treated as independent" in text and "paired tests, mixed models or pseudobulk" in text
    assert "without a fixed, recorded seed" in text and "associations stated as causes" in text
    assert "without multiple-testing correction or effect sizes" in text
    assert "fix and record the seed of any random step you run yourself" in text


def test_sci_reviewer_checks_overreach_in_any_review_and_quotes_its_evidence():
    text = _prompt("sci_reviewer")
    assert "In every review, whatever its format" in text
    assert "overgeneralization" in text and "cherry-picking" in text and "speculation written as fact" in text
    assert "quote the exact sentence" in text and "evidence_quote" in text
