from pathlib import Path

import yaml
import pytest

from labhq import cli
from labhq.models import AgentSpec
from labhq.settings import Settings


ROOT = Path(__file__).parents[1]


def _fail(name, calls):
    def failed(*args, **kwargs):
        calls.append(name)
        raise AssertionError(f"dry-run called {name}")
    return failed


def test_send_dry_run_calls_no_adapter_or_network(tmp_path, monkeypatch, capsys):
    config = tmp_path / "labhq.yaml"
    config.write_text(yaml.safe_dump({
        "runner": {
            "agents_dir": str(ROOT / "agents"),
            "talent_dir": str(tmp_path / "talent"),
        },
    }), encoding="utf-8")
    calls = []
    monkeypatch.setattr("labhq.adapters.get_adapter", _fail("adapter", calls))
    monkeypatch.setattr(cli, "_api", _fail("network", calls))
    monkeypatch.setattr(cli, "_run_websocket", _fail("websocket", calls))

    cli.main(["-c", str(config), "send", "공개 데이터 분석", "--dry-run"])

    output = capsys.readouterr().out
    assert calls == []
    assert "모델·도구·네트워크 호출 없음" in output
    assert "CSO 계획" in output
    assert "계획 이후" in output and "미정" in output
    assert "비용 모름" in output


def test_missing_model_tier_price_stays_unknown():
    from labhq.dry_run import estimate_call

    settings = Settings()
    agent = AgentSpec(id="cso", name="CSO", role="plan", model_tier="reasoning")

    estimate = estimate_call(settings, agent, stage="CSO 계획")

    assert estimate["calls"] == 1
    assert estimate["cost_range_usd"] is None
    assert estimate["cost_status"] == "unknown"


def test_model_tier_price_needs_provenance_and_uses_the_configured_range():
    from labhq.dry_run import estimate_call

    with pytest.raises(ValueError, match="source and checked_on"):
        Settings.model_validate({"model_tier_prices": {"reasoning": {"min_usd": 1, "max_usd": 2}}})
    settings = Settings.model_validate({"model_tier_prices": {
        "source": "https://example.org/official-pricing",
        "checked_on": "2026-10-15",
        "reasoning": {"min_usd": 1, "max_usd": 2},
    }})
    agent = AgentSpec(id="cso", name="CSO", role="plan", model_tier="reasoning")

    estimate = estimate_call(settings, agent, stage="CSO 계획")

    assert estimate["cost_status"] == "known"
    assert estimate["cost_range_usd"] == [1.0, 2.0]
