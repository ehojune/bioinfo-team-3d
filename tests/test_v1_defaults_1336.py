"""v1.0 keeps the ontology and Paper2Agent hiring off by default; the code stays and an explicit setting turns it on (#1336)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from labhq.settings import RecruitSettings, ResearchSettings, Settings

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = (ROOT / "config" / "labhq.example.yaml", ROOT / "labhq" / "config" / "labhq.example.yaml")


def test_settings_without_the_keys_leave_both_off():
    settings = Settings()
    assert settings.recruit.enabled is False
    assert settings.research.semantic_report is False
    assert settings.semantics in (None, "off", False)


@pytest.mark.parametrize("template", TEMPLATES, ids=["config", "packaged"])
def test_example_configs_ship_both_off(template):
    data = yaml.safe_load(template.read_text(encoding="utf-8"))
    assert data["recruit"]["enabled"] is False
    assert data["research"]["semantic_report"] is False
    assert data["semantics"]["mode"] == "off"


def test_explicit_settings_still_turn_them_on():
    assert RecruitSettings(enabled=True).enabled is True
    assert ResearchSettings(semantic_report=True).semantic_report is True
    assert Settings(semantics={"mode": "ab"}).semantics == {"mode": "ab"}
