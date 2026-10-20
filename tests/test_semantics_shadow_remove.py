"""Removal path (#150 B1): off means the shadow is never loaded; deleting it leaves labhq as it was."""
from __future__ import annotations

import asyncio
import ast
import json
import shutil
from types import SimpleNamespace
from pathlib import Path

import pytest

from scripts import semantics_shadow_remove as removal
from tests.semantics_shadow_lab import run_lab

ROOT = Path(__file__).resolve().parents[1]
# file -> number of marked lines; a new hook must be marked and counted here
HOOKS = {"labhq/settings.py": 2, "labhq/gateway/server.py": 10, "labhq/cli.py": 17,
         "labhq/doctor.py": 4, "labhq/orchestrator/cso.py": 3,
         "labhq/research/semantic_devices.py": 2}
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


def test_the_cli_imports_the_shadow_only_for_semantics_or_status_commands():
    tree = ast.parse((ROOT / "labhq" / "cli.py").read_text(encoding="utf-8"))
    branches = [node for node in ast.walk(tree) if isinstance(node, ast.If) and isinstance(node.test, ast.Compare)
                and getattr(node.test.comparators[0], "value", None) in {"semantics", "status"}]
    inside = {id(n) for branch in branches for stmt in branch.body for n in ast.walk(stmt)}
    imports = [n for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and "semantics" in (n.module or "")]
    assert len(branches) == 2 and len(imports) == 2 and all(id(n) in inside for n in imports)


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


def test_a_failed_inner_run_names_its_failures_above_a_long_tail():
    # 2026-10-05: an inner run failed with a faulthandler stack as its last 3000 characters, hiding which test.
    import subprocess

    output = "Fatal Python error: Segmentation fault\nFAILED tests/test_x.py::test_y - boom\n" + "frame\n" * 2000
    with pytest.raises(SystemExit) as caught:
        removal._ok(subprocess.CompletedProcess(["pytest"], 1, output, ""), "pytest")
    head = str(caught.value).splitlines()[:3]
    assert head == ["pytest failed after removal:", "Fatal Python error: Segmentation fault",
                    "FAILED tests/test_x.py::test_y - boom"]


def test_off_comparisons_reuse_only_an_immutable_record(tmp_path, monkeypatch):
    from tests import test_semantics_shadow_worker as worker
    calls, closed = [], []
    live = {'nested': {'values': [1]}}

    async def fake_lab(root, mode, texts):
        calls.append((root, mode, tuple(texts)))
        root.mkdir(parents=True, exist_ok=True)
        return {'hub': SimpleNamespace(store=SimpleNamespace(close=lambda: closed.append(root))),
                'lines': [{'type': 'request'}] if mode in ('shadow', {'mode': 'shadow', 'timeout_s': 2}) else []}

    monkeypatch.setattr(worker, 'run_lab', fake_lab)
    monkeypatch.setattr(worker, 'lab_record', lambda lab, root: live)
    factory = SimpleNamespace(mktemp=lambda name: tmp_path / name)
    baseline = worker.off_lab_record.__wrapped__(factory)
    assert isinstance(baseline, str) and json.loads(baseline) == live
    # No Hub, store, settings or nested mutable object is shared with the comparisons.
    live['nested']['values'].append(2)
    assert json.loads(baseline)['nested']['values'] == [1]
    live['nested']['values'].pop()
    for n, value in enumerate(['shadow', {'mode': 'shadow', 'timeout_s': 2}, {'mode': 'advisory'}, 'bogus']):
        asyncio.run(worker.test_the_lab_records_the_same_with_semantics_off_or_on(
            tmp_path / str(n), value, baseline))
    assert [mode for _, mode, _ in calls].count(None) == 1
    assert len(calls) == 5 and len(closed) == 5
    assert len({root for root, _, _ in calls}) == 5


@pytest.mark.parametrize('only', [None, 'actions'])
def test_check_keeps_compile_collection_state_and_focused_e2e_gates(tmp_path, monkeypatch, only):
    import subprocess
    source = tmp_path / 'source'
    (source / 'labhq').mkdir(parents=True)
    (source / 'tests').mkdir()
    monkeypatch.setattr(removal, 'ROOT', source)
    monkeypatch.setattr(removal, 'remove', lambda *a: {'files': [], 'hook_lines': 0, 'blocks': 0})
    monkeypatch.setattr(removal.shutil, 'copytree', lambda src, dst, **kw: dst.mkdir())
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        output = '1 passed'
        if len(argv) > 2 and argv[2] in (removal.SMOKE, removal.ACTIONS_SMOKE):
            output = '{"done": 1, "interrupted": 1, "resume_approvals": 1}'
        return subprocess.CompletedProcess(argv, 0, output, '')

    monkeypatch.setattr(removal.subprocess, 'run', run)
    summary = removal.check(tmp_path / 'state', only)
    assert summary['state']['interrupted'] == summary['state']['resume_approvals'] == 1
    assert 'compileall' in calls[0][0][2]
    assert len(calls) == 4
    collect = next(argv for argv, _ in calls if '--collect-only' in argv)
    assert collect[-1] == 'tests'
    executed = calls[-1][0]
    selected = removal.ACTIONS_CHECK_TESTS if only else removal.CHECK_TESTS
    assert executed[-len(selected):] == selected
    assert 'tests/test_cp1_input_fit_796.py' in selected
    assert 'tests/test_e2e_mock.py::test_full_lab_flow_with_mock_agents' in selected
    assert not set(selected) & {'tests/test_cso.py', 'tests/test_semantics_pilot.py', 'tests/test_followup.py'}
    if only:
        assert 'tests/test_followup.py::test_followup_end_to_end_with_mock_runner' in selected


@pytest.mark.parametrize('only', [None, 'actions'])
def test_full_removal_leaves_no_top_level_import_of_deleted_modules(only):
    owned = set(removal._scope(only)[0])
    removed = {rel[:-3].replace('/', '.') for rel in owned if rel.endswith('.py')}
    dangling = []
    for path in (ROOT / 'tests').glob('test_*.py'):
        if path.relative_to(ROOT).as_posix() in owned:
            continue
        for node in ast.parse(path.read_text(encoding='utf-8')).body:
            if isinstance(node, ast.ImportFrom):
                names = {node.module, *(f'{node.module}.{alias.name}' for alias in node.names)}
            elif isinstance(node, ast.Import):
                names = {alias.name for alias in node.names}
            else:
                continue
            if names & removed:
                dangling.append((path.name, node.lineno, sorted(names & removed)))
    assert not dangling


@pytest.mark.parametrize('only', [None, 'actions'])
def test_selected_contract_nodes_exist_and_survive_the_transform(only):
    owned = set(removal._scope(only)[0])
    selected = removal.ACTIONS_CHECK_TESTS if only else removal.CHECK_TESTS
    for node in selected:
        path, _, name = node.partition('::')
        assert path not in owned and path != 'tests/test_semantics_shadow_remove.py'
        tree = ast.parse((ROOT / path).read_text(encoding='utf-8'))
        if name:
            assert name in {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}, node


def test_collection_failure_stops_before_state_or_child_execution(tmp_path, monkeypatch):
    import subprocess
    source = tmp_path / 'source'
    (source / 'labhq').mkdir(parents=True)
    monkeypatch.setattr(removal, 'ROOT', source)
    monkeypatch.setattr(removal, 'remove', lambda *a: {})
    monkeypatch.setattr(removal.shutil, 'copytree', lambda src, dst, **kw: dst.mkdir())
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        collect = '--collect-only' in argv
        return subprocess.CompletedProcess(argv, 2 if collect else 0,
            'ERROR tests/test_survivor.py - ModuleNotFoundError: removed adapter' if collect else '', '')

    monkeypatch.setattr(removal.subprocess, 'run', run)
    with pytest.raises(SystemExit, match='pytest collection failed after removal') as caught:
        removal.check(tmp_path / 'state')
    assert 'test_survivor.py' in str(caught.value) and len(calls) == 2
