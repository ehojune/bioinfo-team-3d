"""Issue #172: PR #171 follow-ups. Fake schedulers and a fake broker only; no cluster is contacted."""

import json
import subprocess
from subprocess import CompletedProcess

import httpx
import pytest

from labhq.policy import evaluate_tool
from labhq.runner.approvals import Broker
from labhq.runner.daemon import Runner
from labhq.settings import HpcSettings, PolicySettings, Settings
from labhq.tools.scheduler import Scheduler, script_directives, slurm_cluster_directive
from tests.fixtures.fake_slurm import FakeSlurm

DISABLED = "Slurm accounting storage is disabled"


class _Recorder:
    """Stands in for _run: every argv is recorded and nothing is executed."""

    def __init__(self, stdout="4100\n"):
        self.calls, self.stdout = [], stdout

    def __call__(self, args):
        self.calls.append(args)
        return CompletedProcess(args, 0, self.stdout, "")


def _mcp(monkeypatch, tmp_path, scheduler, *, threshold, broker_reply=None):
    import labhq.tools.hpc_mcp as hpc

    settings = Settings(hpc=HpcSettings(scheduler=scheduler, user="fixture"))
    settings.policy.approvals.hpc_core_hours_threshold = threshold
    backend = Scheduler(settings.hpc)
    backend._run = _Recorder("4100\n" if scheduler != "pbs" else "4100.server\n")
    seen = []

    def prepare(workdir, script_path, logs, body, *args):
        script_path.parent.mkdir(parents=True, exist_ok=True)
        script_path.write_text(body, encoding="utf-8")

    async def broker(path, payload, timeout):
        seen.append((path, payload))
        if isinstance(broker_reply, Exception):
            raise broker_reply
        return (broker_reply or {}).get(path, {})

    monkeypatch.setattr(hpc, "S", settings)
    monkeypatch.setattr(hpc, "WORKDIR", tmp_path)
    monkeypatch.setattr(hpc, "SCHED", backend)
    monkeypatch.setattr(hpc, "_prepare_job_files", prepare)
    monkeypatch.setattr(hpc, "_broker", broker)
    return hpc, backend._run, seen


# ---------- 1. directives the core-hour estimate cannot see ----------

@pytest.mark.parametrize("scheduler,directive", [
    ("slurm", "#SBATCH --array=1-1000"), ("slurm", "#SBATCH --exclusive"), ("slurm", "#SBATCH --gres=gpu:8"),
    ("slurm", "#PBS -l nodes=10:ppn=32"),  # sbatch also reads #PBS lines unless --ignore-pbs
    ("sge", "#$ -pe smp 64"), ("sge", "#$ -t 1-1000"), ("pbs", "#PBS -J 1-1000"), ("pbs", "#PBS -t 1-1000"),
])
async def test_scheduler_directives_force_pi_approval_below_the_threshold(monkeypatch, tmp_path, scheduler,
                                                                          directive):
    hpc, run, seen = _mcp(monkeypatch, tmp_path, scheduler, threshold=100,
                          broker_reply={"/approval": {"approved": False, "note": "fixture PI denied"}})
    denied = json.loads(await hpc.hpc_submit(f"{directive}\nsleep 1", "array", cores=1, walltime="01:00:00"))
    assert denied["submitted"] is False and run.calls == []
    (path, payload), = seen
    assert path == "/approval" and payload["detail"]["script_directives"] == [directive]
    assert "core-h 계산 밖" in payload["summary"]


def test_pbs_directive_prefix_from_the_environment_counts(monkeypatch):
    monkeypatch.setenv("PBS_DPREFIX", "#LAB")
    assert script_directives("#LAB -J 1-1000\nsleep 1") == ["#LAB -J 1-1000"]
    assert slurm_cluster_directive("#LAB -M other\nsleep 1") is None  # only #SBATCH lines pick a cluster


async def test_plain_script_below_the_threshold_still_needs_no_approval(monkeypatch, tmp_path):
    hpc, run, seen = _mcp(monkeypatch, tmp_path, "slurm", threshold=100)
    result = json.loads(await hpc.hpc_submit("# just a comment\nsleep 1", "plain", cores=1, walltime="01:00:00"))
    assert result["submitted"] and [path for path, _ in seen] == ["/jobs/track"] and len(run.calls) == 1


# ---------- 2. cancel only jobs labhq tracks for this agent ----------

async def test_cancel_refuses_jobs_the_broker_did_not_track(monkeypatch, tmp_path):
    hpc, run, seen = _mcp(monkeypatch, tmp_path, "slurm", threshold=0,
                          broker_reply={"/jobs/owned": {"owned": False}})
    with pytest.raises(Exception, match="cancels only jobs"):
        await hpc.hpc_cancel("4100")
    assert run.calls == [] and seen == [("/jobs/owned", {"task_id": hpc.TASK, "agent_id": hpc.AGENT,
                                                          "job_id": "4100"})]


async def test_cancel_fails_closed_when_the_broker_is_unreachable(monkeypatch, tmp_path):
    hpc, run, _ = _mcp(monkeypatch, tmp_path, "slurm", threshold=0, broker_reply=ConnectionError("fixture down"))
    with pytest.raises(Exception, match="fixture down"):
        await hpc.hpc_cancel("4100")
    assert run.calls == []


async def test_cancel_of_a_tracked_job_reaches_scancel(monkeypatch, tmp_path):
    hpc, run, _ = _mcp(monkeypatch, tmp_path, "slurm", threshold=0, broker_reply={"/jobs/owned": {"owned": True}})
    assert json.loads(await hpc.hpc_cancel("4100_3"))["job_id"] == "4100_3"
    assert run.calls == [["scancel", "4100_3"]]


def _runner(tmp_path) -> Runner:
    s = Settings()
    s.gateway.state_dir = s.runner.state_dir = str(tmp_path / "state")
    s.runner.workspace_root = str(tmp_path / "runs")
    s.runner.agents_dir = str(tmp_path / "agents")
    s.runner.talent_dir = str(tmp_path / "talent")
    s.hpc.scheduler, s.hpc.user = "slurm", "fixture"
    return Runner(s)


async def test_broker_owns_only_this_agents_tracked_jobs_and_their_tasks(tmp_path):
    runner = _runner(tmp_path)
    try:
        runner.task_req.update({"t1": "r1", "t2": "r1", "t9": "r9"})
        await runner._on_track({"job_id": "4100", "task_id": "t1", "agent_id": "analyst", "name": "align"})
        await runner._on_track({"job_id": "123[].pbs", "task_id": "t1", "agent_id": "analyst", "name": "qc"})
        await runner._on_track({"job_id": "5200", "task_id": "t9", "agent_id": "other", "name": "theirs"})
        transport = httpx.ASGITransport(app=runner.broker.app)

        async def owned(token, job_id):
            async with httpx.AsyncClient(transport=transport, base_url="http://broker") as client:
                r = await client.post("/jobs/owned", headers={"X-Labhq-Token": token}, json={"job_id": job_id})
                assert r.status_code == 200
                return r.json()["owned"]

        same_task = runner.broker.issue_task_token("t1", "analyst", "r1")
        resumed = runner.broker.issue_task_token("t2", "analyst", "r1")  # same request, later session
        stranger = runner.broker.issue_task_token("t3", "analyst", "r3")
        for job_id in ("4100", "4100_3", "4100+1", "4100.0", "123[7].pbs"):
            assert await owned(same_task, job_id) and await owned(resumed, job_id), job_id
        for job_id in ("41000", "4101", "5200", "123[7].other", "999"):
            assert not await owned(same_task, job_id), job_id
        assert not await owned(stranger, "4100")
    finally:
        runner.store.close()


async def test_broker_without_ownership_handler_owns_nothing():
    async def noop(_):
        return None

    broker = Broker(0, noop, noop, noop)
    token = broker.issue_task_token("t1", "analyst", "r1")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=broker.app), base_url="http://broker") as c:
        r = await c.post("/jobs/owned", headers={"X-Labhq-Token": token}, json={"job_id": "4100"})
        assert r.json() == {"owned": False}
        bad = await c.post("/jobs/owned", headers={"X-Labhq-Token": "wrong"}, json={"job_id": "4100"})
        assert bad.status_code == 401


# ---------- 3. -V / -v cannot export the MCP process environment ----------

@pytest.mark.parametrize("scheduler", ["sge", "pbs", "slurm"])
def test_submit_environment_carries_no_tokens(monkeypatch, scheduler):
    secrets = {"LABHQ_BROKER_TOKEN": "fixture-broker", "ANTHROPIC_API_KEY": "fixture-key",
               "GITHUB_TOKEN": "fixture-gh", "OPENAI_API_KEY": "fixture-openai", "LABHQ_TASK_ID": "t1"}
    needed = {"PATH": "/usr/bin", "HOME": "/home/fixture", "SGE_ROOT": "/opt/sge", "PBS_EXEC": "/opt/pbs",
              "SLURM_CONF": "/etc/slurm/slurm.conf", "LC_ALL": "C"}
    for key, value in {**secrets, **needed}.items():
        monkeypatch.setenv(key, value)
    seen = []

    def run(args, **kw):
        seen.append(kw)
        return CompletedProcess(args, 0, "4100\n", "")

    monkeypatch.setattr(subprocess, "run", run)
    backend = Scheduler(HpcSettings(scheduler=scheduler, user="fixture"))
    backend.submit("/w/j.sh", "align")  # a script "#$ -V" / "#PBS -V" would export exactly this env
    env = seen[0]["env"]
    assert not set(secrets) & set(env)
    assert all(env.get(key) == value for key, value in needed.items())


# ---------- 4. shell commands that create or change jobs ----------

@pytest.mark.parametrize("tool", ["Bash", "PowerShell"])
@pytest.mark.parametrize("command", [
    "qresub 12", "qalter -l h_rt=99:00:00 12", "qhold 12", "qrls 12", "qsig -s KILL 12", "qmod -rj 12",
    "qmove long 12", "qrerun 12", "qorder 12 13", "qsh", "qmake -j 8", "scrontab -e",
    "strigger --set --jobid=4100 --fini --program=/w/x.sh", "scontrol requeue 4100", "scontrol hold 4100",
    "scontrol release 4100", "scontrol update JobId=4100 TimeLimit=7-0", "scontrol -o update JobId=4100",
    "scontrol show job 4100; scontrol requeue 4100", "scontrol",
])
def test_job_changing_commands_ask(tool, command):
    assert evaluate_tool(tool, {"command": command}, PolicySettings()).action == "ask"


@pytest.mark.parametrize("command", ["scontrol show job 4100", "scontrol -o show job 4100",
                                     "scontrol --details show job 4100", "qstat -f 12", "qacct -j 12"])
def test_read_only_scheduler_commands_stay_allowed(command):
    assert evaluate_tool("Bash", {"command": command}, PolicySettings()).action == "allow"


# ---------- 5. bundled short options choosing a cluster ----------

@pytest.mark.parametrize("body,option", [("#SBATCH -vM other\necho hi", "-vM"),
                                         ("#SBATCH -vQMother\necho hi", "-vQMother")])
def test_bundled_cluster_option_is_found(tmp_path, body, option):
    assert slurm_cluster_directive(body) == option
    config = tmp_path / "config.yaml"
    config.write_text(f'hpc:\n  scheduler: slurm\n  slurm:\n    sbatch_args: ["{option}"]\n', encoding="utf-8")
    with pytest.raises(ValueError, match="another cluster"):
        Settings.load(str(config))


@pytest.mark.parametrize("body", ["#SBATCH -JM\necho hi", "#SBATCH -vJM\necho hi", "#SBATCH -pMain\necho hi"])
def test_m_inside_an_option_value_is_not_a_cluster(body):
    assert slurm_cluster_directive(body) is None  # -J / -p take the rest of the token as their value


# ---------- 6. clusters without Slurm accounting ----------

def _slurm():
    backend = Scheduler(HpcSettings(scheduler="slurm", user="fixture"))
    fake = FakeSlurm()
    backend._run = fake
    return backend, fake


@pytest.mark.parametrize("stub", [(1, "", DISABLED + "\n"), (0, DISABLED + "\n", "")])
def test_without_accounting_squeues_final_state_is_used(stub):
    backend, fake = _slurm()
    job_id = backend.submit("/w/j.sh", "align")
    fake.set(job_id, "COMPLETED")
    fake.fail["sacct"] = stub
    info = backend.status(job_id)
    assert (info.state, info.terminal) == ("completed", True)


@pytest.mark.parametrize("stub", [(1, "", DISABLED + "\n"), (0, DISABLED + "\n", "")])
def test_without_accounting_a_job_gone_from_squeue_is_missing_not_an_error(stub):
    backend, fake = _slurm()
    job_id = backend.submit("/w/j.sh", "align")
    fake.purge(job_id)
    fake.fail["sacct"] = stub
    info = backend.status(job_id)
    assert info.state == "missing" and "accounting storage is disabled" in info.detail


async def test_runner_wakes_with_unknown_finished_when_accounting_is_disabled(tmp_path):
    runner = _runner(tmp_path)
    fake = FakeSlurm()
    runner.scheduler._run = fake
    try:
        job_id = runner.scheduler.submit("/w/j.sh", "align")
        await runner._on_track({"job_id": job_id, "task_id": "t1", "agent_id": "a", "name": "align"})
        fake.purge(job_id)
        fake.fail["sacct"] = (1, "", DISABLED)
        for _ in range(3):
            await runner._poll_jobs()
        woke = [e for e in runner.store.pending() if e["type"] == "jobs.finished"]
        assert len(woke) == 1 and woke[0]["data"]["jobs"][0]["state"] == "unknown_finished"
        assert fake.commands().count("sbatch") == 1
    finally:
        runner.store.close()
