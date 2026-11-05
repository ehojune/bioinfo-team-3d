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


# --- #625: a tier names a model only where the agent names none, and a mismatch is never silent ---

def _tiers(**rows):
    return Settings.model_validate({"model_tiers": rows})


def test_model_tiers_default_is_empty_and_validated():
    assert Settings().model_tiers.model_for("fast", "claude_code") is None
    with pytest.raises(ValueError, match="unknown engine"):
        _tiers(fast={"nope": "haiku"})
    with pytest.raises(ValueError, match="not a valid model name"):
        _tiers(fast={"claude_code": "--dangerous"})
    assert _tiers(fast={"claude_code": "haiku"}).model_tiers.model_for("fast", "claude_code") == "haiku"
    assert _tiers(fast={"claude_code": "haiku"}).model_tiers.model_for("fast", "codex") is None


@pytest.mark.parametrize("declared, actual, same", [
    ("opus", "claude-opus-5-5", True),            # alias and full name
    ("claude-opus-5-5", "opus", True),
    ("Opus", "claude-opus-4.1", True),            # case and a dotted version
    ("haiku", "claude-haiku-5-5", True),
    ("sonnet", "claude-opus-5-5", False),         # another model
    ("gpt-6", "gpt-6-luna", False),               # a prefix that is another model is not a match
    ("gpt-6.1-sol", "gpt-6.1-sol", True),
    ("gpt-6.1-sol", "gpt-6-sol", False),
    (None, "opus", False),
    ("opus", None, False),
])
def test_models_match_compares_an_alias_with_a_full_name(declared, actual, same):
    from labhq.settings import models_match

    assert models_match(declared, actual) is same


def test_dry_run_shows_the_model_a_tier_means_or_says_it_is_unset():
    from labhq.dry_run import estimate_call, render_preview

    agent = AgentSpec(id="scout", name="Scout", role="r", model_tier="fast")
    assert estimate_call(Settings(), agent, stage="조사")["model"] is None
    tiered = _tiers(fast={"claude_code": "haiku"})
    assert estimate_call(tiered, agent, stage="조사")["model"] == "haiku"
    named = AgentSpec(id="scout", name="Scout", role="r", model="opus", model_tier="fast")
    assert estimate_call(tiered, named, stage="조사")["model"] == "opus"  # the agent's own model wins
    preview = {"calls": [estimate_call(Settings(), agent, stage="조사"), estimate_call(tiered, agent, stage="조사")],
               "known_cost_range_usd": [0, 0], "unknown_cost_calls": 2, "future": "x", "future_label": "이후",
               "future_unknown": False}
    text = render_preview(preview)
    assert "(fast·모델 미정)" in text and "(fast·haiku)" in text


def _runner_agent(settings, agent, **meta):
    from labhq.models import Task
    from labhq.runner.daemon import Runner

    fake = type("FakeRunner", (), {"registry": type("Registry", (), {"get": lambda _self, _id: agent})(),
                                    "s": settings})()
    return Runner._resolve_agent(fake, Task(agent_id=agent.id, prompt="p", meta={"kind": "step", **meta}))


def test_runner_fills_the_model_from_the_tier_only_when_the_agent_names_none():
    tiered = _tiers(fast={"claude_code": "haiku"}, reasoning={"claude_code": "opus"})
    bare = AgentSpec(id="scout", name="Scout", role="r", model_tier="fast")
    assert _runner_agent(tiered, bare).model == "haiku"
    assert _runner_agent(Settings(), bare).model is None                       # empty table: nothing invented
    named = AgentSpec(id="scout", name="Scout", role="r", model="sonnet", model_tier="fast")
    assert _runner_agent(tiered, named).model == "sonnet"                       # an explicit model always wins
    other_engine = AgentSpec(id="scout", name="Scout", role="r", engine="codex", model_tier="fast")
    assert _runner_agent(tiered, other_engine).model is None                    # the table has no codex entry
    untiered = AgentSpec(id="scout", name="Scout", role="r")
    assert _runner_agent(tiered, untiered).model is None
    overridden = _runner_agent(tiered, bare, agent_overrides={"model": "opus"})
    assert overridden.model == "opus"                                           # a task's own override still wins


def _item(agent_id, tier, model, engine="claude_code"):
    return {"task_id": agent_id, "engine": engine, "model": model, "status": "actual", "usd": 1.0,
            "agent_id": agent_id, **({"model_tier": tier} if tier else {})}


def test_cost_item_records_the_declared_tier():
    from labhq.costs import task_cost_item

    result = {"task_id": "t1", "agent_id": "scout", "usage": {}, "cost_usd": 1.0, "cost_known": True}
    assert task_cost_item(result, {"id": "scout", "model_tier": "fast", "engine": "claude_code",
                                   "model": "haiku"})["model_tier"] == "fast"
    assert "model_tier" not in task_cost_item(result, {"id": "scout", "engine": "claude_code", "model": "haiku"})


def test_cost_summary_warns_when_a_declared_tier_ran_another_model():
    """② 6th (req_5cf7f22a1b): lit_scout (fast), qc_reviewer and chief_of_staff (balanced) all ran claude-opus-5-5."""
    from labhq.costs import aggregate_costs

    items = {"a": _item("analyst", "reasoning", "claude-opus-5-5"), "b": _item("lit_scout", "fast", "claude-opus-5-5"),
             "c": _item("qc_reviewer", "balanced", "claude-opus-5-5"), "d": _item("lit_scout", "fast", "claude-opus-5-5"),
             "e": _item("sci_reviewer", "reasoning", "gpt-6.1-sol", engine="codex"),
             "f": _item("old", None, "claude-opus-5-5")}
    tiers = _tiers(fast={"claude_code": "haiku"}, balanced={"claude_code": "sonnet"},
                   reasoning={"claude_code": "opus", "codex": "gpt-6-astra"}).model_tiers

    warnings = aggregate_costs(items, tiers)["warnings"]

    assert warnings == ["model_tier_mismatch:lit_scout:fast:claude-opus-5-5",
                        "model_tier_mismatch:qc_reviewer:balanced:claude-opus-5-5",
                        "model_tier_mismatch:sci_reviewer:reasoning:gpt-6.1-sol"]
    assert aggregate_costs(items)["warnings"] == []                              # no table: no claim
    assert aggregate_costs(items, _tiers().model_tiers)["warnings"] == []        # an empty table says nothing
    matching = {"x": _item("lit_scout", "fast", "claude-haiku-5-5")}
    assert aggregate_costs(matching, tiers)["warnings"] == []                    # alias and full name agree


def test_a_tier_model_follows_the_engine_the_agent_really_runs_on():
    """#625 review: the tier is read after an override, a request identity or runner.force_engine has moved the agent."""
    tiered = _tiers(fast={"claude_code": "haiku", "codex": "gpt-6-luna"})
    bare = AgentSpec(id="scout", name="Scout", role="r", model_tier="fast")           # claude_code in the YAML
    assert _runner_agent(tiered, bare).model == "haiku"
    assert _runner_agent(tiered, bare, agent_overrides={"engine": "codex"}).model == "gpt-6-luna"
    assert _runner_agent(tiered, bare, agent_overrides={"engine": "codex", "model": "gpt-6-sol"}).model == "gpt-6-sol"
    forced = Settings.model_validate({"runner": {"force_engine": "codex"},
                                      "model_tiers": {"fast": {"claude_code": "haiku", "codex": "gpt-6-luna"}}})
    assert _runner_agent(forced, bare).model == "gpt-6-luna"                           # the forced engine, not the YAML's
    only_claude = Settings.model_validate({"runner": {"force_engine": "codex"},
                                           "model_tiers": {"fast": {"claude_code": "haiku"}}})
    assert _runner_agent(only_claude, bare).model is None                              # no codex entry: nothing carried over


def test_dry_run_previews_the_model_of_the_engine_the_runner_will_force():
    """#625 review: the runner reads the tier after runner.force_engine, so the preview must too."""
    from labhq.dry_run import estimate_call

    agent = AgentSpec(id="scout", name="Scout", role="r", model_tier="fast")
    table = {"fast": {"claude_code": "haiku", "codex": "gpt-6-luna"}}
    assert estimate_call(Settings.model_validate({"model_tiers": table}), agent, stage="조사")["model"] == "haiku"
    forced = Settings.model_validate({"model_tiers": table, "runner": {"force_engine": "codex"}})
    assert estimate_call(forced, agent, stage="조사")["model"] == "gpt-6-luna"


def test_a_step_tier_names_the_model_before_the_staff_members_own_and_only_for_the_engine_it_runs_on():
    """#625 step 2: the gateway sends a step's tier already capped at the staff member's declared tier."""
    tiered = _tiers(fast={"claude_code": "haiku"}, balanced={"claude_code": "sonnet"})
    declared = AgentSpec(id="analyst", name="A", role="r", model="opus", model_tier="reasoning")
    assert _runner_agent(tiered, declared).model == "opus"                                   # no step tier: as before
    lowered = _runner_agent(tiered, declared, model_tier="fast")
    assert lowered.model == "haiku" and lowered.model_tier == "fast"                        # the step's decision wins
    assert _runner_agent(tiered, declared, model_tier="reasoning").model == "opus"          # no entry in the table
    assert _runner_agent(Settings(), declared, model_tier="fast").model == "opus"           # an empty table changes nothing
    assert _runner_agent(tiered, declared, model_tier="turbo").model == "opus"              # not a tier
    other = AgentSpec(id="analyst", name="A", role="r", engine="codex", model="gpt-6.1-sol", model_tier="reasoning")
    assert _runner_agent(tiered, other, model_tier="fast").model == "gpt-6.1-sol"           # no codex entry: untouched
