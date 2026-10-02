"""PI personal paths (policy.private_paths, PI decision 2026-10-03): staff run under the PI's own account, so
Claude file tools are denied these paths, a shell command naming one goes to the PI, and every staff member's
instructions list them as `~` labels."""

import os
from pathlib import Path

import pytest

import labhq.private_paths as private_paths
from labhq import doctor
from labhq.adapters.base import ROLE_FOOTER, RunContext
from labhq.adapters.claude_code import ClaudeCodeAdapter
from labhq.adapters.codex import CodexAdapter
from labhq.models import AgentSpec, Engine, Task, TaskResult
from labhq.policy import claude_deny_private, evaluate_tool
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
