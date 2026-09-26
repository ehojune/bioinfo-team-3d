import pytest

from labhq.policy import claude_rule_path, claude_settings, core_hours, evaluate_tool, hpc_needs_approval, touches
from labhq.settings import DataZone, PolicySettings


def _policy(**kw):
    return PolicySettings(data_zones=[DataZone(path="/data/cohort", level="restricted")], **kw)


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


def test_write_outside_roots_asks():
    p = _policy()
    assert evaluate_tool("Write", {"file_path": "/etc/passwd"}, p, allowed_roots=["/w/task"]).action == "ask"
    assert evaluate_tool("Write", {"file_path": "/w/task/outputs/a.md"}, p, allowed_roots=["/w/task"]).action == "allow"


def test_path_normalization_boundary_and_shell_forms():
    zone = r"C:\Data\Cohort"
    assert touches({"command": r'cat --in="c:/data/cohort/A.vcf"'}, [zone]) == zone
    assert evaluate_tool("Read", {"file_path": "c:/DATA/cohort/A.vcf"},
                         PolicySettings(data_zones=[DataZone(path=zone)])).action == "deny"
    assert touches({"file_path": r"c:/DATA/cohort2/A.vcf"}, [zone]) is None
    assert touches({"command": "cat ../cohort/a.vcf"}, ["/data/cohort"], workdir="/data/task")
    assert touches({"command": "cat /data/cohort2/a.vcf"}, ["/data/cohort"]) is None


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
