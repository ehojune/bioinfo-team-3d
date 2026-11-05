"""Mainline CP1 survives optional-layer removal; no processes or filesystem removal."""
import ast
import builtins
from pathlib import Path
from types import ModuleType
import sys

import pytest

from scripts import semantics_shadow_remove as removal
from tests.test_cp1_input_fit_796 import no_native_workers, run_plans


ROOT = Path(__file__).resolve().parents[1]
CORE = {"labhq/research/input_fit.py", "labhq/research/input_fit.yaml"}


def transformed_sources(only):
    owned = set(removal._scope(only)[0])
    return {p.relative_to(ROOT).as_posix(): removal.strip_hooks(p.read_text(encoding="utf-8"), only)[0]
            for p in (ROOT / "labhq").rglob("*.py") if p.relative_to(ROOT).as_posix() not in owned}


@pytest.mark.parametrize("only", [None, "actions"])
def test_removal_preserves_mainline_and_only_the_intended_optional_layer(only):
    owned = set(removal._scope(only)[0])
    assert CORE.isdisjoint(owned)
    assert all((ROOT / path).is_file() for path in CORE)
    sources = transformed_sources(only)
    for path, text in sources.items():
        ast.parse(text, filename=path, feature_version=(3, 10))
        if only is None:
            assert not any(word in text for word in (
                "semantics_shadow", "semantics_objects", "semantics_input_fit", "semantics-hook",
                "records_from_rows", "semantics_actions", "after_followup")), path
        else:
            assert not any(word in text for word in (
                "semantics_actions", "semantics-hook: actions", "semantics-actions", "after_followup")), path
    adapter = "labhq/research/semantics_input_fit.py"
    observer = "labhq/research/semantics_shadow.py"
    assert (adapter in sources) is (only == "actions")
    assert (observer in sources) is (only == "actions")
    if only == "actions":
        assert "semantics_input_fit.evaluate" in sources[observer]
        assert "from .input_fit import" in sources[adapter]
    assert '"research/input_fit.yaml"' in (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    # Real CI removal must exercise the same positive CP1 contracts, not only old baseline tests.
    selected = removal.CHECK_TESTS if only is None else removal.ACTIONS_CHECK_TESTS
    assert "tests/test_cp1_input_fit_796.py" in selected


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["off", "full", "actions"])
@pytest.mark.parametrize("kind", ["sequence_reads", "unrecognized", None])
async def test_cp1_with_optional_imports_denied_before_and_after_in_memory_removal(monkeypatch, scope, kind):
    source = (ROOT / "labhq/orchestrator/cso.py").read_text(encoding="utf-8")
    if scope != "off":
        source = removal.strip_hooks(source, "actions" if scope == "actions" else None)[0]
    original_import = builtins.__import__
    attempted = []

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        names = [name, *(fromlist or ())]
        if any("semantics" in part for part in names):
            attempted.append(names)
            raise AssertionError(f"CP1 imported optional observer/model: {names}")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    module = ModuleType("labhq.orchestrator._input_fit_boundary")
    module.__package__ = "labhq.orchestrator"
    module.__file__ = str(ROOT / "labhq/orchestrator/cso.py")
    monkeypatch.setitem(sys.modules, module.__name__, module)
    exec(compile(source, module.__file__, "exec"), module.__dict__)
    # Load the evaluator body afresh too; cached imports must not conceal an optional dependency.
    core = ModuleType("labhq.research._input_fit_boundary")
    core.__package__ = "labhq.research"
    core.__file__ = str(ROOT / "labhq/research/input_fit.py")
    exec(compile(Path(core.__file__).read_text(encoding="utf-8"), core.__file__, "exec"), core.__dict__)
    assert core.table_sha256() == module.input_fit.table_sha256()
    monkeypatch.setattr(module, "input_fit", core)
    types = [kind, "raw_counts"] if kind == "sequence_reads" else [kind]
    hub, req = await run_plans(types, orchestrator=module.Orchestrator, observer="off")
    assert not attempted
    assert len(hub.approvals) == 1 and req["outcome"] == "plan_approved"
    trace = req["plan_validation"]["semantic_checks"]
    if kind == "sequence_reads":
        assert len(hub.calls) == 2
        assert trace[0]["input_fit"] == {"fit": 0, "mismatch": 1, "unknown": 1}
        assert trace[1]["input_fit"] == {"fit": 1, "mismatch": 0, "unknown": 1}
        assert len(trace[1]["resolved_mismatches"]) == 1
        assert "sequence_reads" in hub.calls[1].prompt
        assert any("sequence_reads" in warning for warning in hub.approvals[0]["detail"]["plan_warnings"])
        assert hub.approvals[0]["detail"]["plan_validation"]["semantic_checks"] == trace
    else:
        assert len(hub.calls) == 1
        assert trace[0]["input_fit"] == {"fit": 0, "mismatch": 0, "unknown": 2}
        assert trace[0]["mismatches"] == trace[0]["resolved_mismatches"] == []


def test_pure_transform_keeps_mode_boundaries_and_rejects_unclosed_blocks():
    source = "keep\nx # semantics-hook\ny # semantics-hook: actions\n"
    assert removal.strip_hooks(source) == ("keep\n", 2, 0)
    assert removal.strip_hooks(source, "actions") == ("keep\nx # semantics-hook\n", 1, 0)
    source = "keep\n# semantics-shadow: begin\nobserver\n# semantics-shadow: end\n"
    assert removal.strip_hooks(source) == ("keep\n", 0, 1)
    assert removal.strip_hooks(source, "actions") == (source, 0, 0)
    with pytest.raises(ValueError, match="block without"):
        removal.strip_hooks("# semantics-shadow: begin\nobserver\n")
