"""PI personal paths (policy.private_paths, PI decision 2026-10-03): staff run under the PI's own account, so
Claude file tools are denied these paths, a shell command naming one goes to the PI, and every staff member's
instructions list them as `~` labels."""

import json
import os
from pathlib import Path

import pytest

import labhq.private_paths as private_paths
from labhq import doctor
from labhq.adapters.base import ROLE_FOOTER, RunContext
from labhq.adapters.claude_code import ClaudeCodeAdapter
from labhq.adapters.codex import CodexAdapter
from labhq.models import AgentSpec, Engine, Task, TaskResult
from labhq.policy import _scan_path_text, claude_deny_private, evaluate_tool
from labhq.private_paths import (CONFIG_LABEL, DEFAULT_HOME_ENTRIES, ENV_VAR, OUTSIDE_HOME_LABEL, STATE_LABEL,
                                 configured_private_paths, mentioned_private_path, resolve_private_paths)
from labhq.runner.daemon import Runner
from labhq.settings import DataZone, PolicySettings, Settings

REAL_LABHQ_ENTRIES = private_paths._labhq_entries  # conftest replaces it for every other test
REAL_SHORT_NAME = private_paths._short_name

WIN_HOME = "C:\\Users\\pi"
WIN_PRIVATE = ["C:\\Users\\pi\\.ssh", "C:\\Users\\pi\\AppData\\Local\\Google\\Chrome\\User Data"]
WIN_ENV = {"LOCALAPPDATA": "C:\\Users\\pi\\AppData\\Local", "APPDATA": "C:\\Users\\pi\\AppData\\Roaming"}


@pytest.fixture(autouse=True)
def _no_short_names(monkeypatch):
    """pytest's temp folders have 8.3 names on Windows; tests that want short spellings set them explicitly."""
    monkeypatch.setattr(private_paths, "_short_name", lambda path: None)


def _home(tmp_path) -> Path:
    home = tmp_path / "home"
    (home / ".ssh").mkdir(parents=True)
    (home / ".ssh" / "id_ed25519").write_text("key", encoding="utf-8")
    (home / ".netrc").write_text("machine x", encoding="utf-8")
    (home / "AppData" / "Local" / "Google" / "Chrome" / "User Data").mkdir(parents=True)
    return home


def _link_dir(link: Path, target: Path) -> None:
    if os.name == "nt":  # a junction needs no symlink privilege
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
    else:
        os.symlink(target, link, target_is_directory=True)


# ---------------- configuration ----------------

def test_absent_key_means_the_default_list_and_an_empty_list_turns_it_off():
    assert Settings().policy.private_paths is None
    assert Settings.model_validate({"policy": {"private_paths": []}}).policy.private_paths == []
    assert Settings.model_validate({"policy": {"private_paths": ["~/x"]}}).policy.private_paths == ["~/x"]
    for entry in (".ssh", ".aws", ".gnupg", ".config/gh", ".git-credentials", ".netrc", ".claude", ".codex",
                  "AppData/Local/Google/Chrome/User Data", "AppData/Local/Microsoft/Edge/User Data",
                  "AppData/Roaming/Mozilla/Firefox"):
        assert entry in DEFAULT_HOME_ENTRIES


def test_default_list_keeps_what_exists_and_adds_labhq_config_and_gateway_state(tmp_path, monkeypatch):
    monkeypatch.setattr(private_paths, "_labhq_entries", REAL_LABHQ_ENTRIES)
    home = _home(tmp_path)
    config = tmp_path / "lab" / "labhq.yaml"
    config.parent.mkdir()
    config.write_text("gateway: {}\n", encoding="utf-8")
    (tmp_path / "gateway-state").mkdir()
    settings = Settings.model_validate({"gateway": {"state_dir": str(tmp_path / "gateway-state")}})
    settings.config_path = str(config)
    listed = configured_private_paths(settings, home=str(home))
    assert [label for _path, label in listed][:len(DEFAULT_HOME_ENTRIES)] == ["~/" + e for e in DEFAULT_HOME_ENTRIES]
    found = resolve_private_paths(settings, [tmp_path / "runs"], home=str(home))
    assert set(found.labels) == {"~/.ssh", "~/.netrc", "~/AppData/Local/Google/Chrome/User Data",
                                 CONFIG_LABEL, STATE_LABEL}  # missing ~/.aws, ~/.kube … are skipped quietly
    assert str(config) in found.paths and not found.skipped and found.enabled


def test_default_gateway_state_expands_tilde_against_the_runner_home(tmp_path, monkeypatch):
    monkeypatch.setattr(private_paths, "_labhq_entries", REAL_LABHQ_ENTRIES)
    monkeypatch.delenv("LABHQ_STATE_DIR", raising=False)
    home = _home(tmp_path)
    (home / ".labhq" / "state").mkdir(parents=True)
    found = resolve_private_paths(Settings(), [home / ".labhq" / "runs"], home=str(home))
    assert STATE_LABEL in found.labels and str(home / ".labhq" / "state") in found.paths


def test_override_list_expands_tilde_and_labels_outside_home_without_its_path(tmp_path):
    home = _home(tmp_path)
    (home / "secrets").mkdir()
    outside = tmp_path / "vault-notes"
    outside.mkdir()
    settings = Settings.model_validate({"policy": {"private_paths": ["~/secrets", str(outside), "~/missing"]}})
    found = resolve_private_paths(settings, [tmp_path / "runs"], home=str(home))
    assert found.paths == (str(home / "secrets"), str(outside))
    assert found.labels == ("~/secrets", f"{OUTSIDE_HOME_LABEL} …/vault-notes")  # the folder, not its path
    off = Settings.model_validate({"policy": {"private_paths": []}})
    assert resolve_private_paths(off, [], home=str(home)) == private_paths.PrivatePaths(enabled=False)


def test_an_entry_that_holds_or_is_a_work_folder_is_skipped(tmp_path):
    home = _home(tmp_path)
    (home / ".labhq" / "runs" / "task").mkdir(parents=True)
    (home / "refs").mkdir()
    settings = Settings.model_validate({"policy": {"private_paths": ["~/.labhq", "~/refs", "~/.ssh"]}})
    found = resolve_private_paths(settings, [home / ".labhq" / "runs" / "task", home / "refs"], home=str(home))
    assert found.labels == ("~/.ssh",)
    assert found.skipped == ("~/.labhq", "~/refs")


def test_a_linked_entry_is_judged_and_ruled_by_its_real_path_too(tmp_path):
    home = _home(tmp_path)
    (tmp_path / "real-keys").mkdir()
    _link_dir(home / ".gnupg", tmp_path / "real-keys")
    (tmp_path / "shared" / "work").mkdir(parents=True)
    _link_dir(home / "shared-link", tmp_path / "shared")
    settings = Settings.model_validate({"policy": {"private_paths": ["~/.gnupg", "~/shared-link"]}})
    found = resolve_private_paths(settings, [tmp_path / "shared" / "work"], home=str(home))
    assert found.labels == ("~/.gnupg",) and found.skipped == ("~/shared-link",)
    assert str(home / ".gnupg") in found.paths
    assert any(Path(p).resolve() == (tmp_path / "real-keys").resolve() and Path(p) != home / ".gnupg"
               for p in found.paths)


# ---------------- Claude deny rules ----------------

def test_claude_rules_use_the_double_slash_form_for_windows_and_posix_paths():
    win = claude_deny_private({}, ["C:\\Users\\pi\\.ssh", "C:\\Users\\pi\\.netrc"])["permissions"]["deny"]
    for tool in ("Read", "Edit", "Write"):
        assert f"{tool}(//c/Users/pi/.ssh)" in win and f"{tool}(//c/Users/pi/.ssh/**)" in win
        assert f"{tool}(//c/Users/pi/.netrc)" in win
    posix = claude_deny_private({}, ["/home/pi/.aws"])["permissions"]["deny"]
    assert posix == [f"{tool}(//home/pi/.aws{tail})" for tool in ("Read", "Edit", "Write") for tail in ("", "/**")]


def test_claude_rules_merge_into_existing_settings_and_skip_unc():
    base = {"permissions": {"deny": ["Read(//data/cohort/**)"]}, "other": 1}
    merged = claude_deny_private(base, ["\\\\server\\share\\keys", "/home/pi/.ssh"])
    deny = merged["permissions"]["deny"]
    assert deny[0] == "Read(//data/cohort/**)" and merged["other"] == 1
    assert "Read(//home/pi/.ssh/**)" in deny and not any("server" in rule for rule in deny)
    assert claude_deny_private(base, []) is base


# ---------------- approval gate ----------------

def _gate(tool, tool_input, private=WIN_PRIVATE, home=WIN_HOME, environ=WIN_ENV, policy=None, roots=("/work/t1",)):
    return evaluate_tool(tool, tool_input, policy or PolicySettings(), allowed_roots=list(roots), workdir="/work/t1",
                         windows=False, environ=environ, private_paths=private, home=home)


@pytest.mark.parametrize("tool,command", [
    ("Bash", "cat C:\\Users\\pi\\.ssh\\id_rsa"),
    ("Bash", "cat c:/users/PI/.SSH/id_rsa"),
    ("Bash", "cat ~/.ssh/id_rsa"),
    ("Bash", "ls ~/.ssh"),
    ("Bash", "cat $HOME/.ssh/config"),
    ("Bash", "ls ${HOME}/.ssh"),
    ("Bash", "ls /c/Users/pi/.ssh"),
    ("Bash", "ls /mnt/c/Users/pi/.ssh/"),
    ("Bash", "cmd /c type %USERPROFILE%\\.ssh\\id_rsa"),
    ("Bash", "ls ~/AppData/Local/Google/Chrome/User\\ Data/Default"),
    ("Bash", "cp 'C:\\Users\\pi\\AppData\\Local\\Google\\Chrome\\User Data\\Default\\Cookies' ./outputs/"),
    ("PowerShell", "Get-Content $env:USERPROFILE\\.ssh\\id_rsa"),
    ("PowerShell", "Get-ChildItem ${env:USERPROFILE}/.ssh"),
    ("PowerShell", 'dir "$env:LOCALAPPDATA\\Google\\Chrome\\User Data"'),
    ("PowerShell", "dir %LOCALAPPDATA%\\Google\\Chrome\\User Data\\Default"),
])
def test_a_shell_command_naming_a_private_path_goes_to_the_pi(tool, command):
    decision = _gate(tool, {"command": command})
    assert decision.action == "ask" and "private_paths" in decision.reason


@pytest.mark.parametrize("command", [
    "ls ~/.sshd_config", "cat ~/projects/.ssh-notes.md", "echo .ssh", "python run.py --out outputs/x.tsv",
    "ls ~", "cat /work/t1/notes.md", "ls C:/Users/pi/AppData/Local/Google/Chrome-notes",
])
def test_unrelated_shell_commands_keep_their_decision(command):
    assert _gate("Bash", {"command": command}).action == "allow"
    assert _gate("Bash", {"command": command}, private=[]).action == "allow"


def test_posix_spellings_go_to_the_pi():
    for command in ("cat ~/.aws/credentials", "cat /home/pi/.aws/credentials", "cat $HOME/.aws/credentials",
                    'cat "${HOME}/.aws/credentials"'):
        assert _gate("Bash", {"command": command}, private=["/home/pi/.aws"], home="/home/pi",
                     environ={}).action == "ask", command
    assert _gate("Bash", {"command": "cat ~/.aws_old"}, private=["/home/pi/.aws"], home="/home/pi",
                 environ={}).action == "allow"


def test_file_tools_on_a_private_path_are_denied_but_content_mentioning_one_is_not():
    for tool, tool_input in (("Read", {"file_path": "C:\\Users\\pi\\.ssh\\id_rsa"}),
                             ("Read", {"file_path": "~/.ssh/config"}),
                             ("Glob", {"pattern": "C:/Users/pi/.ssh/*"}),
                             ("Grep", {"pattern": "BEGIN", "path": "c:\\users\\pi\\.ssh"}),
                             ("Write", {"file_path": "C:/Users/pi/.ssh/authorized_keys", "content": "x"})):
        decision = _gate(tool, tool_input)
        assert decision.action == "deny" and "private_paths" in decision.reason, (tool, tool_input)
    assert _gate("Write", {"file_path": "/work/t1/README.md", "content": "keys live in ~/.ssh"}).action == "allow"
    assert _gate("Grep", {"pattern": "~/.ssh", "path": "/work/t1"}).action == "allow"


def test_the_private_rule_never_loosens_an_existing_decision():
    policy = PolicySettings(data_zones=[DataZone(path="/data/cohort")])
    zone_and_private = _gate("Bash", {"command": "cat /data/cohort/a.tsv ~/.ssh/id_rsa"}, policy=policy)
    assert zone_and_private.action == "ask" and "restricted" in zone_and_private.reason
    assert _gate("Read", {"file_path": "/data/cohort/a.tsv"}, policy=policy).action == "deny"
    # A write outside the roots used to ask; on a private path it is now refused outright.
    assert _gate("Write", {"file_path": "/tmp/x", "content": "y"}).action == "ask"
    assert _gate("Bash", {"command": "rm -rf ~/.ssh"}).action == "ask"


def test_mentioned_private_path_returns_the_path_it_matched():
    assert mentioned_private_path("tar czf a.tgz ~/.ssh", WIN_PRIVATE, WIN_HOME, {}) == WIN_PRIVATE[0]
    assert mentioned_private_path("tar czf a.tgz ./data", WIN_PRIVATE, WIN_HOME, {}) is None


# ---------------- staff instructions ----------------

def _ctx(tmp_path, engine, labels):
    agent = AgentSpec(id="worker", name="Worker", role="test", engine=engine, system_prompt="ROLE")
    workdir = tmp_path / "wd"
    workdir.mkdir(parents=True, exist_ok=True)
    return RunContext(task=Task(agent_id="worker", prompt="go"), agent=agent, workdir=workdir, settings=Settings(),
                      mcp_servers=[], env={}, emit=None, prompt="go", private_labels=labels)


@pytest.mark.parametrize("engine,adapter,read", [
    (Engine.codex, CodexAdapter, lambda wd: (wd / "AGENTS.md").read_text(encoding="utf-8")),
    (Engine.claude_code, ClaudeCodeAdapter, lambda wd: (wd / ".labhq" / "system_prompt.md").read_text(encoding="utf-8")),
])
def test_every_staff_instruction_lists_private_paths_as_tilde_labels(tmp_path, engine, adapter, read):
    labels = ["~/.ssh", "~/AppData/Local/Google/Chrome/User Data", CONFIG_LABEL]
    ctx = _ctx(tmp_path, engine, labels)
    adapter(Settings()).prepare(ctx)
    text = read(ctx.workdir)
    assert "## PI 개인 파일" in text and "`~/.ssh`" in text and f"`{CONFIG_LABEL}`" in text
    home = str(Path.home())
    assert home not in text and Path.home().as_posix() not in text
    plain = _ctx(tmp_path / "plain", engine, [])
    adapter(Settings()).prepare(plain)
    assert read(plain.workdir).endswith(ROLE_FOOTER)  # nothing configured: the footer is unchanged


# ---------------- runner ----------------

def _runner_settings(tmp_path, private):
    settings = Settings()
    for name in ("state_dir", "workspace_root", "agents_dir", "talent_dir"):
        setattr(settings.runner, name, str(tmp_path / name))
    settings.engines.codex.env = {"CODEX_HOME": str(tmp_path / "codex-home")}
    settings.policy.private_paths = private
    return settings


def _capture_runner(settings, monkeypatch, engine=Engine.claude_code):
    agent = AgentSpec(id="worker", name="Worker", role="test", engine=engine, builtin_mcp=[],
                      system_prompt="You are the worker.")
    runner = Runner(settings)
    monkeypatch.setattr(runner.registry, "get", lambda _id: agent)
    seen = {}

    class Adapter:
        async def run(self, ctx):
            seen["ctx"] = ctx
            return TaskResult(task_id=ctx.task.id, agent_id=ctx.agent.id, ok=True, text="done")

    monkeypatch.setattr("labhq.runner.daemon.get_adapter", lambda *_args: Adapter())
    return runner, seen


@pytest.mark.asyncio
async def test_runner_gives_claude_rules_the_gate_list_and_the_labels(tmp_path, monkeypatch):
    home = _home(tmp_path)
    monkeypatch.setattr(private_paths, "host_home", lambda: str(home))
    settings = _runner_settings(tmp_path, ["~/.ssh", str(tmp_path)])  # tmp_path holds the workspace: skipped
    runner, seen = _capture_runner(settings, monkeypatch)
    for task_id in ("t1", "t2"):
        result = await runner.run_task(Task(id=task_id, agent_id="worker", request_id="r", prompt="q"))
        assert result.ok, result.error
    ctx = seen["ctx"]
    assert ctx.private_labels == ["~/.ssh"]
    deny = ctx.claude_settings["permissions"]["deny"]
    rule = f"Read(/{private_paths_rule(home / '.ssh')}/**)"
    assert rule in deny
    assert ctx.env[ENV_VAR].split(os.pathsep) == [str(home / ".ssh")]
    warned = [e["data"]["text"] for e in runner.store.pending()
              if e["type"] == "agent.log" and "개인 경로 차단에서 제외" in e["data"].get("text", "")]
    assert len(warned) == 1 and OUTSIDE_HOME_LABEL in warned[0], "said once, not on every task"


def private_paths_rule(path: Path) -> str:
    from labhq.policy import claude_rule_path

    return claude_rule_path(str(path))


@pytest.mark.asyncio
async def test_runner_leaves_codex_home_open_to_a_codex_task(tmp_path, monkeypatch):
    home = _home(tmp_path)
    monkeypatch.setattr(private_paths, "host_home", lambda: str(home))
    (home / ".codex").mkdir()
    settings = _runner_settings(tmp_path, ["~/.codex", "~/.ssh"])
    settings.engines.codex.env = {}  # the staff Codex logs in with ~/.codex
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.delenv("CODEX_HOME", raising=False)
    runner, seen = _capture_runner(settings, monkeypatch, engine=Engine.codex)
    assert (await runner.run_task(Task(id="t1", agent_id="worker", request_id="r", prompt="q"))).ok
    assert seen["ctx"].private_labels == ["~/.ssh"]
    runner2, seen2 = _capture_runner(settings, monkeypatch, engine=Engine.claude_code)
    assert (await runner2.run_task(Task(id="t2", agent_id="worker", request_id="r", prompt="q"))).ok
    assert seen2["ctx"].private_labels == ["~/.codex", "~/.ssh"]  # Claude staff never need the Codex login


# ---------------- doctor ----------------

def _doctor_settings(tmp_path, private):
    agents = tmp_path / "agents" / "core"
    agents.mkdir(parents=True)
    (agents / "worker.yaml").write_text("id: worker\nname: Worker\nrole: test\nengine: claude_code\n",
                                        encoding="utf-8")
    return Settings.model_validate({"runner": {"state_dir": str(tmp_path / "state"),
                                               "workspace_root": str(tmp_path / "runs"),
                                               "agents_dir": str(agents.parent)},
                                    "gateway": {"state_dir": str(tmp_path / "gateway")},
                                    "hpc": {"scheduler": "none"},
                                    "policy": {"private_paths": private}})


def _doctor_row(settings, monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", lambda *a, **kw: None)
    rows = doctor.collect(settings, dry_run=True)["checks"]
    assert next(r for r in rows if r["name"] == "runner account isolation")  # kept
    return next(r for r in rows if r["name"] == "private paths")


@pytest.mark.parametrize("private,status,detail", [
    (["~/.ssh", "~/.netrc"], "ok", "2 active"),
    (["~/.ssh", "TMP"], "ok", "1 active; skipped, holds a work folder: OUTSIDE"),
    (["TMP"], "warn", "0 active; skipped, holds a work folder: OUTSIDE"),
    ([], "warn", "off (policy.private_paths: [])"),
    (["~/.kube"], "warn", "0 active"),
])
def test_doctor_reports_active_and_skipped_private_paths(tmp_path, monkeypatch, private, status, detail):
    home = _home(tmp_path)
    monkeypatch.setattr(private_paths, "host_home", lambda: str(home))
    settings = _doctor_settings(tmp_path, [str(tmp_path) if p == "TMP" else p for p in private])
    row = _doctor_row(settings, monkeypatch)
    assert (row["status"], row["detail"]) == (status, detail.replace("OUTSIDE", f"{OUTSIDE_HOME_LABEL} …/{tmp_path.name}"))
    assert "README" in row["hint"] and str(home) not in row["detail"]


# ---------------- review of PR #324 ----------------

def _claude_matches(rule: str, tool: str, command: str) -> bool:
    """Claude's wildcard rule match as probed on 2.1.282: `*` is any text, the rest is literal."""
    import re

    if not rule.startswith(tool + "(") or not rule.endswith(")"):
        return False
    body = rule[len(tool) + 1:-1]
    return re.fullmatch(".*".join(re.escape(part) for part in body.split("*")), command, re.S) is not None


@pytest.mark.parametrize("tool,command", [
    ("Bash", "python -c \"print(open(r'C:/Users/pi/.ssh/id_rsa').read())\""),
    ("Bash", "python -c \"print(open(r'C:\\Users\\pi\\.ssh\\id_rsa').read())\""),
    ("Bash", "ls ~/.ssh"),
    ("Bash", "ls /c/Users/pi/.ssh"),
    ("Bash", "python -c \"import shutil; shutil.copy(r'C:\\Users\\pi\\AppData\\Local\\Google\\Chrome\\User Data\\Default\\Cookies', 'x')\""),
    ("Bash", "cat \"$LOCALAPPDATA\"/Google/Chrome/\"User Data\"/Default/Cookies"),
    ("PowerShell", "Get-Content $env:USERPROFILE\\.ssh\\id_rsa"),
    ("Bash", "python -c \"print(open('D:/vault/notes.txt').read())\""),
    ("Bash", "cat /d/vault/notes.txt"),
    ("Bash", "cat /mnt/d/vault/notes.txt"),
])
def test_ask_rules_catch_a_pre_approved_shell_command_naming_a_private_path(tool, command):
    """A Bash pattern in --allowedTools (`Bash(python *)` on the analyst) skips the gate; an ask rule outranks it."""
    ask = claude_deny_private({}, [*WIN_PRIVATE, "D:\\vault"], home=WIN_HOME)["permissions"]["ask"]
    assert any(_claude_matches(rule, tool, command) for rule in ask), command
    assert "Bash(*.ssh*)" in ask and "PowerShell(*.ssh*)" in ask and "Bash(*User Data*)" in ask


def test_ask_rules_leave_other_commands_and_merge_into_existing_ask_rules():
    base = {"permissions": {"ask": ["Bash(git push *)"], "deny": ["Read(//data/x/**)"]}}
    merged = claude_deny_private(base, WIN_PRIVATE, home=WIN_HOME)["permissions"]
    assert merged["ask"][0] == "Bash(git push *)" and merged["deny"][0] == "Read(//data/x/**)"
    for command in ("python run.py --out outputs/x.tsv", "ls ~", "Rscript analysis.R"):
        assert not any(_claude_matches(rule, "Bash", command) for rule in merged["ask"]), command
    # A personal path outside home with a UNC spelling has no deny rule but still gets ask rules.
    unc = claude_deny_private({}, ["\\\\server\\share\\keys"], home=WIN_HOME)["permissions"]
    assert "deny" not in unc and "Bash(*/server/share/keys*)" in unc["ask"]


@pytest.mark.parametrize("tool,command", [
    ("Bash", 'cat "$HOME"/.ssh/id_rsa'),
    ("Bash", "cat \"$LOCALAPPDATA\"/Google/Chrome/\"User Data\"/Default/Cookies"),
    ("Bash", "cat $LOCALAPPDATA/Google/Chrome/'User Data'/Default/Cookies"),
    ("Bash", "cat C:/Users/pi/./.ssh/id_rsa"),
    ("Bash", "cat ~/projects/../.ssh/id_rsa"),
    ("Bash", "cat C:/Users/pi/AppData/../.ssh/id_rsa"),
    ("Bash", "cat //localhost/C$/Users/pi/.ssh/id_rsa"),
    ("Bash", "type \\\\127.0.0.1\\c$\\Users\\pi\\.ssh\\id_rsa"),
    ("Bash", "cat '\\\\?\\C:\\Users\\pi\\.ssh\\id_rsa'"),
    ("PowerShell", "Get-Content $env:HOMEDRIVE$env:HOMEPATH\\.ssh\\id_rsa"),
    ("PowerShell", "Get-Content \\\\?\\UNC\\localhost\\C$\\Users\\pi\\.ssh\\id_rsa"),
])
def test_quoted_dotted_and_admin_share_spellings_go_to_the_pi(tool, command):
    decision = _gate(tool, {"command": command})
    assert decision.action == "ask" and "private_paths" in decision.reason


@pytest.mark.parametrize("command", [
    "echo \"it's done\" > notes.md", "cat ./a/../b.txt", "ls ~/projects/..", "cat //localhost/C$/data/x.tsv",
])
def test_the_new_spellings_do_not_catch_unrelated_commands(command):
    assert _gate("Bash", {"command": command}).action == "allow"


@pytest.mark.parametrize("path", [
    "\\\\localhost\\C$\\Users\\pi\\.ssh\\id_rsa", "\\\\127.0.0.1\\c$\\Users\\pi\\.ssh\\id_rsa",
    "\\\\?\\C:\\Users\\pi\\.ssh\\id_rsa", "//localhost/c$/Users/pi/.ssh/id_rsa",
])
def test_file_tools_on_an_admin_share_or_prefixed_spelling_are_denied(path):
    decision = _gate("Read", {"file_path": path})
    assert decision.action == "deny" and "private_paths" in decision.reason


def test_short_names_are_ruled_and_matched_like_the_long_path(tmp_path, monkeypatch):
    home = _home(tmp_path)
    ssh = str(home / ".ssh")
    monkeypatch.setattr(private_paths, "_short_name",
                        lambda path: str(home / "SSH~1") if os.path.normcase(path) == os.path.normcase(ssh) else None)
    found = resolve_private_paths(Settings.model_validate({"policy": {"private_paths": ["~/.ssh"]}}), [],
                                  home=str(home))
    assert found.labels == ("~/.ssh",) and found.paths == (ssh, str(home / "SSH~1"))
    deny = claude_deny_private({}, found.paths, home=str(home))["permissions"]["deny"]
    assert f"Read(/{private_paths_rule(home / 'SSH~1')}/**)" in deny
    decision = evaluate_tool("Bash", {"command": "cat ~/SSH~1/id_ed25519"}, PolicySettings(),
                             allowed_roots=["/work/t1"], workdir="/work/t1", windows=False, environ={},
                             private_paths=found.paths, home=str(home))
    assert decision.action == "ask"


@pytest.mark.skipif(os.name != "nt", reason="8.3 names are a Windows feature")
def test_short_spellings_use_the_real_windows_short_name(tmp_path, monkeypatch):
    monkeypatch.setattr(private_paths, "_short_name", REAL_SHORT_NAME)
    folder = tmp_path / "Long Folder Name"
    folder.mkdir()
    short = REAL_SHORT_NAME(str(folder))
    if not short:
        pytest.skip("8.3 names are off on this volume")
    spellings = private_paths.short_spellings(str(folder))
    assert short in spellings and os.path.join(str(tmp_path), os.path.basename(short)) in spellings
    assert all(Path(s).resolve() == folder.resolve() for s in spellings)


def test_outside_home_labels_name_only_the_last_folder():
    assert private_paths._label("D:\\pi-private", WIN_HOME) == f"{OUTSIDE_HOME_LABEL} …/pi-private"
    assert private_paths._label("D:\\", WIN_HOME) == OUTSIDE_HOME_LABEL


@pytest.mark.asyncio
async def test_runner_gives_claude_the_ask_rules_and_keeps_the_paper2agent_skill_open_to_recruit(tmp_path,
                                                                                               monkeypatch):
    home = _home(tmp_path)
    skill = home / ".claude" / "skills" / "paper2agent"
    skill.mkdir(parents=True)
    monkeypatch.setattr(private_paths, "host_home", lambda: str(home))
    monkeypatch.setattr("labhq.recruit.paper2agent.skill_path", lambda engine: skill)
    settings = _runner_settings(tmp_path, ["~/.claude", "~/.ssh"])
    runner, seen = _capture_runner(settings, monkeypatch)
    assert (await runner.run_task(Task(id="t1", agent_id="worker", request_id="r", prompt="q"))).ok
    ctx = seen["ctx"]
    assert ctx.private_labels == ["~/.claude", "~/.ssh"]
    assert "Bash(*.ssh*)" in ctx.claude_settings["permissions"]["ask"]
    recruit = Task(id="t2", agent_id="worker", request_id="r", prompt="q", meta={"kind": "recruit"})
    assert (await runner.run_task(recruit)).ok
    assert seen["ctx"].private_labels == ["~/.ssh"]  # the skill's files sit under ~/.claude


# ---------------- review of PR #324, round 3: shell and outside reads always reach the gate ----------------

def _allowed(tmp_path, tools, private, read_dirs=()):
    from labhq.adapters import get_adapter

    agent = AgentSpec(id="analyst", name="A", role="test", engine=Engine.claude_code, tools=list(tools),
                      builtin_mcp=["approval"])
    (tmp_path / "ws" / ".labhq").mkdir(parents=True, exist_ok=True)
    ctx = RunContext(task=Task(agent_id=agent.id, prompt="x"), agent=agent, workdir=tmp_path / "ws",
                     settings=Settings(), mcp_servers=[], env={}, emit=None, prompt="x",
                     read_dirs=[str(d) for d in read_dirs], claude_settings={}, private_paths=list(private))
    cmd = get_adapter(agent.engine, Settings()).build_command(ctx)
    i = cmd.index("--allowedTools")
    end = cmd.index("--disallowedTools") if "--disallowedTools" in cmd else len(cmd)
    return cmd[i + 1:end]


def test_no_shell_rule_is_pre_approved_and_reads_narrow_to_task_roots_while_private_paths_are_active(tmp_path):
    """Claude matches rule text literally, so a case-varied or aliased spelling passed the deny and ask rules
    while `Bash(python *)` and bare `Read` skipped the gate. With private paths active neither is pre-approved."""
    from labhq.policy import claude_allowed_tools, claude_rule_path, rule_tool

    ref = tmp_path / "ref"
    tools = ["Read", "Grep", "Glob", "Read(//**)", "Write", "Bash", "Bash(python *)", "PowerShell(Get-Content *)",
             "WebSearch"]
    allowed = _allowed(tmp_path, tools, ["C:\\Users\\pi\\.ssh"], read_dirs=[ref])
    assert not [t for t in allowed if rule_tool(t) in {"Bash", "PowerShell"}]
    assert not {"Read", "Grep", "Glob", "Read(//**)", "Write"} & set(allowed)
    ws = str((tmp_path / "ws").resolve())
    assert f"Read(/{claude_rule_path(ws)}/**)" in allowed and f"Read(/{claude_rule_path(str(ref))}/**)" in allowed
    assert f"Edit(/{claude_rule_path(ws)}/**)" in allowed and "WebSearch" in allowed
    assert not any(r.startswith("Edit(") and r.endswith("/ref/**)") for r in allowed)  # references stay read-only
    # Private paths off: the PI's pre-approvals are kept as they were.
    assert _allowed(tmp_path, ["Read", "Bash(python *)"], []) == ["Read", "Bash(python *)"]
    assert claude_allowed_tools(["Bash(ls *)", "WebSearch"], [tmp_path], read_roots=[tmp_path]) == ["WebSearch"]
    fixture = Path(__file__).parent / "fixtures" / "real" / "claude_code" / "claude_read_scope_alias.json"
    probes = {p["id"]: p for p in json.loads(fixture.read_text(encoding="utf-8"))["probes"]}
    assert probes["bare_read_alias"]["pre_approved"] and not probes["scoped_read_alias"]["pre_approved"]
    assert all(probes[k]["pre_approved"] for k in ("scoped_read_inside", "scoped_read_grep", "scoped_read_glob"))


@pytest.mark.parametrize("tool,command", [
    ("Bash", "python script.py --out outputs/x.tsv"), ("Bash", "Rscript analysis.R"), ("Bash", "ls outputs"),
    ("Bash", "cat /work/t1/notes.md"), ("PowerShell", "Get-Content notes.md"), ("Bash", "python -c \"print(1)\""),
])
def test_the_gate_allows_plain_workdir_commands_without_a_pi_prompt(tool, command):
    assert _gate(tool, {"command": command}).action == "allow"
    for tool_name, tool_input in (("Read", {"file_path": "notes.md"}), ("Grep", {"pattern": "x", "path": "."}),
                                  ("Glob", {"pattern": "outputs/*.tsv"})):
        assert _gate(tool_name, tool_input).action == "allow"


@pytest.mark.parametrize("tool,command", [
    ("Bash", "cat C:/USERS/PI/.SSH/id_rsa"),
    ("Bash", "python -c \"print(open(r'c:\\users\\pi\\.Ssh\\id_rsa').read())\""),
    ("Bash", "ls ~/.SSH"),
    ("PowerShell", "Get-Content C:\\USERS\\PI\\.SSH\\ID_RSA"),
])
def test_case_varied_windows_spellings_miss_the_literal_ask_rules_but_the_gate_asks(tool, command):
    ask = claude_deny_private({}, WIN_PRIVATE, home=WIN_HOME)["permissions"]["ask"]
    assert not any(_claude_matches(rule, tool, command) for rule in ask)  # why shell must reach the gate
    decision = _gate(tool, {"command": command})
    assert decision.action == "ask" and "private_paths" in decision.reason


@pytest.mark.parametrize("command, path", [
    (r'''python -c "open(r'C:\Users\pi\ws\alias\id_ed25519')"''', r"C:\Users\pi\ws\alias\id_ed25519"),
    (r'''python -c "open(r'/tmp/ws/alias/id_ed25519')"''', "/tmp/ws/alias/id_ed25519"),
    (r'''python -c "print(open(b'/home/pi/.ssh/key').read())"''', "/home/pi/.ssh/key"),
    (r'''python -c "open('D:/data/x.tsv')"''', "D:/data/x.tsv"),
])
def test_a_quoted_string_inside_a_quoted_command_is_one_path_candidate(command, path):
    # #324 CI: the outer quotes hid the inner ones, so `C:\...` split at the colon into `r'C` and a drive-less
    # `\Users\...` that resolved against the current drive; on D: runners the alias check then saw nothing.
    assert path in _scan_path_text(command).candidates


def test_the_gate_catches_a_link_alias_into_a_private_path(tmp_path):
    home = _home(tmp_path)
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "notes.md").write_text("n", encoding="utf-8")
    _link_dir(ws / "alias", home / ".ssh")
    found = resolve_private_paths(Settings.model_validate({"policy": {"private_paths": ["~/.ssh"]}}), [ws],
                                  home=str(home))

    def gate(tool, tool_input):
        return evaluate_tool(tool, tool_input, PolicySettings(), allowed_roots=[str(ws)], workdir=str(ws),
                             environ={}, private_paths=found.paths, home=str(home))

    for tool, tool_input in (("Read", {"file_path": "alias/id_ed25519"}),
                             ("Read", {"file_path": str(ws / "alias" / "id_ed25519")}),
                             ("Glob", {"pattern": "alias/*"}), ("Glob", {"pattern": "**/*", "path": "alias"}),
                             ("Grep", {"pattern": "BEGIN", "path": "alias"})):
        decision = gate(tool, tool_input)
        assert decision.action == "deny" and "private_paths" in decision.reason, (tool, tool_input)
    for command in ("cat alias/id_ed25519", f"python -c \"open(r'{ws / 'alias' / 'id_ed25519'}')\""):
        decision = gate("Bash", {"command": command})
        assert decision.action == "ask" and "link" in decision.reason, command
    assert gate("Bash", {"command": "cat notes.md"}).action == "allow"
    assert gate("Read", {"file_path": "notes.md"}).action == "allow"
    assert gate("Glob", {"pattern": "*.md"}).action == "allow"
    # A relative Glob path is read from the workdir once, and its pattern from that path (local review).
    (ws / "ref" / "ref").mkdir(parents=True)
    nested = (str(ws / "ref" / "ref"),)
    assert evaluate_tool("Glob", {"path": "ref", "pattern": "*.txt"}, PolicySettings(), allowed_roots=[str(ws)],
                         workdir=str(ws), environ={}, private_paths=nested, home=str(home)).action == "allow"
    assert evaluate_tool("Glob", {"path": "ref", "pattern": "ref/*"}, PolicySettings(), allowed_roots=[str(ws)],
                         workdir=str(ws), environ={}, private_paths=nested, home=str(home)).action == "deny"


def test_posix_paths_keep_their_case_in_the_gate(monkeypatch):
    monkeypatch.setattr(private_paths, "_host_windows", lambda: False)
    private = ["/home/pi/Secret"]
    assert mentioned_private_path("cat /home/pi/secret/x", private, "/home/pi", {}) is None
    assert mentioned_private_path("cat ~/secret/x", private, "/home/pi", {"HOME": "/home/pi"}) is None
    assert mentioned_private_path("cat ~/Secret/x", private, "/home/pi", {}) == private[0]
    assert mentioned_private_path("cat $HOME/Secret/x", private, "/home/pi", {"HOME": "/home/pi"}) == private[0]
    assert private_paths.shell_needles(private, "/home/pi") == ["Secret"]
    # Windows spellings stay case-insensitive on any host.
    assert mentioned_private_path("cat C:/USERS/PI/.SSH/x", WIN_PRIVATE, WIN_HOME, {}) == WIN_PRIVATE[0]


@pytest.mark.skipif(os.name == "nt", reason="Windows paths are case-insensitive")
def test_a_case_sensitive_posix_volume_keeps_a_differently_cased_work_folder_apart(tmp_path, monkeypatch):
    from labhq.adapters.owned import case_sensitive_directory

    home = tmp_path / "home"
    (home / "Secret").mkdir(parents=True)
    if not case_sensitive_directory(home / "Secret"):
        pytest.skip("this volume is case-insensitive")
    settings = Settings.model_validate({"policy": {"private_paths": ["~/Secret"]}})
    found = resolve_private_paths(settings, [home / "secret" / "job"], home=str(home))
    assert found.labels == ("~/Secret",) and not found.skipped
    monkeypatch.setattr("labhq.adapters.owned.case_sensitive_directory", lambda path: False)  # e.g. macOS APFS
    assert resolve_private_paths(settings, [home / "secret" / "job"], home=str(home)).skipped == ("~/Secret",)


def test_plugin_dirs_expand_with_the_claude_engine_env(tmp_path, monkeypatch):
    home = _home(tmp_path)
    plugin = home / ".claude" / "plugins" / "bioinfo"
    plugin.mkdir(parents=True)
    monkeypatch.delenv("BIOINFO_AGENT_DIR", raising=False)
    settings = Settings.model_validate({"policy": {"private_paths": ["~/.claude", "~/.ssh"]}})
    settings.engines.claude_code.env = {"BIOINFO_AGENT_DIR": str(plugin)}  # as the init wizard writes it
    keep = private_paths.plugin_keep_dirs(settings, ["${BIOINFO_AGENT_DIR}"])
    assert keep == [str(plugin)]
    elsewhere = str(tmp_path / "elsewhere")
    assert private_paths.plugin_keep_dirs(settings, ["${BIOINFO_AGENT_DIR}"],
                                          task_env={"BIOINFO_AGENT_DIR": elsewhere}) == [elsewhere]
    found = resolve_private_paths(settings, keep, home=str(home))
    assert found.labels == ("~/.ssh",) and found.skipped == ("~/.claude",)


def test_doctor_expands_plugin_dirs_with_the_claude_engine_env(tmp_path, monkeypatch):
    home = _home(tmp_path)
    monkeypatch.setattr(private_paths, "host_home", lambda: str(home))
    plugin = home / ".claude" / "plugins" / "bioinfo"
    plugin.mkdir(parents=True)
    monkeypatch.delenv("BIOINFO_AGENT_DIR", raising=False)
    settings = _doctor_settings(tmp_path, ["~/.claude", "~/.ssh"])
    (tmp_path / "agents" / "core" / "worker.yaml").write_text(
        "id: worker\nname: Worker\nrole: test\nengine: claude_code\nplugin_dirs: ['${BIOINFO_AGENT_DIR}']\n",
        encoding="utf-8")
    settings.engines.claude_code.env = {"BIOINFO_AGENT_DIR": str(plugin)}
    row = _doctor_row(settings, monkeypatch)
    assert row["detail"] == "1 active; skipped, holds a work folder: ~/.claude"


@pytest.mark.asyncio
async def test_runner_hands_claude_the_active_paths_and_warns_when_shell_has_no_gate(tmp_path, monkeypatch):
    home = _home(tmp_path)
    monkeypatch.setattr(private_paths, "host_home", lambda: str(home))
    runner, seen = _capture_runner(_runner_settings(tmp_path, ["~/.ssh"]), monkeypatch)
    agent = runner.registry.get("worker")
    agent.tools = ["Read", "Bash(python *)"]  # builtin_mcp=[]: no approval gate
    for task_id in ("t1", "t2"):
        assert (await runner.run_task(Task(id=task_id, agent_id="worker", request_id="r", prompt="q"))).ok
    assert seen["ctx"].private_paths == [str(home / ".ssh")]
    warned = [e["data"]["text"] for e in runner.store.pending()
              if e["type"] == "agent.log" and "셸 명령이 모두 거부" in e["data"].get("text", "")]
    assert len(warned) == 1, "said once, not on every task"
    off, seen_off = _capture_runner(_runner_settings(tmp_path / "off", []), monkeypatch)
    assert (await off.run_task(Task(id="t3", agent_id="worker", request_id="r", prompt="q"))).ok
    assert seen_off["ctx"].private_paths == []


# ---------------- cd before a relative path (PR #324 live probe) ----------------

@pytest.mark.parametrize("tool,command", [
    ("Bash", "cd C:\\Users\\pi && cat .ssh\\id_rsa"),
    ("Bash", "cd ~ && cat .SSH/id_rsa"),
    ("Bash", "cd $HOME; ls .ssh"),
    ("Bash", "cd /c/Users/pi && cat .ssh/config"),
    ("Bash", 'cmd /c "cd /d %USERPROFILE% && type .ssh\\id_rsa"'),
    ("Bash", "pushd C:/Users && cat pi/.ssh/id_rsa"),
    ("Bash", "cd ~/AppData && cd Local/Google/Chrome && ls 'User Data'"),
    ("PowerShell", "Set-Location -Path $env:USERPROFILE; Get-Content .ssh/id_rsa"),
    ("PowerShell", "sl ${env:LOCALAPPDATA}; dir 'Google\\Chrome\\User Data'"),
])
def test_a_relative_path_after_cd_into_home_goes_to_the_pi(tool, command):
    decision = _gate(tool, {"command": command})
    assert decision.action == "ask" and "private_paths" in decision.reason, command


@pytest.mark.parametrize("command", [
    "cd outputs && python run.py", "cd ~ && ls", "cd; ls", "cd ~ && ls .ssh-notes", "cd - && ls",
    'cd "$(git rev-parse --show-toplevel)" && ls', "git commit -m 'cd docs and fix .ssh notes'",
])
def test_cd_without_a_private_path_keeps_the_decision(command):
    assert _gate("Bash", {"command": command}).action == "allow", command


def test_too_many_folder_changes_go_to_the_pi():
    assert _gate("Bash", {"command": " && ".join(["cd a"] * 40) + " && ls"}).action == "ask"


def test_the_live_probe_cd_spellings_reach_the_gate_on_real_folders(tmp_path):
    fake_home = tmp_path / "fh"
    (fake_home / ".sec").mkdir(parents=True)
    (fake_home / ".sec" / "key.txt").write_text("CANARY", encoding="utf-8")
    ws = tmp_path / "ws"
    (ws / "sub").mkdir(parents=True)
    _link_dir(ws / "alias", fake_home / ".sec")
    found = resolve_private_paths(Settings.model_validate({"policy": {"private_paths": [str(fake_home / ".sec")]}}),
                                  [ws], home=str(tmp_path / "elsewhere"))

    def gate(command):
        return evaluate_tool("Bash", {"command": command}, PolicySettings(), allowed_roots=[str(ws)],
                             workdir=str(ws), environ={}, private_paths=found.paths, home=str(tmp_path / "elsewhere"))

    for command in (f'cd "{fake_home}" && cat .sec/key.txt', "cd ../fh && cat .sec/key.txt",
                    "cd .. && cd fh/.sec && cat key.txt", "cd alias && cat key.txt"):
        decision = gate(command)
        assert decision.action == "ask" and "private_paths" in decision.reason, command
    for command in ("cd sub && python script.py", f'cd "{ws}" && cat notes.md', "cd .. && ls"):
        assert gate(command).action == "allow", command


# ---------------- user-environment registry (#325) ----------------
# The PI's GITHUB_TOKEN is a user environment variable: labhq strips it from staff process env, but the same
# account can read it back from the registry. Pure text tests; no registry is read.

@pytest.mark.parametrize("tool,command", [
    ("Bash", r"reg query HKCU\Environment"),
    ("Bash", r"reg query HKCU\Environment /v GITHUB_TOKEN"),
    ("Bash", r'reg.exe QUERY "HKEY_CURRENT_USER\Environment" /v GITHUB_TOKEN'),
    ("Bash", "reg query hkcu/environment"),
    ("Bash", r"reg export HKCU\Environment out.reg"),
    ("Bash", r"reg save HKCU\Environment env.hiv"),
    ("Bash", "reg query HKCU /s /f GITHUB_TOKEN"),
    ("Bash", r"reg query HKU\S-1-5-21-1-2-3-1001\Environment"),
    ("Bash", r"cmd /c reg query HK^CU\Environment"),
    ("Bash", r'reg query "HK"CU\Environment'),
    ("Bash", "cat /proc/registry/HKEY_CURRENT_USER/Environment/GITHUB_TOKEN"),
    ("Bash", "wmic environment get name,variablevalue"),
    ("Bash", "python -c \"import winreg; k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, 'Environment')\""),
    ("Bash", "py -c \"import _winreg as w; print(w.OpenKey(w.HKEY_CURRENT_USER, 'Environment'))\""),
    ("PowerShell", r"Get-ItemProperty HKCU:\Environment"),
    ("PowerShell", r"get-itemproperty -Path 'hkcu:\environment' -Name GITHUB_TOKEN"),
    ("PowerShell", "Get-ItemProperty HKCU:/Environment"),
    ("PowerShell", "gp HKCU:Environment"),
    ("PowerShell", r"Get-Item HKCU:\Environment"),
    ("PowerShell", r"gi Registry::HKEY_CURRENT_USER\Environment"),
    ("PowerShell", r"Get-ChildItem Registry::HKEY_CURRENT_USER\Environment"),
    ("PowerShell", r"gci HKCU:\ -Recurse"),
    ("PowerShell", r"Get-ItemProperty HKCU:\Env*"),
    ("PowerShell", r"Set-Location HKCU:; Get-ItemProperty Environment"),
    ("PowerShell", r"Get-ItemPropertyValue HKCU:\Environment GITHUB_TOKEN"),
    ("PowerShell", "[Environment]::GetEnvironmentVariable('GITHUB_TOKEN', 'User')"),
    ("PowerShell", '[System.Environment]::GetEnvironmentVariable("GITHUB_TOKEN", "user")'),
    ("PowerShell", "[Environment]::GetEnvironmentVariable('GITHUB_TOKEN', [EnvironmentVariableTarget]::User)"),
    ("PowerShell", "[environment]::getenvironmentvariables([System.EnvironmentVariableTarget]::User)"),
    ("PowerShell", "[Environment]::GetEnvironmentVariables('User')"),
    ("PowerShell", "[Environment]::GetEnvironmentVariable('GITHUB_TOKEN', 1)"),
    ("PowerShell", "$t = [EnvironmentVariableTarget]::User; [Environment]::GetEnvironmentVariable('X', $t)"),
    ("PowerShell", "[Microsoft.Win32.Registry]::CurrentUser.OpenSubKey('Environment').GetValue('GITHUB_TOKEN')"),
    ("PowerShell", r"[Microsoft.Win32.Registry]::GetValue('HKEY_CURRENT_USER\Environment', 'GITHUB_TOKEN', $null)"),
    ("PowerShell", "Get-CimInstance Win32_Environment"),
    ("PowerShell", "gwmi win32_environment | ? UserName -like '*pi*'"),
    # PR #327 sweep: PowerShell collapses `.` and `..`, so the key may come after a detour.
    ("PowerShell", r"Get-ItemProperty HKCU:\Software\..\Environment"),
    ("PowerShell", r"gp HKCU:\.\Environment"),
    ("PowerShell", r"Get-ItemProperty HKCU:Software\..\Environment"),
    ("PowerShell", r"Get-ItemProperty HKCU:\Software\Microsoft\..\..\Environment"),
    ("PowerShell", r"Get-ItemProperty HKCU:\Software\..\Env*"),
    # ... or a relative path after a change of location into a subkey.
    ("PowerShell", r"cd HKCU:\Software; gp ..\Environment"),
    ("PowerShell", r"Set-Location HKCU:\Software; (Get-ItemProperty ..\Environment).GITHUB_TOKEN"),
    ("PowerShell", r"Push-Location Registry::HKEY_CURRENT_USER\Software; gp ..\Environment"),
    ("PowerShell", r"Set-Location -Path HKCU:\Software\Microsoft; gci .. -Recurse"),
    ("Bash", "cd /proc/registry/HKEY_CURRENT_USER/Software && cat ../Environment/GITHUB_TOKEN"),
    # Python string prefixes, idiomatic for registry paths.
    ("Bash", "python -c \"import winreg; print(winreg.QueryValueEx(winreg.OpenKey(winreg.HKEY_CURRENT_USER, "
             "r'Environment'), 'GITHUB_TOKEN'))\""),
    ("Bash", "python -c \"import winreg; winreg.OpenKey(winreg.HKEY_CURRENT_USER, u'Environment')\""),
    ("Bash", "python -c \"import subprocess; subprocess.run(['reg','query',r'HKCU\\Environment','/v','GITHUB_TOKEN'])\""),
    ("Bash", "python -c \"import subprocess; subprocess.run(['reg','query','HKCU','/s'])\""),
    # The .NET method group through .Invoke, or held for a later call.
    ("PowerShell", "[Environment]::GetEnvironmentVariable.Invoke('GITHUB_TOKEN','User')"),
    ("PowerShell", "$f=[Environment]::GetEnvironmentVariable; $f.Invoke('GITHUB_TOKEN','User')"),
    ("PowerShell", "[Environment]::GetEnvironmentVariables.Invoke('User')"),
    # A subexpression between the separator and the key name.
    ("PowerShell", "Get-ItemProperty \"HKCU:\\$('Environment')\""),
    # The whole hive through the provider, Git Bash or regedit.
    ("PowerShell", "gci Registry::HKEY_CURRENT_USER -Recurse"),
    ("Bash", "ls -R /proc/registry/HKEY_CURRENT_USER"),
    ("Bash", "regedit /e out.reg HKEY_CURRENT_USER"),
])
def test_a_user_environment_registry_read_goes_to_the_pi(tool, command):
    decision = _gate(tool, {"command": command})
    assert decision.action == "ask" and r"HKCU\Environment" in decision.reason, command


@pytest.mark.parametrize("tool,command", [
    ("PowerShell", "$env:GITHUB_TOKEN"),
    ("PowerShell", "Get-ChildItem Env:"),
    ("PowerShell", "gci env:GITHUB_TOKEN"),
    ("PowerShell", "[Environment]::GetEnvironmentVariable('PATH')"),
    ("PowerShell", "[Environment]::GetEnvironmentVariable('PATH', 'Process')"),
    ("PowerShell", "[Environment]::GetEnvironmentVariable('PATH', [EnvironmentVariableTarget]::Process)"),
    ("PowerShell", "[Environment]::GetEnvironmentVariables()"),
    ("PowerShell", r"Get-ItemProperty HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"),
    ("PowerShell", "Write-Output 'environment ready'"),
    ("Bash", "echo %GITHUB_TOKEN%"),
    ("Bash", "cmd /c set"),
    ("Bash", "set"),
    ("Bash", "printenv"),
    ("Bash", "env | grep PATH"),
    ("Bash", "python -c \"import os; print(os.environ.get('PATH'))\""),
    ("Bash", r"reg query HKCU\Software\Python"),
    ("Bash", "conda env list"),
    ("Bash", "python setup_environment.py --out outputs/env.txt"),
    ("Bash", "cat docs/environment.md"),
    # PR #327 sweep false alarms: a bare HKCU word outside reg.exe, and Process targets spelled other ways.
    ("Bash", "echo HKCU"),
    ("Bash", "ls outputs/hkcu"),
    ("PowerShell", "[Environment]::GetEnvironmentVariable('TEMP', $null)"),
    ("Bash", "dotnet script -e 'Console.WriteLine(Environment.GetEnvironmentVariable(\"PATH\", "
             "EnvironmentVariableTarget.Process));'"),
    ("PowerShell", "[Environment]::GetEnvironmentVariable.Invoke('PATH')"),
    ("Bash", "cd docs/../tests && python -c \"print(r'raw', b'bytes')\""),
    ("PowerShell", r"cd HKCU:\Software\Python; gp ."),
])
def test_ordinary_env_reads_and_unrelated_commands_stay_allowed(tool, command):
    assert _gate(tool, {"command": command}).action == "allow", command


@pytest.mark.parametrize("command", [
    r"reg query HKCU\Environment",
    "[Environment]::GetEnvironmentVariable('GITHUB_TOKEN', 'User')",
    "Get-CimInstance Win32_Environment",
])
def test_the_registry_rule_is_off_without_private_paths(command):
    assert _gate("PowerShell", {"command": command}, private=[]).action == "allow"


def test_the_registry_rule_only_tightens():
    policy = PolicySettings(data_zones=[DataZone(path="/data/cohort")])
    zone = _gate("Bash", {"command": r"cat /data/cohort/a.tsv; reg query HKCU\Environment"}, policy=policy)
    assert zone.action == "ask" and "restricted" in zone.reason
    for tool in ("Read", "Grep"):
        assert _gate(tool, {"pattern": r"HKCU\Environment", "path": "/work/t1"}).action == "allow"
