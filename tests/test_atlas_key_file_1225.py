"""The documented AlphaGenome key file is named like the variable; a launch setting that lists its path is no override (#1225).

`labhq init` stores the key at `~/.labhq/secrets/alphagenome_api_key`. That path is among the private paths handed to
`labhq_annot` and to Claude's deny list, and the launch check used to refuse any launch setting that contained the
variable's letters: every staff run that mounts `annot` was refused before it started on a PC that had the key file.
"""
import pytest

from labhq.adapters import base as adapters_base
from labhq.adapters.codex import CodexAdapter
from labhq.models import McpServerSpec
from labhq.tools import annot_alphagenome_atlas as atlas

from tests.test_annot_alphagenome_atlas import context

KEY_FILE_POSIX = "/home/someone/.labhq/secrets/alphagenome_api_key"
KEY_FILE_WINDOWS = "C:\\Users\\someone\\.labhq\\secrets\\alphagenome_api_key"
KEY_FILE_SHORT = "C:\\Users\\SOMEONE\\LABHQ~1\\secrets\\ALPHAG~1"


def with_key_file_paths(ctx):
    """What a PC that ran `labhq init` hands the adapter: the key file among the private paths, in both places."""
    private = ";".join([KEY_FILE_WINDOWS, KEY_FILE_POSIX, KEY_FILE_SHORT])
    ctx.mcp_servers = [McpServerSpec(name="labhq_annot", command="python", args=["-m", "labhq.tools.annot_mcp"],
                                     env={"LABHQ_PRIVATE_PATHS": private, "LABHQ_ANNOT_TOOLS": "pdb,chembl"})]
    ctx.claude_settings = {"permissions": {"deny": [f"Read({KEY_FILE_POSIX})", f"Edit({KEY_FILE_WINDOWS})"]}}
    return ctx


async def run_until_preflight(monkeypatch, ctx):
    """Run the adapter up to its preflight, where the launch check has already passed (or refused)."""
    monkeypatch.setattr(CodexAdapter, "preflight_error", lambda self, ctx, env: "stopped at preflight")
    return await CodexAdapter(ctx.settings).run(ctx)


async def test_a_pc_with_the_key_file_still_launches_a_staff_run_that_mounts_annot(tmp_path, monkeypatch):
    ctx = with_key_file_paths(context(tmp_path))
    result = await run_until_preflight(monkeypatch, ctx)
    assert result.error == "stopped at preflight", "the key file's path was read as an override of the key"


@pytest.mark.parametrize("path", [KEY_FILE_POSIX, KEY_FILE_WINDOWS, KEY_FILE_SHORT,
                                  "~/.labhq/secrets/alphagenome_api_key", "x/ALPHAGENOME_API_KEY.bak"])
def test_a_path_named_like_the_variable_is_not_the_variable(path):
    assert not adapters_base.names_atlas_key(path)
    assert not adapters_base.names_atlas_key({"LABHQ_PRIVATE_PATHS": path, "other": [path]})


@pytest.mark.parametrize("setting", [
    {"env": {"ALPHAGENOME_API_KEY": "anything"}},                       # the variable set in an MCP server's env
    {"env": {"alphagenome_api_key": "anything"}},                       # case does not matter for the name
    ["-c", 'mcp_servers.other.env_vars=["ALPHAGENOME_API_KEY"]'],       # listed by an engine argument
    ["-c", "shell_environment_policy.set.ALPHAGENOME_API_KEY=x"],       # set through the shell policy
    "${ALPHAGENOME_API_KEY}", "$ALPHAGENOME_API_KEY", "%ALPHAGENOME_API_KEY%",  # referenced
    "ALPHAGENOME_API_KEY",
])
def test_the_variable_itself_set_listed_or_referenced_is_still_named(setting):
    assert adapters_base.names_atlas_key(setting)


async def test_a_launch_that_names_the_variable_is_still_refused_next_to_the_key_file_paths(tmp_path, monkeypatch):
    ctx = with_key_file_paths(context(tmp_path))
    ctx.mcp_servers[0].env[atlas.KEY_ENV] = "from-a-server-env"
    result = await run_until_preflight(monkeypatch, ctx)
    assert not result.ok and "Atlas" in result.error and "from-a-server-env" not in result.error


async def test_a_launch_that_carries_the_key_value_is_still_refused(tmp_path, monkeypatch):
    ctx = with_key_file_paths(context(tmp_path))
    monkeypatch.setenv(atlas.KEY_ENV, "value-of-the-key-0123456789")
    ctx.mcp_servers[0].env["LEAK"] = "value-of-the-key-0123456789"
    result = await run_until_preflight(monkeypatch, ctx)
    assert not result.ok and "Atlas" in result.error and "value-of-the-key" not in result.error
