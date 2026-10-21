import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from labhq.policy import (
    claude_rule_path, claude_settings, core_hours, evaluate_tool, hpc_needs_approval,
    mentions_zone, touches, touches_resolved,
)
from labhq.settings import DataZone, PolicySettings, Settings


def _policy(**kw):
    return PolicySettings(data_zones=[DataZone(path="/data/cohort", level="restricted")], **kw)


def _long_separator_runs():
    return "/" * 65_536 + "\\" * 65_536 + ":" * 600_000


def _assert_linear(call):
    # Budget these synchronous scans' CPU cost, excluding other workers' scheduling
    # delays. A fixed-input cost regression does not prove asymptotic linearity.
    started = time.process_time()
    assert call(_long_separator_runs()) is None
    assert time.process_time() - started < 2, "path scanning must stay linear in the text length"


@pytest.mark.parametrize("cpu_elapsed,wall_elapsed,result,reject", [
    (0.125, 900, None, False),
    (2, 0.125, None, True),
    (2.125, 0.125, None, True),
    (0.125, 0.125, False, True),
    (0.125, 0.125, None, False),
])
def test_linear_scan_budget_uses_cpu_and_preserves_result_and_strict_limit(
        monkeypatch, cpu_elapsed, wall_elapsed, result, reject):
    cpu, wall = iter((0, cpu_elapsed)), iter((0, wall_elapsed))
    # Replace only this module's time object; pytest's real clocks stay intact.
    monkeypatch.setitem(globals(), "time", SimpleNamespace(
        process_time=lambda: next(cpu), perf_counter=lambda: next(wall)))
    seen = []

    def scan(text):
        seen.append(text)
        return result

    if reject:
        with pytest.raises(AssertionError):
            _assert_linear(scan)
    else:
        _assert_linear(scan)
    assert seen == [_long_separator_runs()]


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


@pytest.mark.parametrize("command", [
    'curl -s -o /dev/null -w "%{http_code}" "https://example.org/x"',  # trial 2026-10-08 asked the PI for this
    "echo x | tee /dev/null", "cp out.txt /dev/null",
])
def test_a_command_writing_to_a_null_device_is_not_a_write(command):
    assert evaluate_tool("Bash", {"command": command}, _policy(), ["/work", "C:/work"]).action == "allow"
    # Any other target outside the roots still asks.
    other = command.replace("/dev/null", "/elsewhere/out.txt")
    assert evaluate_tool("Bash", {"command": other}, _policy(), ["/work", "C:/work"]).action == "ask"


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
    # BashOutput/KillShell and TaskOutput/TaskStop only read or stop a background shell the gated Bash started:
    # asking the PI for each would bury the cards that matter (#518 follow-up).
    for tool in ("StructuredOutput", "ToolSearch", "WebSearch", "WebFetch", "TodoWrite", "Skill",
                 "BashOutput", "KillShell", "TaskOutput", "TaskStop"):
        assert evaluate_tool(tool, {}, p).action == "allow", tool
    p.approvals.auto_allow_tools.append("Monitor")
    assert evaluate_tool("Monitor", {"command": "true"}, p).action == "allow"


@pytest.mark.parametrize("tool", ["Workflow", "TeamCreate", "TeamDelete", "Task", "Agent"])
def test_native_delegation_tools_are_denied_even_if_configured_for_auto_allow(tool):
    """#421: native delegation bypasses labhq's approval, event and cost accounting."""
    p = PolicySettings()
    p.approvals.auto_allow_tools.append(tool)
    decision = evaluate_tool(tool, {}, p)
    assert decision.action == "deny"
    assert "labhq" in decision.reason


def test_example_configs_publish_the_same_non_delegating_auto_allow_list():
    root = Path(__file__).resolve().parents[1]
    public = root / "config" / "labhq.example.yaml"
    packaged = root / "labhq" / "config" / "labhq.example.yaml"
    assert public.read_bytes() == packaged.read_bytes()
    data = yaml.safe_load(public.read_text(encoding="utf-8"))
    configured = data["policy"]["approvals"]["auto_allow_tools"]
    assert configured == PolicySettings().approvals.auto_allow_tools
    assert not {"Task", "Agent", "Workflow", "TeamCreate", "TeamDelete"} & set(configured)
    assert Settings.model_validate(data).policy.approvals.auto_allow_tools == configured


def test_a_config_written_from_the_518_template_gets_the_background_shell_tools():
    """#520 review: `labhq init` wrote the #518 list explicitly, which replaced the newer default on load."""
    template = ["Read", "Glob", "Grep", "LS", "WebSearch", "WebFetch", "TodoWrite", "Skill", "StructuredOutput",
                "ToolSearch"]
    loaded = Settings.model_validate({"policy": {"approvals": {"auto_allow_tools": template}}})
    assert loaded.policy.approvals.auto_allow_tools == [*template, "BashOutput", "KillShell", "TaskOutput", "TaskStop"]
    assert evaluate_tool("TaskOutput", {}, loaded.policy).action == "allow"
    edited = ["Read", "Glob"]  # a list the PI edited stays as written
    kept = Settings.model_validate({"policy": {"approvals": {"auto_allow_tools": edited}}})
    assert kept.policy.approvals.auto_allow_tools == edited


WINDOWS_STEP = "C:/Users/pi/labhq/work/req/step"


def _decide_in_windows_step(command):
    return evaluate_tool("Bash", {"command": command}, _policy(), [WINDOWS_STEP], WINDOWS_STEP, windows=True)


@pytest.mark.parametrize("command", [
    # rm reads options anywhere before `--`; only the first cluster was read (cross review #17).
    "rm -f -r C:/Users/pi/Documents",
    "rm -v -rf C:/Users/pi/Documents",
    "rm --recursive --force C:/Users/pi/Documents",
    "rm --rec C:/Users/pi/Documents",
    "rm C:/Users/pi/Documents -rf",
    "RM -f -R C:/Users/pi/Documents",
    "sh -c 'rm -f -r C:/Users/pi/Documents'",
    "rm -f -r results/",
    # A find that deletes outside the write roots, or from a start the gate cannot place.
    "find C:/Users/pi/Documents -delete",
    "find /c/Users/pi/Documents -name '*.docx' -exec rm {} +",
    r"find C:/Users/pi/Documents -type f -execdir rm -f {} \;",
    r"""find C:/Users/pi/Documents -exec sh -c 'rm "$1"' _ {} \;""",
    "find C:/Users/pi/Documents -type f | xargs rm -f",
    "find ../ -delete",
    'find "$D" -delete',
    "find $(pwd) -delete",
    "find -L . -delete",
    "cd .. && find . -delete",
    "sudo find . -delete",
    # One rule for every recursive delete: inside the roots but outside `.tmp` (outputs/ holds evidence) asks, as
    # `rm -rf outputs/x` does; a pipe or redirection makes it more than one simple command, as it does for rm.
    "find . -name '*.pyc' -delete",
    "find -name '*.pyc' -delete",
    r"find outputs -name '*.part' -exec rm {} \;",
    f"find {WINDOWS_STEP}/.tmp -delete",
    "find . -name '*.part' | xargs rm",
    "find .tmp -name '*.part' | xargs rm",
    "find .tmp -type f -delete 2>/dev/null",
])
def test_recursive_deletes_in_every_option_form_ask_outside_the_step(command):
    decision = _decide_in_windows_step(command)
    assert decision.action == "ask"
    assert decision.reason.startswith("재귀 삭제 확인 필요: `")


@pytest.mark.parametrize("command", [
    "rm -f -r .tmp/cache",
    "rm -v -rf .tmp/cache",
    "rm --recursive --force .tmp/cache",
    "find .tmp -type f -delete",
    "find .tmp/cache .tmp/parts -name '*.part' -delete",
    r"find .tmp -name '*.part' -exec rm {} \;",
    "find . -name x -exec grep -l y {} +",
    "docker run --rm -v data:/data img nextflow run x -profile docker",
    # #533: an rm payload's literal targets below .tmp pass; an operand after `--`, a glob and a later brace list
    # cannot be options.
    "find .tmp -name '*.part' -exec rm -f -- {} +",
    r"find .tmp -name '*.part' -exec rm -f .tmp/stale.lock {} \;",
    'rm -f -- "$f"',
    "rm -f *.log out.{bam,bai}",
    'echo "rm"',
])
def test_deletes_below_step_tmp_and_commands_that_delete_nothing_stay_allowed(command):
    assert _decide_in_windows_step(command).action == "allow"


@pytest.mark.parametrize("command", [
    # #533: a find payload whose delete the gate cannot read, or that deletes outside .tmp, asks.
    r"find .tmp -exec rm $'-rf' /outside \;",
    r"find .tmp -exec rm -f /outside \;",
    r"find .tmp -exec rm -f ../x {} \;",
    r"find .tmp -exec $CMD {} \;",
    r'find .tmp -exec "$RM" -f {} +',
    r"find .tmp -exec env rm -f C:/Users/pi/Documents/x \;",
    r"find .tmp -execdir rm -f x \;",
    r"find .tmp -exec rm {}.bak \;",
    r'find .tmp -ok rm -- "$X" \;',
    # An rm option word the gate cannot read may be `-r`.
    "rm $'-rf' C:/Users/pi/Documents",
    'rm "$OPT" C:/Users/pi/Documents',
    'rm -f"$X" C:/Users/pi/Documents',
    "rm {-r,-f} C:/Users/pi/Documents",
    "rm `printf -- -rf` C:/Users/pi/Documents",
])
def test_find_payloads_and_rm_options_the_gate_cannot_read_ask(command):
    decision = _decide_in_windows_step(command)
    assert decision.action == "ask"
    assert decision.reason.startswith("재귀 삭제 확인 필요: `")


@pytest.mark.parametrize("command", [
    # #535 follow-up: only a real rm meets the recursive-delete rule; `docker rm` is another program's subcommand.
    'docker rm "$id"', "docker rm -f $(docker ps -aq)",
    # `git rm` removes files in the current tree only, as `git add` writes there.
    'git rm "$f"', 'git rm -r "$d"', "git rm -r --cached outputs/old", "git log --grep rm -r",
    # One double-quoted computed word as the only operand: one option cluster with nothing to delete, or one file.
    'rm -f "$f"', 'rm "$tmp"', 'rm -fv "${out}.tmp"',
])
def test_rm_words_that_cannot_delete_recursively_stay_allowed(command):
    assert _decide_in_windows_step(command).action == "allow"


@pytest.mark.parametrize("command", [
    # A git rm the options or environment may point at another tree.
    "git -C C:/Users/pi/repo rm notes.txt", "git --work-tree=C:/Users/pi rm -r Documents",
    'git --git-dir "$G" rm f', 'git "$OPT" rm f', "GIT_WORK_TREE=C:/Users/pi git rm Documents/x",
    "git -c core.worktree=C:/Users/pi rm Documents/x", "git -c alias.x='!rm -rf C:/Users/pi/Documents' x",
    # Computed words rm may read as options, and real rms after a wrapper, a computed program or a redirection.
    'rm -f "$a" "$b"', "rm -f $f", 'rm -f "$f" C:/Users/pi/x', 'rm C:/Users/pi/x "$f"', 'rm -r "$f"',
    "sudo rm -rf C:/Users/pi/Documents", '"$RUNNER" rm -rf C:/Users/pi/Documents',
    "2>/dev/null rm -rf C:/Users/pi/Documents",
])
def test_git_rm_into_another_tree_and_unreadable_rm_operands_ask(command):
    decision = _decide_in_windows_step(command)
    assert decision.action == "ask"
    assert decision.reason.startswith("재귀 삭제 확인 필요: `")


@pytest.mark.parametrize("start", [".tmp/link", ".tmp/link/"])
def test_a_find_delete_whose_start_is_a_link_out_of_the_step_asks(tmp_path, start):
    outside = tmp_path / "outside"
    outside.mkdir()
    step = tmp_path / "step"
    (step / ".tmp").mkdir(parents=True)
    try:
        (step / ".tmp" / "link").symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlink unavailable: {exc}")

    decision = evaluate_tool("Bash", {"command": f"find {start} -delete"}, _policy(),
                             allowed_roots=[str(step)], workdir=str(step))
    assert decision.action == "ask"


@pytest.mark.parametrize("tool,command", [
    ("Bash", "cmd //c rd //s //q C:/Users/pi/Documents"),
    ("PowerShell", "cmd /c rmdir /S /Q C:/Users/pi/Documents"),
    ("PowerShell", 'cmd /c "del /s /q C:/Users/pi/Documents/*.docx"'),
    ("PowerShell", "Get-ChildItem C:/Users/pi/Documents -Recurse | Remove-Item -Force"),
    ("PowerShell", "gci C:/Users/pi/Documents -r -File | Where-Object Length -gt 0 | rm"),
])
def test_cmd_and_piped_powershell_recursive_deletes_ask(tool, command):
    decision = evaluate_tool(tool, {"command": command}, _policy(), [WINDOWS_STEP], WINDOWS_STEP, windows=True)
    assert decision.action == "ask"
    assert decision.reason.startswith("재귀 삭제 확인 필요: `")


@pytest.mark.parametrize("command", [
    "Get-ChildItem . -Recurse -Filter *.csv | Select-Object Name",
    "Get-ChildItem -Recurse | Measure-Object",
    "rd .tmp/empty",
])
def test_recursive_listings_and_plain_rd_in_powershell_stay_allowed(command):
    decision = evaluate_tool("PowerShell", {"command": command}, _policy(), [WINDOWS_STEP], WINDOWS_STEP,
                             windows=True)
    assert decision.action == "allow"


def _decide_powershell_in_windows_step(command):
    return evaluate_tool("PowerShell", {"command": command}, _policy(), [WINDOWS_STEP], WINDOWS_STEP, windows=True)


@pytest.mark.parametrize("command", [
    # PR #525 review: a delete after the first pipe, in a script block too, removes what the listing found.
    "Get-ChildItem C:/Users/pi/Documents -Recurse -File | ForEach-Object { Remove-Item $_ -Force }",
    "gci C:/Users/pi/Documents -r | % { rm $_.FullName }",
    "gci C:/Users/pi/Documents -r -File | % { $_.Delete() }",
    'gci C:/Users/pi/Documents -r | % { iex "Remove-Item $_" }',
    "Get-ChildItem -Recurse | ForEach-Object { Remove-Item $_ }",
    "Get-ChildItem outputs -Recurse -File | ForEach-Object { Remove-Item $_ }",
    "Get-ChildItem .tmp -Recurse | ForEach-Object { Remove-Item $_ }; Set-Location ..",
    "Get-ChildItem .tmp -FollowSymlink -Recurse | Remove-Item",
    "Get-ChildItem $dir -Recurse | Remove-Item",
    "Get-Content list.txt | ForEach-Object { Remove-Item $_ }",
    "Get-ChildItem .tmp | ForEach-Object { Get-ChildItem C:/Users/pi -Recurse } | Remove-Item",
])
def test_a_powershell_listing_piped_into_a_delete_asks_outside_step_tmp(command):
    decision = _decide_powershell_in_windows_step(command)
    assert decision.action == "ask"
    assert decision.reason.startswith("재귀 삭제 확인 필요: `")


@pytest.mark.parametrize("command", [
    "Get-ChildItem .tmp -Recurse -File | ForEach-Object { Remove-Item $_ -Force }",
    "gci -Path .tmp/cache -r | Remove-Item -Force",
    "Get-ChildItem .tmp -Filter *.part | Remove-Item",
    "Get-ChildItem -LiteralPath .tmp/a -Recurse -File -ErrorAction SilentlyContinue | % { $_.Delete() }",
    "Get-ChildItem . -Recurse | Where-Object { $_.Name -like '*rm*' } | Select-Object Name",
    # #533: filters that pass the listed items on unchanged keep the pipeline readable.
    "Get-ChildItem .tmp -File | Where-Object { $_.Length -gt 0 } | Sort-Object Length | Select-Object -First 3 "
    "| Remove-Item -Confirm:$false",
    "gci .tmp -r | ? Length -gt 0 | % { $PSItem.Delete($true) }",
])
def test_a_powershell_listing_of_step_tmp_piped_into_a_delete_stays_allowed(command):
    assert _decide_powershell_in_windows_step(command).action == "allow"


@pytest.mark.parametrize("command", [
    # #533: after a listing of .tmp, a delete of another path, a stage that makes new objects, or text run as code.
    "Get-ChildItem .tmp | % { [System.IO.DirectoryInfo]::new('C:/outside').Delete($true) }",
    "Get-ChildItem .tmp | ForEach-Object { [System.IO.Directory]::Delete('C:/outside', $true) }",
    "Get-ChildItem .tmp | % { Remove-Item C:/outside }",
    "Get-ChildItem .tmp | Remove-Item -Path C:/outside",
    "Get-ChildItem .tmp | % { 'C:/outside' } | Remove-Item",
    "Get-ChildItem .tmp | Select-Object @{n='PSPath';e={'C:/outside'}} | Remove-Item",
    "Get-ChildItem .tmp | % { iex $code }",
    "Get-ChildItem .tmp | Where-Object { [IO.File]::Delete('C:/x') } | Remove-Item",
    "Get-ChildItem .tmp | % { Remove-Item $_; Remove-Item C:/x }",
    # A .NET delete names no literal path anywhere else either.
    "[System.IO.Directory]::Delete('C:/Users/pi/Documents', $true)",
    "(Get-Item C:/Users/pi/Documents).Delete($true)",
])
def test_a_powershell_delete_that_may_reach_another_path_asks(command):
    decision = _decide_powershell_in_windows_step(command)
    assert decision.action == "ask"
    assert decision.reason.startswith("재귀 삭제 확인 필요: `")


@pytest.mark.parametrize("command", [
    # PR #535 review: a .NET delete spelled inside text a data command prints or writes is not a call.
    "Write-Output '$_.Delete()'", """Set-Content notes.txt '[IO.File]::Delete("x")'""",
])
def test_a_dotnet_delete_inside_a_powershell_data_string_is_text(command):
    assert _decide_powershell_in_windows_step(command).action == "allow"
    # Text run as code is still read as a delete.
    assert _decide_powershell_in_windows_step(f"iex {command.split(' ', 1)[1]}").action == "ask"


OUTSIDE_HOME = {"HOME": "/home/pi", "USERPROFILE": "C:\\Users\\pi"}


@pytest.mark.parametrize("command", [
    # #521: a write destination starting with `~` is the shell's home, outside the write roots here.
    "cp a ~/x", "echo x > ~/.bashrc", "curl -o ~/x https://example.org/x", "echo x | tee -a ~/x", "cp a ~",
])
def test_a_tilde_write_destination_is_judged_at_the_shell_home(command):
    decision = evaluate_tool("Bash", {"command": command}, _policy(), ["/work/step"], "/work/step",
                             windows=False, environ=OUTSIDE_HOME)
    assert decision.action == "ask" and "outside allowed roots" in decision.reason
    inside = evaluate_tool("Bash", {"command": command}, _policy(), ["/work/step"], "/work/step",
                           windows=False, environ={"HOME": "/work/step"})
    assert inside.action == "allow"


@pytest.mark.parametrize("command,environ", [
    ("cp a ~root/x", OUTSIDE_HOME), ("cp a ~+/x", OUTSIDE_HOME), ("echo x > ~-/x", OUTSIDE_HOME),
    ("cp a ~/x", {}),  # no HOME: nothing to expand to
])
def test_a_tilde_write_destination_the_gate_cannot_expand_asks(command, environ):
    decision = evaluate_tool("Bash", {"command": command}, _policy(), ["/work/step"], "/work/step",
                             windows=False, environ=environ)
    assert decision.action == "ask" and "cannot expand" in decision.reason


@pytest.mark.parametrize("command", [
    "Set-Content ~/x 'a'", r"'a' > ~\x", "Invoke-WebRequest https://example.org/x -OutFile ~/x",
])
def test_a_powershell_tilde_write_is_judged_at_every_home_the_shell_may_use(command):
    root = "C:/Users/pi/labhq/work/step"
    outside = {"USERPROFILE": "C:\\Users\\pi"}
    assert evaluate_tool("PowerShell", {"command": command}, _policy(), [root], root, windows=True,
                         environ=outside).action == "ask"
    # Windows PowerShell 5.1 may take HOMEDRIVE+HOMEPATH instead of USERPROFILE: both must be inside.
    split = {"USERPROFILE": root, "HOMEDRIVE": "H:", "HOMEPATH": "\\"}
    assert evaluate_tool("PowerShell", {"command": command}, _policy(), [root], root, windows=True,
                         environ=split).action == "ask"
    assert evaluate_tool("PowerShell", {"command": command}, _policy(), [root], root, windows=True,
                         environ={"USERPROFILE": root}).action == "allow"


@pytest.mark.parametrize("command", ["cat ~/notes.txt > out.txt", "cp ~/a out.txt", "ls ~"])
def test_tilde_read_paths_are_unchanged(command):
    assert evaluate_tool("Bash", {"command": command}, _policy(), ["/work/step"], "/work/step",
                         windows=False, environ=OUTSIDE_HOME).action == "allow"


@pytest.mark.parametrize("command", ["find -- /c/Users/pi/Documents -delete", "find -files0-from list.txt -delete"])
def test_find_start_paths_given_through_options_ask(command):
    decision = _decide_in_windows_step(command)
    assert decision.action == "ask"
    assert decision.reason.startswith("재귀 삭제 확인 필요: `")
