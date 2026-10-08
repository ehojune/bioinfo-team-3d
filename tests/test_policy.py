import time

import pytest

from labhq.policy import (
    claude_rule_path, claude_settings, core_hours, evaluate_tool, hpc_needs_approval,
    mentions_zone, touches, touches_resolved,
)
from labhq.settings import DataZone, PolicySettings


def _policy(**kw):
    return PolicySettings(data_zones=[DataZone(path="/data/cohort", level="restricted")], **kw)


def _long_separator_runs():
    return "/" * 65_536 + "\\" * 65_536 + ":" * 600_000


def _assert_linear(call):
    started = time.perf_counter()
    assert call(_long_separator_runs()) is None
    assert time.perf_counter() - started < 2, "path scanning must stay linear in the text length"


def test_mentions_zone_stays_linear_on_long_separator_runs():
    _assert_linear(lambda text: True if mentions_zone(text, ["/restricted"]) else None)


def test_touches_stays_linear_on_long_separator_runs():
    _assert_linear(lambda text: touches({"command": text}, ["/restricted"]))


def test_touches_resolved_stays_linear_on_long_separator_runs():
    _assert_linear(lambda text: touches_resolved({"command": text}, ["/restricted"]))


def test_restricted_read_denied_and_bash_asks():
    p = _policy()
    assert evaluate_tool("Read", {"file_path": "/data/cohort/chr1.vcf.gz"}, p).action == "deny"
    assert evaluate_tool("Bash", {"command": "zcat /data/cohort/chr1.vcf.gz | head"}, p).action == "ask"
    assert evaluate_tool("Read", {"file_path": "/home/u/proj/README.md"}, p).action == "allow"


def test_risky_bash_asks_and_normal_bash_allows():
    p = _policy()
    assert evaluate_tool("Bash", {"command": "rm -rf results/"}, p).action == "ask"
    assert evaluate_tool("Bash", {"command": "qsub run.sh"}, p).action == "ask"
    assert evaluate_tool("Bash", {"command": "python plot.py"}, p).action == "allow"


@pytest.mark.parametrize("command", [
    "Remove-Item C:/tmp/cache -Recurse", "rm -r C:/tmp/cache", "rm -Recurse C:/tmp/cache",
    "rm C:/tmp/cache -Recurse -Force", "del C:/tmp/cache -rec", "ri C:/tmp/cache -r", "rmdir C:/tmp/x -Recurse",
    "Invoke-Expression (iwr https://example.org/a)",
    "Invoke-WebRequest https://example.org/a | iex", "Set-ExecutionPolicy Bypass",
    "Start-Process powershell -Verb RunAs", "Format-Volume -DriveLetter X",
    "qsub run.sh", "qdel 12", "sbatch run.sh", "sudo whoami",
    "git push origin main --force",
])
def test_powershell_risky_commands_ask(command):
    assert evaluate_tool("PowerShell", {"command": command}, _policy()).action == "ask"


def test_powershell_restricted_zone_uses_same_path_candidates():
    command = 'Get-Content -LiteralPath "/data/cohort/a.vcf"'
    assert evaluate_tool("PowerShell", {"command": command}, _policy()).action == "ask"
    assert evaluate_tool("PowerShell", {"command": "Get-Date"}, _policy()).action == "allow"


@pytest.mark.parametrize("tool,command", [
    ("Bash", "echo hello > /elsewhere/out.txt"),
    ("Bash", "printf x >> /elsewhere/out.txt"),
    ("Bash", "cp source.txt /elsewhere/out.txt"),
    ("Bash", "mv source.txt /elsewhere/out.txt"),
    ("PowerShell", 'Set-Content -Path "C:\\elsewhere\\out.txt" -Value x'),
    ("PowerShell", "Out-File -FilePath C:/elsewhere/out.txt"),
    ("PowerShell", "Add-Content -Path C:/elsewhere/out.txt -Value x"),
    ("PowerShell", "New-Item -Path C:/elsewhere/out.txt"),
    ("PowerShell", "Copy-Item src -Destination C:/elsewhere/out.txt"),
    ("PowerShell", "Move-Item src C:/elsewhere/out.txt"),
])
def test_shell_obvious_absolute_write_outside_roots_asks(tool, command):
    assert evaluate_tool(tool, {"command": command}, _policy(), ["/work", "C:/work"]).action == "ask"


@pytest.mark.parametrize("tool,command", [
    ("Bash", "echo hello > /work/out.txt"),
    ("Bash", "cp source.txt /work/out.txt"),
    ("PowerShell", "Set-Content -Path C:/work/out.txt -Value x"),
    ("PowerShell", "Copy-Item src -Destination C:/work/out.txt"),
])
def test_shell_obvious_absolute_write_inside_roots_allows(tool, command):
    assert evaluate_tool(tool, {"command": command}, _policy(), ["/work", "C:/work"]).action == "allow"


def test_write_outside_roots_asks():
    p = _policy()
    assert evaluate_tool("Write", {"file_path": "/etc/passwd"}, p, allowed_roots=["/w/task"]).action == "ask"
    assert evaluate_tool("Write", {"file_path": "/w/task/outputs/a.md"}, p, allowed_roots=["/w/task"]).action == "allow"


def test_path_normalization_boundary_and_shell_forms():
    zone = r"C:\Data\Cohort"
    assert touches({"command": r'cat --in="c:/data/cohort/A.vcf"'}, [zone]) == zone
    assert touches({"command": r"copy host:C:\Data\Cohort\A.vcf"}, [zone]) == zone
    assert evaluate_tool("Read", {"file_path": "c:/DATA/cohort/A.vcf"},
                         PolicySettings(data_zones=[DataZone(path=zone)])).action == "deny"
    assert touches({"file_path": r"c:/DATA/cohort2/A.vcf"}, [zone]) is None
    assert touches({"command": "cat ../cohort/a.vcf"}, ["/data/cohort"], workdir="/data/task")
    assert touches({"command": "cat /data/cohort2/a.vcf"}, ["/data/cohort"]) is None


@pytest.mark.parametrize("command", [
    "docker run -v /data/cohort:/mnt img",
    "scp host:/data/cohort/a.vcf .",
    "rsync src:/data/cohort/ dst/",
    "cat file:///data/cohort/a.vcf",
    "cat file:///data/%2e/cohort/a.vcf",
    "cat file://localhost/data/./cohort/a.vcf",
    "cat file:///data/%63ohort?download=1",
    "cat file:///data/%63ohort#section",
    "cat file:///data/%63ohort/a.vcf",
    "cat --input=host:file:///data/cohort/a.vcf",
    "cat --input=s3like:/data/cohort",
    "cat</data/cohort/a",
    "$(cat /data/cohort/a)",
])
def test_embedded_absolute_restricted_path_asks(command):
    assert touches({"command": command}, ["/data/cohort"]) == "/data/cohort"
    assert evaluate_tool("Bash", {"command": command}, _policy()).action == "ask"


@pytest.mark.parametrize("command", [
    "docker run -v /data/cohort2:/mnt img",
    "scp host:/data/cohort2/a.vcf .",
    "cat file:///data/cohort2/a.vcf",
    "cat --input=s3like:/data/cohort2",
])
def test_embedded_neighbor_path_does_not_match(command):
    assert touches({"command": command}, ["/data/cohort"]) is None
    assert evaluate_tool("Bash", {"command": command}, _policy()).action == "allow"


def test_absolute_path_containing_zone_as_middle_suffix_does_not_match():
    path = "/scratch/data/cohort/report.txt"
    assert touches({"file_path": path}, ["/data/cohort"]) is None
    assert touches({"command": f"cat --input={path}"}, ["/data/cohort"]) is None
    assert evaluate_tool("Read", {"file_path": path}, _policy()).action == "allow"
    assert evaluate_tool("Bash", {"command": f"cat {path}"}, _policy()).action == "allow"


def test_restricted_zone_with_spaces_in_structured_and_shell_paths():
    zone = "/data/controlled cohort"
    policy = PolicySettings(data_zones=[DataZone(path=zone)])
    path = f"{zone}/a.vcf"
    for key in ("file_path", "notebook_path", "path"):
        assert touches({key: path}, [zone]) == zone
    assert evaluate_tool("Read", {"file_path": path}, policy).action == "deny"
    assert evaluate_tool("NotebookRead", {"notebook_path": path}, policy).action == "deny"
    assert evaluate_tool("Read", {"path": path}, policy).action == "deny"
    for command in (f'cat "{path}"', f"cat '{path}'", f"cat {path}"):
        assert touches({"command": command}, [zone]) == zone
        assert evaluate_tool("Bash", {"command": command}, policy).action == "ask"
    escaped = r"cat /data/controlled\ cohort/a.vcf"
    assert touches({"command": escaped}, [zone]) == zone
    assert evaluate_tool("Bash", {"command": escaped}, policy).action == "ask"

    neighbor = "/data/controlled cohorts/x"
    assert touches({"file_path": neighbor}, [zone]) is None
    assert evaluate_tool("Read", {"file_path": neighbor}, policy).action == "allow"
    assert touches({"command": f"cat {neighbor}"}, [zone]) is None
    assert evaluate_tool("Bash", {"command": f"cat {neighbor}"}, policy).action == "allow"
    escaped_neighbor = r"cat /data/controlled\ cohorts/x"
    assert touches({"command": escaped_neighbor}, [zone]) is None
    assert evaluate_tool("Bash", {"command": escaped_neighbor}, policy).action == "allow"
    outside = "/scratch/data/controlled cohort/x"
    assert touches({"command": f"cat {outside}"}, [zone]) is None
    assert evaluate_tool("Bash", {"command": f"cat {outside}"}, policy).action == "allow"
    windows_zone = r"C:\Data\Controlled Cohort"
    assert touches({"command": "type c:/DATA/controlled cohort/x"}, [windows_zone]) == windows_zone
    assert touches({"command": "type c:/DATA/controlled cohorts/x"}, [windows_zone]) is None
    assert touches({"command": r"type C:\data\x"}, [r"C:\data"]) == r"C:\data"


def test_unc_paths_are_compared_but_not_exported_as_unverified_claude_rules():
    zone = r"\\server\share\cohort"
    assert touches({"file_path": "//SERVER/share/cohort/a.vcf"}, [zone]) == zone
    assert touches({"file_path": "//server/share/cohort2/a.vcf"}, [zone]) is None
    with pytest.raises(ValueError, match="UNC"):
        claude_rule_path(zone)
    with pytest.raises(ValueError, match="UNC"):
        claude_settings(PolicySettings(data_zones=[DataZone(path=zone)]))


def test_relative_write_resolved_against_workdir():
    p = _policy()
    roots = ["/work/task"]
    assert evaluate_tool("Write", {"file_path": "out/report.txt"}, p, roots, workdir="/work/task").action == "allow"
    assert evaluate_tool("Write", {"file_path": "../outside.txt"}, p, roots, workdir="/work/task").action == "ask"
    assert evaluate_tool("Write", {"file_path": "out/report.txt"}, p, roots).action == "ask"
    assert evaluate_tool("Write", {"file_path": "/work/task2/a"}, p, roots).action == "ask"
    assert evaluate_tool("Write", {"file_path": "../cohort/a"}, p, ["/data/task"], workdir="/data/task").action == "deny"


def test_drive_rooted_backslash_is_absolute_without_a_drive_letter():
    path = r"\Windows\System32\drivers\etc\hosts"
    assert evaluate_tool("Write", {"file_path": path}, _policy(),
                         allowed_roots=[r"C:\work\task"], workdir=r"C:\work\task").action == "ask"
    assert touches({"file_path": r"\data\cohort\a.vcf"}, ["/data/cohort"],
                   workdir=r"C:\work\task") == "/data/cohort"


def test_drive_relative_path_never_uses_workdir_as_its_base():
    zone = r"C:\data\cohort"
    p = PolicySettings(data_zones=[DataZone(path=zone)])
    roots = [r"C:\work\task"]
    for path in (r"C:outside.txt", r"c:..\data\cohort\a.vcf"):
        assert evaluate_tool("Write", {"file_path": path}, p, roots,
                             workdir=r"C:\work\task").action == "ask"
        assert touches({"file_path": path}, [zone], workdir=r"C:\work\task") == zone
        assert evaluate_tool("Read", {"file_path": path}, p, roots,
                             workdir=r"C:\work\task").action == "deny"
    assert evaluate_tool("Bash", {"command": "cat --input=C:outside.txt"}, p,
                         workdir=r"C:\work\task").action == "ask"
    assert touches({"command": "cat D:outside.txt"}, [zone], workdir=r"C:\work\task") is None


def test_relative_zone_is_rejected_at_config_load(tmp_path):
    from labhq.settings import Settings

    config = tmp_path / "config.yaml"
    config.write_text("policy:\n  data_zones:\n    - path: ../cohort\n")
    with pytest.raises(ValueError, match="must be absolute"):
        Settings.load(str(config))


def test_claude_settings_absolute_rule_syntax():
    deny = claude_settings(_policy())["permissions"]["deny"]
    assert "Read(//data/cohort/**)" in deny


def test_hpc_thresholds():
    p = _policy()
    assert hpc_needs_approval(1, "00:10:00", p)  # threshold 0 → always ask
    p.approvals.hpc_core_hours_threshold = 10
    assert not hpc_needs_approval(2, "02:00:00", p) and hpc_needs_approval(8, "04:00:00", p)
    assert core_hours(4, "1-00:00:00") == 96



@pytest.mark.parametrize("tool,command", [
    ("Bash", "python analysis.py 2>/dev/null"), ("Bash", "git status >/dev/null 2>&1"),
    ("PowerShell", "Get-ChildItem > $null"), ("PowerShell", "cmd /c dir > NUL"),
])
def test_null_device_redirects_are_not_writes(tool, command):
    assert evaluate_tool(tool, {"command": command}, _policy(), ["/work", "C:/work"]).action == "allow"


def test_non_recursive_rm_is_allowed():
    assert evaluate_tool("PowerShell", {"command": "rm C:/work/tmp.txt"}, _policy()).action == "allow"


@pytest.mark.parametrize("command", [
    "rm -rf .tmp",
    "rm -rf .tmp/cache",
    "rm -r .tmp/cache",
])
def test_single_recursive_delete_below_step_tmp_is_allowed(command):
    decision = evaluate_tool("Bash", {"command": command}, _policy(),
                             allowed_roots=["/work/step"], workdir="/work/step")
    assert decision.action == "allow"


@pytest.mark.parametrize("command", [
    "Remove-Item -Recurse -Force .tmp",
    r"Remove-Item -Recurse .tmp\a",
])
def test_single_powershell_recursive_delete_below_step_tmp_is_allowed(command):
    decision = evaluate_tool("PowerShell", {"command": command}, _policy(),
                             allowed_roots=["C:/work/step"], workdir="C:/work/step")
    assert decision.action == "allow"


@pytest.mark.parametrize("tool,command,workdir", [
    ("Bash", "rm -rf inputs/upstream/subdir", "/work/step"),
    ("Bash", "rm -rf outputs/tmp", "/work/step"),
    ("Bash", "command cd .. && rm -rf sibling", "/work/step"),
    ("Bash", "cd .tmp && rm -rf x", "/work/step"),
    ("PowerShell", r"Microsoft.PowerShell.Management\Set-Location ..; Remove-Item -Recurse sibling",
     "C:/work/step"),
    ("Bash", "rm -rf .tmp; rm -rf x", "/work/step"),
])
def test_recursive_delete_requires_a_single_simple_command_below_step_tmp(tool, command, workdir):
    decision = evaluate_tool(tool, {"command": command}, _policy(),
                             allowed_roots=[workdir], workdir=workdir)
    assert decision.action == "ask"
    assert decision.reason.startswith("재귀 삭제 확인 필요: `")


def test_recursive_delete_below_linked_step_tmp_asks(tmp_path):
    target = tmp_path / "linked"
    target.mkdir()
    try:
        (tmp_path / ".tmp").symlink_to(target, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlink unavailable: {exc}")

    decision = evaluate_tool("Bash", {"command": "rm -rf .tmp/cache"}, _policy(),
                             allowed_roots=[str(tmp_path)], workdir=str(tmp_path))
    assert decision.action == "ask"
    assert decision.reason.startswith("재귀 삭제 확인 필요: `")


@pytest.mark.parametrize("command", ["rm -rf .tmp/link/", "rm -rf .tmp/link/.", "rm -rf .tmp/link"])
def test_recursive_delete_of_a_linked_target_below_step_tmp_asks(tmp_path, command):
    """#501 review: `rm -rf .tmp/link/` follows a directory link out of the step folder."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / ".tmp").mkdir()
    try:
        (tmp_path / ".tmp" / "link").symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlink unavailable: {exc}")

    decision = evaluate_tool("Bash", {"command": command}, _policy(),
                             allowed_roots=[str(tmp_path)], workdir=str(tmp_path))
    assert decision.action == "ask"


@pytest.mark.parametrize("command", [
    r"Remove-Item -Recurse -Force HKCU:\Software\X",
    r"Remove-Item -Recurse Cert:\CurrentUser\My",
    "Remove-Item -Recurse -Force .tmp,C:/outside",
    r"Remove-Item -Recurse -Path @('.tmp','..\x')",
    "Get-ChildItem .tmp | Remove-Item -Recurse",
    r"Remove-Item -Recurse -LiteralPath \\server\share\tmp",
    "Remove-Item -Recurse -Path ~",
    "Remove-Item -Recurse -Path $target",
    "Remove-Item -Recurse -Path $(Join-Path . .tmp)",
    "Remove-Item -Recurse -Path ${target}",
    "Remove-Item -Recurse -Path %TEMP%",
    "Remove-Item -Recurse -Path !TEMP!",
    "Remove-Item -Recurse -Path *.tmp",
    "Remove-Item -Recurse -Path",
])
def test_powershell_recursive_delete_requires_literal_filesystem_targets(command):
    decision = evaluate_tool("PowerShell", {"command": command}, _policy(),
                             allowed_roots=["C:/work/step"], workdir="C:/work/step")
    assert decision.action == "ask"
    assert decision.reason.startswith("재귀 삭제 확인 필요: `")


@pytest.mark.parametrize("command", [
    "rm -rf ../x",
    "rm -rf $DIR",
    'rm -rf "$D"',
    "rm -rf /tmp/x",
    "rm -rf .tmp ../x",
])
def test_recursive_delete_outside_or_unresolved_still_asks(command):
    decision = evaluate_tool("Bash", {"command": command}, _policy(),
                             allowed_roots=["/work/step"], workdir="/work/step")
    assert decision.action == "ask"
    assert decision.reason.startswith("재귀 삭제 확인 필요: `")
    assert "(/" not in decision.reason


def test_other_risky_command_summary_has_a_korean_label_without_the_regex():
    decision = evaluate_tool("Bash", {"command": "qsub run.sh"}, _policy())
    assert decision.reason == "위험 명령 확인 필요: `qsub run.sh`"


def test_a_tool_without_a_rule_asks_instead_of_being_allowed():
    """#421: an unclassified tool used to fall through to allow. Monitor runs a shell command the Bash checks never
    see; tools measured in trial records (StructuredOutput, ToolSearch) and the configured list stay allowed."""
    p = PolicySettings()
    monitor = evaluate_tool("Monitor", {"command": "until cat /data/cohort/x; do sleep 2; done"}, p)
    assert monitor.action == "ask" and "auto_allow_tools" in monitor.reason
    for tool in ("StructuredOutput", "ToolSearch", "WebSearch", "WebFetch", "TodoWrite", "Skill"):
        assert evaluate_tool(tool, {}, p).action == "allow", tool
    p.approvals.auto_allow_tools.append("Monitor")
    assert evaluate_tool("Monitor", {"command": "true"}, p).action == "allow"
