"""Removal path (#150 B1): off means the shadow is never loaded; deleting it leaves labhq as it was."""
from __future__ import annotations

import asyncio
import ast
import shutil
from pathlib import Path

import pytest

from scripts import semantics_shadow_remove as removal
from tests.semantics_shadow_lab import run_lab

ROOT = Path(__file__).resolve().parents[1]
# file -> number of marked lines; a new hook must be marked and counted here
HOOKS = {"labhq/settings.py": 2, "labhq/gateway/server.py": 9, "labhq/cli.py": 13,
         "labhq/orchestrator/cso.py": 2}
# `# semantics-hook: actions` lines of the action layer's shadow (#149 결정 13 A1), gone after `--only actions`
ACTION_HOOKS = {"labhq/gateway/server.py": 4, "labhq/orchestrator/cso.py": 4, "labhq/cli.py": 8}  # cli: A2 parser (결정 16)


@pytest.fixture(scope="module")
def pristine_lab_state(tmp_path_factory):
    """Build the live lab state once, as a template no test opens: a Hub on it would recover the running request."""
    root = tmp_path_factory.mktemp("semantics-removal") / "lab"
    lab = asyncio.run(run_lab(root, {"mode": "shadow", "actions": "shadow"},
                              ["CD276 세포유형 분석 [artifact]"]))
    assert lab["lines"] and "actions" in lab["lines"][-1]
    hub = lab["hub"]
    hub.requests["req_inflight1"] = {"id": "req_inflight1", "status": "running", "text": "x",
                                     "mode": "orchestrate", "created_at": 1.0}
    hub.save_request("req_inflight1")
    hub.store.close()
    return root / "state"


@pytest.fixture
def removable_lab_state(pristine_lab_state, tmp_path):
    """Each removal mode gets its own copy, so each starts from a fresh `running` request in any order."""
    return Path(shutil.copytree(pristine_lab_state, tmp_path / "state"))


def expected_hooks() -> dict:
    if not (ROOT / "labhq" / "research" / "semantics_actions.py").exists():
        return HOOKS
    return {rel: HOOKS.get(rel, 0) + ACTION_HOOKS.get(rel, 0) for rel in {*HOOKS, *ACTION_HOOKS}}


def test_every_hook_is_marked_and_counted():
    owned = {ROOT / rel for rel in removal.OWNED}
    found = {}
    for path in sorted((ROOT / "labhq").rglob("*.py")):
        if path in owned or path.name == "semantics.py":
            continue
        rel = path.relative_to(ROOT).as_posix()
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            mentions = any(w in line for w in ("semantics_shadow", "semantics_objects", "semantics_wanted",
                                               "semantics_cmd", "semantics_cli", ".semantics", '"semantics"'))
            if removal.HOOK in line:
                found[rel] = found.get(rel, 0) + 1
            elif mentions or line.strip().startswith("semantics:"):
                raise AssertionError(f"{rel}:{number} touches the shadow without {removal.HOOK}")
    assert found == expected_hooks()


def test_the_cli_imports_the_shadow_only_for_its_own_command():
    tree = ast.parse((ROOT / "labhq" / "cli.py").read_text(encoding="utf-8"))
    branch = next(node for node in ast.walk(tree) if isinstance(node, ast.If) and isinstance(node.test, ast.Compare)
                  and getattr(node.test.comparators[0], "value", None) == "semantics")
    inside = {id(n) for stmt in branch.body for n in ast.walk(stmt)}
    imports = [n for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and "semantics" in (n.module or "")]
    assert imports and all(id(n) in inside for n in imports)


def test_removing_the_shadow_leaves_a_working_labhq(removable_lab_state):
    summary = removal.check(removable_lab_state)
    assert set(summary["files"]) == {rel for rel in removal.OWNED if (ROOT / rel).exists()}
    assert summary["hook_lines"] == sum(expected_hooks().values()) and summary["blocks"] == 2
    assert summary["state"] == {"done": 1, "interrupted": 1, "resume_approvals": 1}
    assert " passed" in summary["pytest"] and "failed" not in summary["pytest"]


def test_removing_the_action_layer_only_leaves_the_b1_shadow_working(removable_lab_state):
    summary = removal.check(removable_lab_state, only="actions")
    assert summary["files"] == removal.ACTIONS_OWNED
    assert summary["hook_lines"] == 8 + 11 + 24 and summary["blocks"] == 2  # server 4, cso 4, cli 11 (A2), shadow 24
    assert summary["state"] == {"done": 1, "interrupted": 1, "resume_approvals": 1, "b1_line": "ok",
                                "actions_field": False}
    assert " passed" in summary["pytest"] and "failed" not in summary["pytest"]


def test_the_public_scan_covers_every_shadow_file():
    from tests.test_semantics_public import pilot_files
    scanned = {p.relative_to(ROOT).as_posix() for p in pilot_files()}
    assert set(removal.OWNED) <= scanned
