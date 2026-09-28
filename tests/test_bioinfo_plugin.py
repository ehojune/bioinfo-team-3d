"""The one Claude Code staff member allowed to load the approved bioinfo plugin skill."""

import json
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
        assert cmd[cmd.index("--setting-sources") + 1] == "project,local"
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
