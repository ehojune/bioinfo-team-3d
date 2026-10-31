"""The documented AlphaGenome key file is named like the variable; listing that one path is no override (#1225).

`labhq init` stores the key at `~/.labhq/secrets/alphagenome_api_key`. That path is among the private paths handed to
`labhq_annot` and to Claude's deny list, and the launch check used to refuse any launch setting that contained the
variable's letters: every staff run that mounts `annot` was refused before it started on a PC that had the key file.

The exception is the configured key file path and nothing else. No shape of text decides what counts as a path, so
`Env:\\ALPHAGENOME_API_KEY` or a look-alike path is refused like any other mention of the name.
"""
import pytest

from labhq.adapters import base as adapters_base
from labhq.adapters.codex import CodexAdapter
from labhq.models import McpServerSpec
from labhq.settings import Settings
from labhq.tools import annot_alphagenome_atlas as atlas

from tests.test_annot_alphagenome_atlas import context

KEY_FILE = "C:/Users/someone/.labhq/secrets/alphagenome_api_key"  # annot.alphagenome_key_file as the PC wrote it
KEY_FILE_BACKSLASH = KEY_FILE.replace("/", "\\")


def with_key_file_paths(ctx):
    """What a PC that ran `labhq init` hands the adapter: the configured key file among the private paths, both places,
    spelled with either slash."""
    ctx.settings.annot.alphagenome_key_file = KEY_FILE
    private = ";".join([KEY_FILE_BACKSLASH, KEY_FILE, "C:\\Users\\SOMEONE\\LABHQ~1\\secrets\\ALPHAG~1"])
    ctx.mcp_servers = [McpServerSpec(name="labhq_annot", command="python", args=["-m", "labhq.tools.annot_mcp"],
                                     env={"LABHQ_PRIVATE_PATHS": private, "LABHQ_ANNOT_TOOLS": "pdb,chembl"})]
    ctx.claude_settings = {"permissions": {"deny": [f"Read({KEY_FILE})", f"Edit({KEY_FILE_BACKSLASH})"]}}
    return ctx


def allowed_forms():
    settings = Settings()
    settings.annot.alphagenome_key_file = KEY_FILE
    return adapters_base.atlas_key_file_forms(settings)


async def run_until_preflight(monkeypatch, ctx):
    """Run the adapter up to its preflight, where the launch check has already passed (or refused)."""
    monkeypatch.setattr(CodexAdapter, "preflight_error", lambda self, ctx, env: "stopped at preflight")
    return await CodexAdapter(ctx.settings).run(ctx)


async def test_a_pc_with_the_key_file_still_launches_a_staff_run_that_mounts_annot(tmp_path, monkeypatch):
    ctx = with_key_file_paths(context(tmp_path))
    result = await run_until_preflight(monkeypatch, ctx)
    assert result.error == "stopped at preflight", "the key file's path was read as an override of the key"


def test_the_configured_key_file_path_is_not_the_variable_in_any_spelling():
    allowed = allowed_forms()
    for spelling in (KEY_FILE, KEY_FILE_BACKSLASH, KEY_FILE.upper(), KEY_FILE_BACKSLASH.lower()):
        assert not adapters_base.names_atlas_key(spelling, allowed)
        assert not adapters_base.names_atlas_key({"LABHQ_PRIVATE_PATHS": f"x;{spelling};y", "o": [spelling]}, allowed)


def test_every_rule_labhq_writes_for_the_key_file_is_allowed(tmp_path):
    """Claude's deny and ask rules spell the key file their own way (`Read(//c/Users/x/.labhq/secrets/...)`,
    `Bash(*.labhq/secrets/alphagenome_api_key*)`): whatever the function that writes them produces is the key file."""
    import os

    from labhq import policy

    settings = Settings()
    settings.annot.alphagenome_key_file = str(tmp_path / ".labhq" / "secrets" / "alphagenome_api_key")
    out = policy.claude_deny_private({}, [os.path.expanduser(settings.annot.alphagenome_key_file)])  # as the runner calls it
    rules = [rule for group in out["permissions"].values() for rule in group]
    named = [rule for rule in rules if "alphagenome_api_key" in rule.casefold()]
    assert named, "the generator must write rules that name the file, or this test checks nothing"
    allowed = adapters_base.atlas_key_file_forms(settings)
    assert not [rule for rule in named if adapters_base.names_atlas_key(rule, allowed)]
    assert adapters_base.names_atlas_key({"permissions": {"deny": [*named, "Read(//c/x/ALPHAGENOME_API_KEY)"]}},
                                         allowed), "a rule for any other path that carries the name is still refused"


@pytest.mark.parametrize("setting", [
    {"env": {"ALPHAGENOME_API_KEY": "anything"}},                       # the variable set in an MCP server's env
    {"env": {"alphagenome_api_key": "anything"}},                       # case does not matter for the name
    ["-c", 'mcp_servers.other.env_vars=["ALPHAGENOME_API_KEY"]'],       # listed by an engine argument
    ["-c", "shell_environment_policy.set.ALPHAGENOME_API_KEY=x"],       # set through the shell policy
    "${ALPHAGENOME_API_KEY}", "$ALPHAGENOME_API_KEY", "%ALPHAGENOME_API_KEY%",  # referenced
    "ALPHAGENOME_API_KEY",
    "Set-Item -Path Env:\\ALPHAGENOME_API_KEY -Value x",                # PowerShell's env drive path (#1228 review)
    "Set-Item -Path Env:/ALPHAGENOME_API_KEY -Value x",
    "[Environment]::SetEnvironmentVariable('ALPHAGENOME_API_KEY','x')",
    "C:/elsewhere/alphagenome_api_key",                                  # a path that is not the configured key file
    "x/ALPHAGENOME_API_KEY.bak",
])
def test_any_other_mention_of_the_variable_name_is_named_whatever_it_looks_like(setting):
    allowed = allowed_forms()
    assert adapters_base.names_atlas_key(setting, allowed)
    assert adapters_base.names_atlas_key({"outer": [setting]}, allowed)


async def test_a_launch_that_names_the_variable_is_still_refused_next_to_the_key_file_paths(tmp_path, monkeypatch):
    ctx = with_key_file_paths(context(tmp_path))
    ctx.mcp_servers[0].env[atlas.KEY_ENV] = "from-a-server-env"
    result = await run_until_preflight(monkeypatch, ctx)
    assert not result.ok and "Atlas" in result.error and "from-a-server-env" not in result.error


async def test_a_powershell_env_path_next_to_the_key_file_paths_is_still_refused(tmp_path, monkeypatch):
    ctx = with_key_file_paths(context(tmp_path))
    ctx.settings.engines.codex.prefix_args = ["Set-Item", "-Path", "Env:\\ALPHAGENOME_API_KEY", "-Value", "x"]
    result = await run_until_preflight(monkeypatch, ctx)
    assert not result.ok and "Atlas" in result.error


async def test_a_launch_that_carries_the_key_value_is_still_refused(tmp_path, monkeypatch):
    ctx = with_key_file_paths(context(tmp_path))
    monkeypatch.setenv(atlas.KEY_ENV, "value-of-the-key-0123456789")
    ctx.mcp_servers[0].env["LEAK"] = "value-of-the-key-0123456789"
    result = await run_until_preflight(monkeypatch, ctx)
    assert not result.ok and "Atlas" in result.error and "value-of-the-key" not in result.error


@pytest.mark.parametrize("configured", ["alphagenome_api_key", "secrets/alphagenome_api_key", "ALPHAGENOME_API_KEY"])
def test_a_relative_or_bare_key_file_setting_never_hides_the_variable_name(configured):
    """#1228 review: `annot.alphagenome_key_file: alphagenome_api_key` once put the bare file name among the allowed
    strings, and removing it from the launch erased the real variable `ALPHAGENOME_API_KEY` with it."""
    settings = Settings()
    settings.annot.alphagenome_key_file = configured
    forms = adapters_base.atlas_key_file_forms(settings)
    assert forms and all(adapters_base._anchored_path(form) or "(" in form for form in forms), forms
    assert configured not in forms and configured.lower() not in forms
    assert adapters_base.names_atlas_key({"env": {"ALPHAGENOME_API_KEY": "x"}}, forms)
    assert adapters_base.names_atlas_key(["-c", 'mcp_servers.o.env_vars=["ALPHAGENOME_API_KEY"]'], forms)


def test_a_failing_rule_generator_leaves_the_rules_out_and_refuses_more(monkeypatch):
    """The test guard refuses an operator `.labhq` path (RuntimeError) in CI: the rules are then simply not allowed."""
    from labhq import policy

    def refuse(*_args, **_kwargs):
        raise RuntimeError("pytest refused operator .labhq path")

    monkeypatch.setattr(policy, "claude_deny_private", refuse)
    settings = Settings()
    settings.annot.alphagenome_key_file = KEY_FILE
    forms = adapters_base.atlas_key_file_forms(settings)
    assert KEY_FILE in forms and not [form for form in forms if form.startswith(("Read(", "Bash("))]
    assert adapters_base.names_atlas_key(f"Read(//c/Users/someone/.labhq/secrets/alphagenome_api_key)", forms)


def test_a_settings_path_that_the_test_guard_refuses_only_leaves_that_form_out(monkeypatch):
    """CI (#1228): `Settings.path` raises RuntimeError for the operator's `~/.labhq/...` default under pytest. That one
    spelling is then not allowed (more is refused, never less); the others and the name check stay."""
    def refuse(self, text):
        raise RuntimeError("pytest refused operator .labhq path; use an isolated temporary home/state")

    monkeypatch.setattr(Settings, "path", refuse)
    settings = Settings()
    settings.annot.alphagenome_key_file = KEY_FILE
    forms = adapters_base.atlas_key_file_forms(settings)
    assert KEY_FILE in forms and KEY_FILE_BACKSLASH in forms
    assert not adapters_base.names_atlas_key(KEY_FILE, forms)
    assert adapters_base.names_atlas_key({"env": {"ALPHAGENOME_API_KEY": "x"}}, forms)
    settings.annot.alphagenome_key_file = "~/.labhq/secrets/alphagenome_api_key"
    assert adapters_base.atlas_key_file_forms(settings), "the ~ spelling alone still works"
