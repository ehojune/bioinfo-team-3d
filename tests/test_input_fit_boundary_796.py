"""Mainline CP1 survives optional-layer removal; no processes or filesystem removal."""
import builtins
from pathlib import Path
from types import ModuleType
import sys

import pytest

from tests.test_cp1_input_fit_796 import no_native_workers, run_plans


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["sequence_reads", "unrecognized", None])
async def test_cp1_with_removed_model_imports_denied(monkeypatch, kind):
    source = (ROOT / "labhq/orchestrator/cso.py").read_text(encoding="utf-8")
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
