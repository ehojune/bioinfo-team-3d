"""Issue #119: HPC first-setup consult against fixture SGE/PBS/Slurm output. No cluster is contacted."""

from subprocess import CompletedProcess

import pytest

from labhq import hpc_consult as consult
from labhq.settings import HpcSettings, PolicySettings
from labhq.tools.scheduler import Scheduler

SGE = {
    ("qconf", "-sql"): "all.q\nlong.q\n",
    ("qconf", "-spl"): "make\nmpi\nsmp\n",
    ("qconf", "-sp", "make"): "pe_name            make\nslots              999\nallocation_rule    $round_robin\n",
    ("qconf", "-sp", "mpi"): "pe_name            mpi\nslots              999\nallocation_rule    $fill_up\n",
    ("qconf", "-sp", "smp"): "pe_name            smp\nslots              999\nallocation_rule    $pe_slots\n",
    ("qconf", "-sc"): """#name               shortcut   type        relop requestable consumable default  urgency
#----------------------------------------------------------------------------------------
arch                a          RESTRING    ==    YES         NO         NONE     0
h_rt                h_rt       TIME        <=    YES         NO         0:0:0    0
h_vmem              h_vmem     MEMORY      <=    YES         YES        0        0
mem_free            mf         MEMORY      <=    YES         NO         0        0
slots               s          INT         <=    YES         YES        1        1000
# >#< starts a comment but comments are not saved across edits --------
""",
}
# Another site: no "smp", a single-host PE under another name, memory counted per job, no h_rt.
SGE_OTHER = {
    ("qconf", "-sql"): "batch.q\n",
    ("qconf", "-spl"): "mpi\nthreaded\n",
    ("qconf", "-sp", "mpi"): "allocation_rule    $fill_up\n",
    ("qconf", "-sp", "threaded"): "allocation_rule    $pe_slots\n",
    ("qconf", "-sc"): "#name shortcut type relop requestable consumable default urgency\n"
                      "mem_req   mr   MEMORY  <=  YES  JOB  0  0\n",
}
PBS_PRO = {
    ("qstat", "-Q"): """Queue              Max   Tot Ena Str   Que   Run   Hld   Wat   Trn   Ext Type
---------------- ----- ----- --- --- ----- ----- ----- ----- ----- ----- ----
workq                0     0 yes yes     0     0     0     0     0     0 Exec
long                 0     0 yes yes     0     0     0     0     0     0 Exec
""",
    ("qstat", "-B", "-f"): "Server: pbs01\n    server_state = Active\n    default_queue = workq\n"
                           "    pbs_version = 2022.1.1\n",
    ("pbsnodes", "-a"): "node01\n     Mom = node01\n     state = free\n     resources_available.ncpus = 32\n"
                        "     resources_available.mem = 256gb\n",
}
TORQUE = {
    ("qstat", "-Q"): """Queue              Max    Tot   Ena   Str   Que   Run   Hld   Wat   Trn   Ext T   Cpt
----------------   ---   ----    --    --   ---   ---   ---   ---   ---   --- -   ---
batch                0      0   yes   yes     0     0     0     0     0     0 E     0
""",
    ("qstat", "-B", "-f"): "Server: torque01\n    default_queue = batch\n    pbs_version = 6.1.3\n",
    ("pbsnodes", "-a"): "node01\n     state = free\n     np = 16\n     ntype = cluster\n",
}
SLURM = {("sinfo", "-h", "-o", "%P"): "debug*\nlong\n"}
READ_ONLY = {("qconf", "-sql"), ("qconf", "-spl"), ("qconf", "-sc"), ("qstat", "-Q"), ("qstat", "-B", "-f"),
             ("pbsnodes", "-a"), ("sinfo", "-h", "-o", "%P")}


class Cluster:
    """Fixture query runner: canned stdout per argv, rc 1 for anything else."""

    def __init__(self, outputs):
        self.outputs, self.calls = outputs, []

    def __call__(self, argv):
        self.calls.append(tuple(argv))
        out = self.outputs.get(tuple(argv))
        return CompletedProcess(argv, 0 if out is not None else 1, out or "", "" if out is not None else "denied")


def _read_only(calls):
    return all(call in READ_ONLY or call[:2] == ("qconf", "-sp") for call in calls)


@pytest.mark.parametrize("outputs,expected", [
    (SGE, {"pe": "smp", "mem_resource": "h_vmem", "mem_per_slot": True, "runtime_resource": "h_rt"}),
    (SGE_OTHER, {"pe": "threaded", "mem_resource": "mem_req", "mem_per_slot": False, "runtime_resource": ""}),
])
def test_sge_survey_becomes_a_valid_hpc_draft(outputs, expected):
    cluster = Cluster(outputs)
    hpc, notes = consult.draft("sge", consult.survey("sge", cluster))
    assert _read_only(cluster.calls)
    settings = HpcSettings.model_validate(hpc)
    assert settings.scheduler == "sge" and settings.sge.model_dump() == expected
    assert any(queue in " ".join(notes) for queue in ("all.q", "batch.q"))


@pytest.mark.parametrize("outputs,pro,template", [
    (PBS_PRO, True, consult.PBS_PRO_TEMPLATE),
    (TORQUE, False, HpcSettings().pbs.resource_template),
])
def test_pbs_survey_tells_pro_from_torque(outputs, pro, template):
    cluster = Cluster(outputs)
    hpc, notes = consult.draft("pbs", consult.survey("pbs", cluster))
    assert _read_only(cluster.calls)
    settings = HpcSettings.model_validate(hpc)
    assert (settings.pbs.pro, settings.pbs.resource_template) == (pro, template)
    assert "서버 기본" in " ".join(notes)
    res = Scheduler(settings).submit_args("/w/j.sh", "qc", 4, "8G", "02:00:00", None, "/w/o", "/w/e")[4]
    assert "walltime=02:00:00" in res and ("ncpus=4" if pro else "ppn=4") in res


def test_failed_queries_keep_defaults_and_say_so():
    hpc, notes = consult.draft("sge", consult.survey("sge", Cluster({})))
    assert HpcSettings.model_validate(hpc).sge == HpcSettings().sge
    assert any("qconf -sc" in note for note in notes)
    hpc, notes = consult.draft("pbs", consult.survey("pbs", Cluster({})))
    assert HpcSettings.model_validate(hpc).pbs == HpcSettings().pbs and any("PBS Pro" in n for n in notes)


def test_slurm_partitions_are_listed_not_chosen():
    hpc, notes = consult.draft("slurm", consult.survey("slurm", Cluster(SLURM)))
    assert HpcSettings.model_validate(hpc).default_queue is None and "debug*" in " ".join(notes)


class Backend(Scheduler):
    """SGE backend whose commands are recorded; the trial job finishes after `polls` status checks."""

    def __init__(self, polls=1):
        super().__init__(HpcSettings(scheduler="sge", user="fixture"))
        self.calls, self.polls = [], polls

    def _run(self, args):
        self.calls.append(args)
        if args[0] == "qsub":
            return CompletedProcess(args, 0, "777\n", "")
        if args[0] == "qstat":
            self.polls -= 1
            row = " 777 0.5 labhq_trial fixture r 10/01/2026 10:00:00 all.q@node01 1\n"
            return CompletedProcess(args, 0, row if self.polls > 0 else "", "")
        if args[0] == "qacct":
            return CompletedProcess(args, 0, "jobname labhq_trial\nfailed 0\nexit_status 0\n", "")
        raise AssertionError(args)

    def submitted(self):
        return [c for c in self.calls if c[0] == "qsub"]


def test_declined_trial_submits_nothing(tmp_path):
    backend, asked = Backend(), []
    result = consult.trial_job(backend.cfg, tmp_path / "trial", lambda text: asked.append(text) or False,
                               backend=backend, sleep=lambda s: pytest.fail("no wait after a refusal"))
    assert result["outcome"] == "declined" and backend.calls == [] and not (tmp_path / "trial").exists()
    assert "qsub" in asked[0] and str(tmp_path) not in asked[0]  # the PI sees the command, not local paths


def test_approved_trial_is_submitted_once_and_followed_to_the_end(tmp_path):
    backend, waits = Backend(polls=3), []
    result = consult.trial_job(backend.cfg, tmp_path / "trial", lambda text: True, backend=backend,
                               sleep=waits.append, poll_s=2.0)
    assert (result["outcome"], result["job_id"], result["state"]) == ("tracked", "777", "completed")
    assert len(backend.submitted()) == 1 and waits == [2.0, 2.0]
    assert (tmp_path / "trial" / "labhq_trial.sh").read_text(encoding="utf-8").rstrip().endswith("sleep 1")


def test_trial_waits_a_bounded_number_of_polls(tmp_path):
    backend, waits = Backend(polls=10**6), []
    result = consult.trial_job(backend.cfg, tmp_path / "trial", lambda text: True, backend=backend,
                               sleep=waits.append, max_polls=4)
    assert result["outcome"] == "not_finished" and result["state"] == "running"
    assert len(waits) == 3 and len(backend.submitted()) == 1


def test_trial_never_runs_inside_a_restricted_zone(tmp_path):
    backend = Backend()
    policy = PolicySettings.model_validate({"data_zones": [{"path": str(tmp_path), "level": "restricted"}]})
    result = consult.trial_job(backend.cfg, tmp_path / "trial", lambda text: pytest.fail("asked"),
                               policy=policy, backend=backend)
    assert result["outcome"] == "refused" and backend.calls == []
