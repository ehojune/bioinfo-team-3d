"""The one Claude Code staff member allowed to load the approved bioinfo plugin skill."""

import json
import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from labhq.adapters import get_adapter
from labhq.adapters.base import RunContext
from labhq.models import AgentSpec, Engine, Task
from labhq.registry import Registry
from labhq.settings import Settings
from scripts.redact_stream import Redactor


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _no_outer_repo(tmp_path, monkeypatch):
    # A pytest temp dir inside a checkout would make git see the outer repo; each test builds its own layout.
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", tmp_path.as_posix())


async def _emit(kind, data):
    pass


def _ctx(tmp_path, agent, env=None):
    wd = tmp_path / "work"
    wd.mkdir(parents=True, exist_ok=True)
    return RunContext(task=Task(agent_id=agent.id, prompt="x"), agent=agent, workdir=wd,
                      settings=Settings(), mcp_servers=[], env=env or {}, emit=_emit, prompt="x")


def test_core_agent_skill_exception_is_scoped(tmp_path, monkeypatch):
    plugin = tmp_path / "plugin"
    (plugin / ".claude-plugin").mkdir(parents=True)
    (plugin / ".claude-plugin" / "plugin.json").write_text('{"name":"bioinfo"}')
    monkeypatch.setenv("BIOINFO_AGENT_DIR", str(plugin))
    registry = Registry(ROOT / "agents", tmp_path / "talent")
    registry.load()
    bioinfo = registry.get("bioinfo-agent")
    assert bioinfo.engine == Engine.claude_code and bioinfo.builtin_mcp == ["approval", "hpc"]
    for agent in registry.agents.values():
        if agent.engine != Engine.claude_code:
            continue
        ctx = _ctx(tmp_path / agent.id, agent)
        cmd = get_adapter(agent.engine, ctx.settings).build_command(ctx)
        assert cmd[cmd.index("--setting-sources") + 1] == ("local" if agent.allow_skills else "project,local")
        if agent.id == "bioinfo-agent":
            assert cmd[cmd.index("--plugin-dir") + 1] == str(plugin)
            assert "--disable-slash-commands" not in cmd
            settings = json.loads(cmd[cmd.index("--settings") + 1])
            assert settings["autoMemoryEnabled"] is False and settings["claudeMdExcludes"]
        else:
            assert "--plugin-dir" not in cmd and "--disable-slash-commands" in cmd


def test_plugin_dir_expands_task_environment_and_repeats_flag(tmp_path, monkeypatch):
    monkeypatch.delenv("BIOINFO_AGENT_DIR", raising=False)
    first, second = tmp_path / "first", tmp_path / "second"
    for directory in (first, second):
        (directory / ".claude-plugin").mkdir(parents=True)
        (directory / ".claude-plugin" / "plugin.json").write_text("{}")
    agent = AgentSpec(id="a", name="A", role="test", engine=Engine.claude_code,
                      plugin_dirs=["${BIOINFO_AGENT_DIR}", str(second)], allow_skills=True)
    ctx = _ctx(tmp_path, agent, {"BIOINFO_AGENT_DIR": str(first)})
    adapter = get_adapter(agent.engine, ctx.settings)
    assert adapter.preflight_error(ctx, ctx.env) is None
    cmd = adapter.build_command(ctx)
    assert [cmd[i + 1] for i, arg in enumerate(cmd) if arg == "--plugin-dir"] == [str(first), str(second)]


def _plugin(root, name="bioinfo", skill="bioinfo-analyze", version="0.4.9"):
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": name, "version": version}))
    if skill:
        (root / "skills" / skill).mkdir(parents=True)
        (root / "skills" / skill / "SKILL.md").write_text("---\nname: x\n---\n")
    return root


def _bioinfo_agent():
    return AgentSpec(id="bioinfo-agent", name="Bioinfo", role="test", engine=Engine.claude_code,
                     plugin_dirs=["${BIOINFO_AGENT_DIR}"], allow_skills=True,
                     required_skills=["bioinfo:bioinfo-analyze"])


@pytest.mark.asyncio
@pytest.mark.parametrize("case,expected", [
    ("unset", "BIOINFO_AGENT_DIR is not set"),
    ("missing_dir", "plugin directory for ${BIOINFO_AGENT_DIR} does not exist"),
    ("missing_manifest", "plugin manifest for ${BIOINFO_AGENT_DIR} is missing"),
    ("other_plugin", "required plugin 'bioinfo' is not among plugin_dirs"),
    ("skill_removed", "required skill 'bioinfo:bioinfo-analyze' is missing"),
])
async def test_plugin_preflight_refuses_before_workspace_write(tmp_path, monkeypatch, case, expected):
    monkeypatch.delenv("BIOINFO_AGENT_DIR", raising=False)
    plugin = tmp_path / "plugin"
    if case == "missing_manifest":
        plugin.mkdir()
    elif case == "other_plugin":
        _plugin(plugin, name="other")
    elif case == "skill_removed":
        _plugin(plugin, skill=None)
    env = {} if case == "unset" else {"BIOINFO_AGENT_DIR": str(plugin)}
    ctx = _ctx(tmp_path, _bioinfo_agent(), env)
    result = await get_adapter(Engine.claude_code, ctx.settings).run(ctx)
    assert not result.ok and expected in result.error
    assert str(plugin) not in result.error and str(tmp_path) not in result.error
    assert not any(ctx.workdir.iterdir())


def test_plugin_provenance_pins_version_and_content_without_paths(tmp_path):
    plugin = _plugin(tmp_path / "plugin")
    ctx = _ctx(tmp_path, _bioinfo_agent(), {"BIOINFO_AGENT_DIR": str(plugin)})
    adapter = get_adapter(Engine.claude_code, ctx.settings)
    assert adapter.preflight_error(ctx, ctx.env) is None
    [first] = ctx.plugin_provenance
    assert first["name"] == "bioinfo" and first["version"] == "0.4.9" and len(first["sha256"]) == 64
    assert str(tmp_path) not in json.dumps(first)
    (plugin / "skills" / "bioinfo-analyze" / "SKILL.md").write_text("changed gate\n")
    ctx2 = _ctx(tmp_path, _bioinfo_agent(), {"BIOINFO_AGENT_DIR": str(plugin)})
    assert adapter.preflight_error(ctx2, ctx2.env) is None
    assert ctx2.plugin_provenance[0]["sha256"] != first["sha256"]


@pytest.mark.parametrize("skills,message", [
    (["bioinfo-analyze"], "'plugin:skill'"),
    (["bioinfo:"], "'plugin:skill'"),
])
def test_required_skills_format(skills, message):
    with pytest.raises(ValidationError, match=message):
        AgentSpec(id="a", name="A", role="t", engine=Engine.claude_code, plugin_dirs=["x"], allow_skills=True,
                  required_skills=skills)


def test_required_skills_need_plugin_and_skill_exception():
    with pytest.raises(ValidationError, match="needs plugin_dirs and allow_skills"):
        AgentSpec(id="a", name="A", role="t", engine=Engine.claude_code, required_skills=["p:s"])


def test_core_bioinfo_agent_requires_its_skill():
    registry = Registry(ROOT / "agents", ROOT / "talent-unused")
    registry.load()
    assert registry.get("bioinfo-agent").required_skills == ["bioinfo:bioinfo-analyze"]


@pytest.mark.parametrize("field", ["plugin_dirs", "allow_skills"])
def test_plugin_options_reject_other_engines(field):
    value = ["${BIOINFO_AGENT_DIR}"] if field == "plugin_dirs" else True
    with pytest.raises(ValidationError, match="require engine: claude_code"):
        AgentSpec(id="a", name="A", role="test", engine=Engine.cli, **{field: value})


def test_redacted_real_plugin_init_is_minimal():
    path = ROOT / "tests/fixtures/real/claude_code/claude_plugin_skills.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    init = json.loads(lines[0])
    assert init["type"] == "system" and init["subtype"] == "init"
    assert init["skills"] == ["bioinfo:bioinfo-analyze"]
    assert "bioinfo-analyze" not in init["skills"]
    assert init["plugins"] == [{"name": "bioinfo"}]
    assert init["cwd"] == "<WORKDIR>"
    assert "path" not in init["plugins"][0] and "source" not in init["plugins"][0]


def test_plugin_init_redaction_discards_other_inventory():
    raw = {"type": "system", "subtype": "init", "cwd": "private/work",
           "skills": ["private-skill", "bioinfo:bioinfo-analyze"],
           "plugins": [{"name": "personal", "path": "private/personal"},
                       {"name": "bioinfo", "path": "private/plugin", "source": "bioinfo@inline"}]}
    redactor = Redactor(home="", tmp="", workdir="", username="", plugin_skill="bioinfo:bioinfo-analyze")
    init = json.loads(redactor.line(json.dumps(raw)))
    assert init == {"type": "system", "subtype": "init", "cwd": "<WORKDIR>",
                    "skills": ["bioinfo:bioinfo-analyze"], "plugins": [{"name": "bioinfo"}]}


@pytest.mark.parametrize("rel", [".mcp.json", ".lsp.json", "custom/cmds/run.md"])
def test_plugin_provenance_covers_root_config_and_manifest_paths(tmp_path, rel):
    from labhq.adapters.claude_code import plugin_provenance

    plugin = _plugin(tmp_path / "plugin")
    manifest = {"name": "bioinfo", "version": "1", "commands": "./custom/cmds/"}
    (plugin / ".claude-plugin" / "plugin.json").write_text(json.dumps(manifest))
    target = plugin / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text('{"a": 1}')
    before = plugin_provenance("bioinfo", plugin)["sha256"]
    target.write_text('{"a": 2}')
    after = plugin_provenance("bioinfo", plugin)["sha256"]
    assert after != before
    (plugin / "notes.txt").write_text("not loaded by Claude Code")
    assert plugin_provenance("bioinfo", plugin)["sha256"] == after


def test_plugin_provenance_is_recorded_before_spawn(tmp_path):
    plugin = _plugin(tmp_path / "plugin")
    recorded = []
    ctx = _ctx(tmp_path, _bioinfo_agent(), {"BIOINFO_AGENT_DIR": str(plugin)})
    ctx.record_run = lambda **fields: recorded.append(fields)
    assert get_adapter(Engine.claude_code, ctx.settings).preflight_error(ctx, ctx.env) is None
    assert recorded == [{"plugins": ctx.plugin_provenance}] and recorded[0]["plugins"][0]["name"] == "bioinfo"


def test_plugin_link_outside_the_plugin_is_refused_without_paths(tmp_path):
    plugin = _plugin(tmp_path / "plugin", skill=None)
    outside = tmp_path / "outside" / "SKILL.md"
    outside.parent.mkdir()
    outside.write_text("---\nname: x\n---\n")
    (plugin / "skills" / "bioinfo-analyze").mkdir(parents=True)
    try:
        (plugin / "skills" / "bioinfo-analyze" / "SKILL.md").symlink_to(outside)
    except OSError:
        pytest.skip("symlinks need extra privileges on this host")
    ctx = _ctx(tmp_path, _bioinfo_agent(), {"BIOINFO_AGENT_DIR": str(plugin)})
    err = get_adapter(Engine.claude_code, ctx.settings).preflight_error(ctx, ctx.env)
    assert err and "outside the plugin" in err and str(tmp_path) not in err


def test_git_plugin_provenance_covers_scripts_but_not_ignored_output(tmp_path):
    from labhq.adapters.claude_code import plugin_provenance

    plugin = _plugin(tmp_path / "plugin")
    (plugin / "scripts").mkdir()
    (plugin / "scripts" / "start.sh").write_text("echo 1\n")
    (plugin / ".gitignore").write_text("runs/\n")
    (plugin / "runs").mkdir()
    (plugin / "runs" / "big.bam").write_text("x")
    try:
        for args in (["init", "-q"], ["add", "."], ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "c"]):
            subprocess.run(["git", "-C", str(plugin), *args], check=True, capture_output=True)
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("git is not available")
    first = plugin_provenance("bioinfo", plugin)
    assert first["commit"] and len(first["commit"]) == 40
    (plugin / "runs" / "big.bam").write_text("changed run output")
    assert plugin_provenance("bioinfo", plugin)["sha256"] == first["sha256"]
    (plugin / "scripts" / "start.sh").write_text("echo 2\n")
    assert plugin_provenance("bioinfo", plugin)["sha256"] != first["sha256"]


def test_git_plugin_provenance_uses_index_executable_mode(tmp_path):
    from labhq.adapters.claude_code import plugin_provenance

    plugin = _plugin(tmp_path / "plugin")
    script = plugin / "hooks" / "start.sh"
    script.parent.mkdir()
    script.write_text("exit 0\n")
    try:
        subprocess.run(["git", "-C", str(plugin), "init", "-q"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(plugin), "add", "."], check=True, capture_output=True)
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("git is not available")
    before = plugin_provenance("bioinfo", plugin)
    subprocess.run(["git", "-C", str(plugin), "update-index", "--chmod=+x", "hooks/start.sh"],
                   check=True, capture_output=True)
    after = plugin_provenance("bioinfo", plugin)
    assert after["sha256"] != before["sha256"]
    assert str(tmp_path) not in json.dumps(after)


@pytest.mark.parametrize("git_checkout", [False, True])
def test_untracked_plugin_executable_mode_changes_hash_where_supported(tmp_path, git_checkout):
    from labhq.adapters.claude_code import plugin_provenance

    plugin = _plugin(tmp_path / "plugin")
    script = plugin / "hooks" / "start.sh"
    script.parent.mkdir()
    script.write_text("exit 0\n")
    if git_checkout:
        try:
            subprocess.run(["git", "-C", str(plugin), "init", "-q"], check=True, capture_output=True)
        except (OSError, subprocess.CalledProcessError):
            pytest.skip("git is not available")
    script.chmod(script.stat().st_mode & ~0o111)
    if script.stat().st_mode & 0o111:
        pytest.skip("filesystem cannot clear executable bits")
    before = plugin_provenance("bioinfo", plugin)["sha256"]
    script.chmod(script.stat().st_mode | 0o111)
    if not script.stat().st_mode & 0o111:
        pytest.skip("filesystem cannot set executable bits")
    assert plugin_provenance("bioinfo", plugin)["sha256"] != before


def test_skill_member_ignores_workspace_memory_files(tmp_path):
    ctx = _ctx(tmp_path, _bioinfo_agent(), {"BIOINFO_AGENT_DIR": str(_plugin(tmp_path / "plugin"))})
    cmd = get_adapter(Engine.claude_code, ctx.settings).build_command(ctx)
    ex = json.loads(cmd[cmd.index("--settings") + 1])["claudeMdExcludes"]
    wd = ctx.workdir.resolve()
    for name in ("CLAUDE.md", "CLAUDE.local.md", ".claude/CLAUDE.md"):
        assert (wd / name).as_posix() in ex
    assert (wd / ".claude" / "rules").as_posix() + "/**" in ex


@pytest.mark.parametrize("ignored_by_parent", [False, True])
def test_plugin_inside_a_parent_checkout_hashes_the_scripts_its_hooks_run(tmp_path, ignored_by_parent):
    import subprocess
    from labhq.adapters.claude_code import plugin_provenance
    repo = tmp_path / "parent"
    plugin = _plugin(repo / "plugins" / "bioinfo")
    (plugin / "hooks").mkdir()
    (plugin / "hooks" / "hooks.json").write_text('{"hooks": {"Stop": [{"command": "${CLAUDE_PLUGIN_ROOT}/scripts/run.sh"}]}}')
    (plugin / "scripts").mkdir()
    (plugin / "scripts" / "run.sh").write_text("echo one\n")
    (repo / "unrelated.txt").write_text("outside the plugin\n")
    if ignored_by_parent:
        (repo / ".gitignore").write_text("plugins/\n")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    before = plugin_provenance("bioinfo", plugin)["sha256"]
    (repo / "unrelated.txt").write_text("changed, still outside\n")
    assert plugin_provenance("bioinfo", plugin)["sha256"] == before
    (plugin / "scripts" / "run.sh").write_text("echo two\n")
    assert plugin_provenance("bioinfo", plugin)["sha256"] != before


@pytest.mark.parametrize("index_mode", ["100644", "100755"])
def test_tracked_plugin_hash_includes_unstaged_executable_bits(tmp_path, monkeypatch, index_mode):
    import os
    from labhq.adapters import claude_code

    plugin = _plugin(tmp_path / "plugin")
    script = plugin / "hooks" / "start.sh"
    script.parent.mkdir()
    script.write_text("exit 0\n")
    monkeypatch.setattr(claude_code, "_plugin_files", lambda path: ({script}, "fixed-commit"))
    monkeypatch.setattr(claude_code, "_git", lambda *args: f"{index_mode} hash 0\thooks/start.sh\0".encode())
    stat = Path.stat
    executable = False

    def working_stat(path, *args, **kwargs):
        result = stat(path, *args, **kwargs)
        if path == script:
            values = list(result)
            values[0] = (result.st_mode & ~0o111) | (0o111 if executable else 0)
            return os.stat_result(values)
        return result

    monkeypatch.setattr(Path, "stat", working_stat)
    before = claude_code.plugin_provenance("bioinfo", plugin)
    executable = True
    after = claude_code.plugin_provenance("bioinfo", plugin)
    assert after["sha256"] != before["sha256"]
    assert after["commit"] == before["commit"] == "fixed-commit"
    assert str(tmp_path) not in json.dumps(after)
    executable = False
    assert claude_code.plugin_provenance("bioinfo", plugin) == before
