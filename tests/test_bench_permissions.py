import asyncio
import json
from pathlib import Path
import shlex
import subprocess

import pytest

from labhq import bench
from labhq.bench_permissions import permits
from labhq.cli import main
from labhq.settings import Settings


def test_claude_dry_run_emits_confined_edits_commands_and_isolation(tmp_path, monkeypatch, capsys):
    seen = []
    original = bench._real_commands

    def commands(*args, **kwargs):
        result = original(*args, **kwargs)
        seen.append(result["sonnet-max"])
        return result

    monkeypatch.setattr(bench, "_real_commands", commands)
    monkeypatch.setattr(Settings, "load", lambda *_: Settings())
    monkeypatch.setattr(bench, "run_case", lambda *_a, **_k: pytest.fail("dry-run executed"))
    main(["bench", "run", "public-protein-qc", "--arms", "sonnet-max", "--dry-run",
          "--output", str(tmp_path)])
    assert "--permission-mode acceptEdits" in capsys.readouterr().out
    command = seen[0]
    settings = json.loads(command[command.index("--settings") + 1])
    assert command[command.index("--permission-mode") + 1] == "acceptEdits"
    assert command[command.index("--setting-sources") + 1] == "local"
    assert "--disable-slash-commands" in command
    assert settings["autoMemoryEnabled"] is False and settings["claudeMdExcludes"]
    assert settings["permissions"]["additionalDirectories"] == []
    assert command[command.index("--disallowedTools") + 1:] == ["Agent", "Task", "SendMessage", "TeamCreate"]
    allows = command[command.index("--allowedTools") + 1:command.index("--disallowedTools")]
    assert len(allows) == 3 and allows[0].startswith("Edit(//") and allows[0].endswith("/sonnet-max/**)")
    assert allows[1:] == ["Bash(pwd)", "Bash(wc -l *)"]
    assert not any(flag in command for flag in ("--add-dir", "--dangerously-skip-permissions"))
    hook = settings["hooks"]["PreToolUse"][0]
    assert hook["matcher"] == "Write|Edit|NotebookEdit|Bash|PowerShell"
    guard = shlex.split(hook["hooks"][0]["command"])
    root = Path(guard[-1])
    assert root.parent.parent.parent == tmp_path
    # Execute only the policy hook from the printed command, never a model CLI.
    for event, expected in [
        ({"tool_name": "Write", "tool_input": {"file_path": str(root / "answer.md")}}, "allow"),
        ({"tool_name": "Write", "tool_input": {"file_path": str(root.parent / "escape.md")}}, "deny"),
        ({"tool_name": "Bash", "tool_input": {"command": "echo report > answer.md"}}, "allow"),
        ({"tool_name": "Bash", "tool_input": {"command": "echo report > ../escape.md"}}, "deny"),
    ]:
        result = subprocess.run(guard, input=json.dumps(event), text=True, capture_output=True, check=True)
        assert json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"] == expected
    assert not any(tmp_path.iterdir())


@pytest.mark.parametrize("tool", ["Write", "Edit", "NotebookEdit"])
@pytest.mark.parametrize("path,expected", [("answer.md", True), ("nested/report.md", True),
                                          ("../escape.md", False), ("../sonnet-max-other/x", False),
                                          (".claude/settings.json", False), (".git/config", False)])
def test_file_tool_scope(tmp_path, tool, path, expected):
    assert permits(tmp_path, {"tool_name": tool, "tool_input": {
        "notebook_path" if tool == "NotebookEdit" else "file_path": path
    }}) is expected


@pytest.mark.parametrize("command,expected", [
    ("pwd", True), ("wc -l answer.md", True), ("mkdir -p figures", True),
    ("touch figures/output.md", True), ("echo report >> answer.md", True),
    ("echo report > ../escape.md", False), ("touch ../escape.md", False),
    ("mkdir -p ../other", False), ("wc -l ../other.md", False),
    ("cd .. && touch escape.md", False), ("echo $(touch ../escape.md)", False),
    ("python -c 'open(\"../escape.md\",\"w\")'", False),
    ("sh -c 'touch ../escape.md'", False), ("echo x > .claude/settings.local.json", False),
    ("echo x > output.md > ../escape.md", False), ("touch x; touch ../escape.md", False),
    ("echo x > /tmp/escape.md", False), ("echo x > ~/escape.md", False),
    ("touch --reference=../other new", False), ("wc -l *", False),
])
def test_simple_commands_stay_inside_arm(tmp_path, command, expected):
    assert permits(tmp_path, {"tool_name": "Bash", "tool_input": {"command": command}}) is expected


def test_resolved_symlink_cannot_grant_outside_writes(tmp_path):
    root = tmp_path / "arm"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    try:
        (root / "link").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink privilege unavailable")
    assert not permits(root, {"tool_name": "Write", "tool_input": {"file_path": "link/escape.md"}})
    assert not permits(root, {"tool_name": "Bash", "tool_input": {"command": "touch link/escape.md"}})


def test_baseline_process_working_directory_is_its_arm(tmp_path, monkeypatch):
    observed = []

    class Process:
        returncode = 0

        async def communicate(self):
            return b'{"type":"result","result":"report"}\n', b""

    async def spawn(*_args, **kwargs):
        observed.append(kwargs["cwd"])
        return Process()

    monkeypatch.setattr("labhq.adapters.base._resolve_command", lambda command, *_: command)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    asyncio.run(bench._run_baseline(bench.load_case("public-protein-qc"), "sonnet-max", tmp_path,
                                   "real", ["fake-claude"], Settings()))
    assert observed == [tmp_path]


@pytest.mark.parametrize("saved", [None, "", "# Saved report\n\nDetailed evidence.\n"])
def test_claude_file_artifact_survives_final_saved_message(tmp_path, monkeypatch, saved):
    class Process:
        returncode = 0

        async def communicate(self):
            return b'{"type":"result","result":"chat fallback","total_cost_usd":0.1}\n', b""

    async def spawn(*_args, **_kwargs):
        if saved is not None:
            (tmp_path / "answer.md").write_text(saved, encoding="utf-8")
        return Process()

    monkeypatch.setattr("labhq.adapters.base._resolve_command", lambda command, *_: command)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    result = asyncio.run(bench._run_baseline(bench.load_case("public-protein-qc"), "sonnet-max", tmp_path,
                                           "real", ["fake-claude"], Settings()))
    assert (tmp_path / "answer.md").read_text(encoding="utf-8") == (saved or "chat fallback\n")
    assert result["cost_usd"] == 0.1
    prompts = bench._real_commands(bench.load_case("public-protein-qc"), tmp_path)
    assert "answer.md" in prompts["sonnet-max"][prompts["sonnet-max"].index("-p") + 1]
