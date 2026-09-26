from labhq.settings import HpcSettings
from labhq.tools.scheduler import (
    Scheduler, build_script, normalize_walltime, parse_pbs_qstat_full, parse_pbs_qstat_table,
    parse_sge_qacct, parse_sge_qstat, sanitize_job_name,
)

SGE_QSTAT = """job-ID  prior   name       user         state submit/start at     queue                          slots ja-task-ID
-----------------------------------------------------------------------------------------------------------------
 123456 0.55500 align_s1   labuser      r     09/24/2026 10:12:33 all.q@node01                       8
 123457 0.00000 align_s2   labuser      qw    09/24/2026 10:12:40                                    8
 123458 0.00000 broken     labuser      Eqw   09/24/2026 10:13:01                                    1
"""

SGE_QACCT = """==============================================================
qname        all.q
hostname     node01
jobname      align_s1
jobnumber    123456
failed       0
exit_status  0
"""

PBS_TABLE = """
server.cluster:
                                                                                  Req'd       Req'd       Elap
Job ID                  Username    Queue    Jobname          SessID  NDS   TSK   Memory      Time    S   Time
----------------------- ----------- -------- ---------------- ------ ----- ------ --------- --------- - ---------
12345.server            labuser     batch    align_s1          23456     1      8       32gb  24:00:00 R  01:02:03
12346.server            labuser     batch    qc_s1               --      1      1        4gb  04:00:00 Q       --
"""

PBS_FULL = """Job Id: 12345.server
    Job_Name = align_s1
    job_state = C
    exit_status = 1
    resources_used.walltime = 00:10:00
"""


def test_sge_qstat():
    jobs = parse_sge_qstat(SGE_QSTAT)
    assert [(j.job_id, j.state) for j in jobs] == [("123456", "running"), ("123457", "queued"), ("123458", "error")]


def test_sge_qacct():
    info = parse_sge_qacct("123456", SGE_QACCT)
    assert info.state == "completed" and info.exit_status == 0 and info.terminal


def test_pbs_table_and_full():
    jobs = parse_pbs_qstat_table(PBS_TABLE)
    assert [(j.job_id, j.state, j.name) for j in jobs] == [("12345.server", "running", "align_s1"),
                                                          ("12346.server", "queued", "qc_s1")]
    full = parse_pbs_qstat_full(PBS_FULL)
    assert full and full.state == "failed" and full.exit_status == 1


def test_submit_args_sge_divides_memory_per_slot():
    s = Scheduler(HpcSettings(scheduler="sge"))
    args = s.submit_args("/w/j.sh", "1bad name", 8, "32G", "2:00:00", "all.q", "/w/o", "/w/e")
    assert args[:4] == ["qsub", "-terse", "-N", "j_1bad_name"]
    assert "-pe" in args and args[args.index("-pe") + 2] == "8"
    assert "h_vmem=4G" in args and "h_rt=02:00:00" in args and args[-1] == "/w/j.sh"


def test_submit_args_pbs():
    s = Scheduler(HpcSettings(scheduler="pbs"))
    args = s.submit_args("/w/j.sh", "align", 4, "16G", "1-00:00:00", None, "/w/o", "/w/e")
    assert "nodes=1:ppn=4,mem=16gb,walltime=24:00:00" in args


def test_submit_prefix_only_on_qsub(monkeypatch):
    from subprocess import CompletedProcess

    s = Scheduler(HpcSettings(scheduler="sge", user="data-account",
                              submit_prefix=["sudo", "-n", "-u", "data-account"]))
    args = s.submit_args("/w/j.sh", "align", 1, "4G", "01:00:00", None, "/w/o", "/w/e")
    assert args[:5] == ["sudo", "-n", "-u", "data-account", "qsub"]
    seen = []

    def fake_run(argv):
        seen.append(argv)
        return CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(s, "_run", fake_run)
    s.queue()
    assert seen == [["qstat", "-u", "data-account"]]


def test_helpers():
    assert normalize_walltime("90:00") == "90:00:00"  # HH:MM
    assert normalize_walltime("1-02:00:00") == "26:00:00"
    assert sanitize_job_name("qc run") == "qc_run"
    script = build_script("#!/bin/bash\n#$ -V\necho hi", "/w")
    lines = script.splitlines()
    assert lines[0] == "#!/bin/bash" and lines[1] == "#$ -V" and "set -euo pipefail" in lines and lines[-1] == "echo hi"
