"""A step may write its own outputs/ although the artifact contract marks the workspace root private (#946, trial
2026-10-25 attempt 11: every research step died with `missing outputs` because Edit/Write were denied on the whole
workspace root, and a deny outranks the allow rule for the task's own folder)."""

from dataclasses import replace

import pytest

from labhq import private_paths
from labhq.policy import claude_deny_private, claude_rule_path
from labhq.private_paths import resolve_private_paths, scoped_open_reads
from labhq.settings import Settings


REAL_LABHQ_ENTRIES = private_paths._labhq_entries  # conftest replaces it for every test


def _setup(tmp_path, monkeypatch):
    monkeypatch.setattr(private_paths, "_labhq_entries", REAL_LABHQ_ENTRIES)
    home = tmp_path / "home"
    staff = home / ".labhq" / "claude-staff"
    root = tmp_path / "runs"
    ws_dir = root / "2026-10-25" / "task_a_lit_scout"
    for folder in (staff / "projects", ws_dir / "outputs", root / "2026-10-25" / "task_b_other",
                   root / "2026-10-24" / "task_old"):
        folder.mkdir(parents=True)
    (root / ".labhq-system-ca.pem").write_text("pem")
    settings = Settings()
    settings.runner.workspace_root = str(root)
    settings.runner.talent_dir = str(tmp_path / "talent")
    settings.engines.claude_code.env = {"CLAUDE_CONFIG_DIR": str(staff)}
    project = staff / "projects" / "slug"
    project.mkdir()
    private = resolve_private_paths(settings, [str(ws_dir), str(root)], home=str(home), open_reads=[str(project)],
                                    cwd=str(ws_dir))
    # The artifact-contract branch of the runner: the whole workspace root becomes a private path.
    private = replace(private, paths=tuple(dict.fromkeys([*private.paths, str(root.resolve())])), enabled=True,
                      open_reads=tuple(scoped_open_reads(settings, [*private.open_reads, str(ws_dir)], cwd=str(ws_dir))))
    return home, staff, root, ws_dir, project, private


def _rules(deny, tool, path):
    """The deny rules of `tool` that cover `path`: a `/**` rule covers its folder and everything below it."""
    target = f"/{claude_rule_path(str(path))}"
    out = []
    for rule in deny:
        if not rule.startswith(f"{tool}(") or not rule.endswith(")"):
            continue
        inner = rule[len(tool) + 1:-1]
        if inner == target or (inner.endswith("/**") and (target + "/").startswith(inner[:-3] + "/")):
            out.append(rule)
    return out


def test_a_step_can_write_its_own_outputs_but_not_the_rest_of_the_workspace_root(tmp_path, monkeypatch):
    home, staff, root, ws_dir, project, private = _setup(tmp_path, monkeypatch)
    deny = claude_deny_private({}, private.paths, str(home), open_reads=private.open_reads,
                               write_roots=[str(ws_dir)])["permissions"]["deny"]
    for tool in ("Edit", "Write"):
        assert not _rules(deny, tool, ws_dir / "outputs" / "result.tsv")  # nothing outranks the task's own allow rule
        assert not _rules(deny, tool, ws_dir / ".tmp" / "x")
        assert _rules(deny, tool, root / "2026-10-24" / "task_old" / "x")  # another day's workspaces stay closed
        assert _rules(deny, tool, root / ".labhq-system-ca.pem")  # as do top-level files
    assert not _rules(deny, "Read", ws_dir / "outputs" / "result.tsv")  # reads were already scoped this way


def test_without_the_write_root_the_old_whole_root_deny_is_what_blocked_outputs(tmp_path, monkeypatch):
    home, staff, root, ws_dir, project, private = _setup(tmp_path, monkeypatch)
    deny = claude_deny_private({}, private.paths, str(home), open_reads=private.open_reads)["permissions"]["deny"]
    assert _rules(deny, "Write", ws_dir / "outputs" / "result.tsv")  # the failure #946 reports


def test_the_staff_config_folder_keeps_its_whole_path_edit_write_deny(tmp_path, monkeypatch):
    home, staff, root, ws_dir, project, private = _setup(tmp_path, monkeypatch)
    deny = claude_deny_private({}, private.paths, str(home), open_reads=private.open_reads,
                               write_roots=[str(ws_dir)])["permissions"]["deny"]
    for tool in ("Edit", "Write"):
        assert f"{tool}(/{claude_rule_path(str(staff))}/**)" in deny
        assert _rules(deny, tool, project / "anything")  # the config folder is never written, its project folder too


@pytest.mark.asyncio
async def test_a_runner_step_with_the_artifact_contract_gets_no_deny_over_its_own_outputs(tmp_path, monkeypatch):
    from labhq.models import Task
    from tests.test_claude_staff_home import _capture_runner, _runner_settings, _staff

    home, staff, _ws, _mine, _other, _settings = _staff(tmp_path, monkeypatch)
    settings = _runner_settings(tmp_path, None)
    settings.engines.claude_code.env = {"CLAUDE_CONFIG_DIR": str(staff)}
    runner, seen = _capture_runner(settings, monkeypatch)
    task = Task(id="t1", agent_id="worker", request_id="r", prompt="q",
                meta={"kind": "step", "step_id": "s1", "upstream_artifacts": {}})
    assert (await runner.run_task(task)).ok
    ctx = seen["ctx"]
    deny = ctx.claude_settings["permissions"]["deny"]
    root = runner.ws_root.resolve()
    assert str(root) in ctx.private_paths  # the artifact contract made the whole workspace root private
    for tool in ("Edit", "Write"):
        assert not _rules(deny, tool, ctx.workdir / "outputs" / "result.tsv")
        assert not _rules(deny, tool, ctx.workdir / ".tmp" / "x")


def _alias(real, link):
    """A directory alias (symlink, or a junction on Windows without the symlink privilege), or None."""
    import os
    import subprocess

    try:
        os.symlink(real, link, target_is_directory=True)
        return link
    except (OSError, NotImplementedError):
        pass
    if os.name == "nt":
        done = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(real)], capture_output=True)
        return link if done.returncode == 0 else None
    return None


def test_a_junctioned_workspace_root_still_leaves_the_tasks_own_folder_writable(tmp_path, monkeypatch):
    monkeypatch.setattr(private_paths, "_labhq_entries", REAL_LABHQ_ENTRIES)
    home = tmp_path / "home"
    real = tmp_path / "real_runs"
    (real / "2026-10-25" / "task_a" / "outputs").mkdir(parents=True)
    (real / "2026-10-24" / "task_old").mkdir(parents=True)
    alias = _alias(real, tmp_path / "runs")
    if alias is None:
        pytest.skip("no symlink or junction available")
    ws_dir = alias / "2026-10-25" / "task_a"  # the runner keeps the alias spelling of the task folder
    settings = Settings()
    settings.runner.workspace_root = str(alias)
    settings.runner.talent_dir = str(tmp_path / "talent")
    home.mkdir()
    private = resolve_private_paths(settings, [str(ws_dir), str(alias)], home=str(home), open_reads=[], cwd=str(ws_dir))
    private = replace(private, paths=tuple(dict.fromkeys([*private.paths, str(alias.resolve())])), enabled=True,
                      open_reads=tuple(scoped_open_reads(settings, [*private.open_reads, str(ws_dir)], cwd=str(ws_dir))))
    deny = claude_deny_private({}, private.paths, str(home), open_reads=private.open_reads,
                               write_roots=[str(ws_dir)])["permissions"]["deny"]
    for spelled in (ws_dir / "outputs" / "r.tsv", real / "2026-10-25" / "task_a" / "outputs" / "r.tsv"):
        for tool in ("Edit", "Write", "Read"):
            assert not _rules(deny, tool, spelled), (tool, spelled)
    assert _rules(deny, "Write", real / "2026-10-24" / "task_old" / "x")  # the rest of the root stays closed


@pytest.mark.asyncio
async def test_a_runner_with_a_junctioned_root_denies_inputs_under_both_spellings_and_allows_outputs(
        tmp_path, monkeypatch):
    import os

    from labhq.models import Task
    from tests.test_claude_staff_home import _capture_runner, _runner_settings, _staff

    home, staff, _ws, _mine, _other, _settings = _staff(tmp_path, monkeypatch)
    settings = _runner_settings(tmp_path, None)
    settings.engines.claude_code.env = {"CLAUDE_CONFIG_DIR": str(staff)}
    real = tmp_path / "real_ws"
    real.mkdir()
    alias = _alias(real, tmp_path / "ws_alias")
    if alias is None:
        pytest.skip("no symlink or junction available")
    settings.runner.workspace_root = str(alias)
    runner, seen = _capture_runner(settings, monkeypatch)
    task = Task(id="t1", agent_id="worker", request_id="r", prompt="q",
                meta={"kind": "step", "step_id": "s1", "upstream_artifacts": {}})
    assert (await runner.run_task(task)).ok
    ctx = seen["ctx"]
    deny = ctx.claude_settings["permissions"]["deny"]
    workdir = str(ctx.workdir)
    real_workdir = os.path.realpath(workdir)
    for base in (workdir, real_workdir):
        for tool in ("Edit", "Write"):
            assert not _rules(deny, tool, os.path.join(base, "outputs", "r.tsv")), (tool, base)
            assert _rules(deny, tool, os.path.join(base, "inputs", "s0", "x")), (tool, base)  # ancestor copies stay read-only


@pytest.mark.asyncio
@pytest.mark.parametrize("contract", [True, False])
async def test_the_write_allow_of_an_artifact_contract_step_covers_only_outputs_and_tmp(tmp_path, monkeypatch,
                                                                                         contract):
    from labhq.adapters import get_adapter
    from labhq.models import Engine, Task
    from tests.test_claude_staff_home import _capture_runner, _runner_settings, _staff

    home, staff, _ws, _mine, _other, _settings = _staff(tmp_path, monkeypatch)
    settings = _runner_settings(tmp_path, None)
    settings.engines.claude_code.env = {"CLAUDE_CONFIG_DIR": str(staff)}
    runner, seen = _capture_runner(settings, monkeypatch)
    meta = {"kind": "step", "step_id": "s1", **({"upstream_artifacts": {}} if contract else {})}
    assert (await runner.run_task(Task(id="t1", agent_id="worker", request_id="r", prompt="q", meta=meta))).ok
    ctx = seen["ctx"]
    monkeypatch.undo()  # the capture patch replaced get_adapter: build the real command from the captured context
    ctx = replace(ctx, agent=ctx.agent.model_copy(update={"tools": ["Write", "Edit"]}))
    cmd = get_adapter(Engine.claude_code, settings).build_command(ctx)
    edits = [rule for rule in cmd[cmd.index("--allowedTools") + 1:] if rule.startswith("Edit(")]
    workdir = claude_rule_path(str(ctx.workdir))
    if contract:
        assert f"Edit(/{workdir}/**)" not in edits  # a whole-folder rule would also pre-approve `INPUTS/` spellings
        assert f"Edit(/{workdir}/outputs/**)" in edits and f"Edit(/{workdir}/.tmp/**)" in edits
    else:
        assert f"Edit(/{workdir}/**)" in edits
