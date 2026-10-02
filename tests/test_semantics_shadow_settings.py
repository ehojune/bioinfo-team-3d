"""`semantics:` setting (#150 B1): off by default, off or shadow only; a bad value turns semantics off with one
warning and never fails the settings load."""
from __future__ import annotations

import logging
from pathlib import Path

import pytest
import yaml

from labhq.settings import Settings

ROOT = Path(__file__).resolve().parents[1]
BAD = [3, "shadoww", "advisory", "on", True, [1], {"mode": "shadow", "timeout_s": -1},
       {"mode": "shadow", "timeout_s": "2"}, {"mode": "shadow", "timeout_s": True}, {"mode": "shadow", "timeout_s": 11},
       {"mode": "shadow", "history_requests": 5}, {"mode": "shadow", "history_requests": 2.5},
       {"mode": "shadow", "extra": 1}, {"mode": "advisory"}, {"mode": 1}, {"mode": "off", "x": 1}]


def _load(tmp_path, text):
    path = tmp_path / "labhq.yaml"
    path.write_text(text, encoding="utf-8")
    return Settings.load(str(path))


@pytest.mark.parametrize("value", BAD, ids=repr)
def test_a_bad_value_loads_turns_semantics_off_and_warns_once(tmp_path, caplog, value):
    from labhq.research import semantics_shadow as shadow
    shadow._warned.clear()
    s = _load(tmp_path, yaml.safe_dump({"semantics": value, "gateway": {"port": 9999}}))
    assert s.gateway.port == 9999 and s.semantics == value  # the rest of the settings load as before
    with caplog.at_level(logging.WARNING, logger="labhq.semantics"):
        assert shadow.resolve(s.semantics) is None
        assert shadow.resolve(s.semantics) is None
    assert len([r for r in caplog.records if "semantics setting ignored" in r.getMessage()]) == 1


@pytest.mark.parametrize("text", ["", "semantics: off\n", "semantics: 'off'\n", "semantics: null\n",
                                  "semantics: {mode: off}\n", "semantics: {}\n"])
def test_every_spelling_of_off_is_off_without_a_warning(tmp_path, caplog, text):
    from labhq.research import semantics_shadow as shadow
    shadow._warned.clear()
    with caplog.at_level(logging.WARNING, logger="labhq.semantics"):
        assert shadow.resolve(_load(tmp_path, text).semantics) is None
    assert not caplog.records


@pytest.mark.parametrize("text,timeout,history", [
    ("semantics: shadow\n", 5.0, 200),
    ("semantics: {mode: shadow}\n", 5.0, 200),
    ("semantics: {mode: shadow, timeout_s: 2, history_requests: 50}\n", 2.0, 50),
])
def test_shadow(tmp_path, text, timeout, history):
    from labhq.research.semantics_shadow import ShadowConfig, resolve
    assert resolve(_load(tmp_path, text).semantics) == ShadowConfig(timeout_s=timeout, history_requests=history)


@pytest.mark.parametrize("text", ["semantics: ab\n", "semantics: {mode: ab}\n"])
def test_ab_is_supported_but_plain_advisory_remains_held(tmp_path, text):
    from labhq.research.semantics_shadow import ShadowConfig, resolve
    assert resolve(_load(tmp_path, text).semantics) == ShadowConfig(mode="ab")


def test_default_is_off_and_the_example_configs_say_so():
    from labhq.research.semantics_shadow import resolve
    assert Settings().semantics is None
    texts = [(ROOT / p).read_text(encoding="utf-8") for p in ("config/labhq.example.yaml",
                                                               "labhq/config/labhq.example.yaml")]
    assert texts[0] == texts[1]
    data = yaml.safe_load(texts[0])
    assert "semantics" in data and resolve(data["semantics"]) is None


def test_a_gateway_with_a_bad_value_starts_with_semantics_off(tmp_path):
    from labhq.gateway.server import Hub
    s = Settings()
    s.gateway.state_dir = str(tmp_path / "state")
    s.semantics = {"mode": "advisory"}
    assert Hub(s).semantics_shadow is None
    assert not (tmp_path / "state" / "semantics").exists()
