"""Removal path (#150 B1): off means the shadow is never loaded; deleting it leaves labhq as it was."""
from __future__ import annotations

import ast
from pathlib import Path

from scripts import semantics_shadow_remove as removal
from tests.semantics_shadow_lab import run_lab

ROOT = Path(__file__).resolve().parents[1]
# file -> number of marked lines; a new hook must be marked and counted here
HOOKS = {"labhq/settings.py": 2, "labhq/gateway/server.py": 9, "labhq/cli.py": 13,
         "labhq/orchestrator/cso.py": 2}
# `# semantics-hook: actions` lines of the action layer's shadow (#149 결정 13 A1), gone after `--only actions`
ACTION_HOOKS = {"labhq/gateway/server.py": 4, "labhq/orchestrator/cso.py": 4}


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


async def test_removing_the_shadow_leaves_a_working_labhq(tmp_path):
    lab = await run_lab(tmp_path / "lab", "shadow", ["CD276 세포유형 분석 [artifact]"])
    hub = lab["hub"]
    assert lab["lines"]
    hub.requests["req_inflight1"] = {"id": "req_inflight1", "status": "running", "text": "x", "mode": "orchestrate",
                                     "created_at": 1.0}
    hub.save_request("req_inflight1")
    summary = removal.check(tmp_path / "lab" / "state")
    assert set(summary["files"]) == {rel for rel in removal.OWNED if (ROOT / rel).exists()}
    assert summary["hook_lines"] == sum(expected_hooks().values()) and summary["blocks"] == 2
    assert summary["state"] == {"done": 1, "interrupted": 1, "resume_approvals": 1}
    assert " passed" in summary["pytest"] and "failed" not in summary["pytest"]


def test_the_public_scan_covers_every_shadow_file():
    from tests.test_semantics_public import pilot_files
    scanned = {p.relative_to(ROOT).as_posix() for p in pilot_files()}
    assert set(removal.OWNED) <= scanned
