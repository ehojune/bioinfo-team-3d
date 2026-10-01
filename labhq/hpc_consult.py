"""HPC first-setup consult (#119): read-only cluster queries → an `hpc:` draft, then at most one trial job.

The queries list queues, parallel environments and resources; none of them submits, alters or cancels a job.
The trial job (`sleep 1`, 1 core, 5 minutes) is submitted only after the PI says yes, once, and is then followed
with the same status commands the runner uses. Restricted data zones are never read or written.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import time
from pathlib import Path
from typing import Callable

from .policy import restricted_paths, touches_resolved
from .settings import HpcSettings, PolicySettings
from .tools.scheduler import Scheduler, build_script, command_env

Run = Callable[[list[str]], "subprocess.CompletedProcess | None"]

# Read-only queries per scheduler family.
SURVEY = {
    "sge": {"queues": ["qconf", "-sql"], "pes": ["qconf", "-spl"], "complexes": ["qconf", "-sc"]},
    "pbs": {"queues": ["qstat", "-Q"], "server": ["qstat", "-B", "-f"], "nodes": ["pbsnodes", "-a"]},
    "slurm": {"partitions": ["sinfo", "-h", "-o", "%P"]},
}
PE_PREFERENCE = ("smp", "threads", "openmp", "shm", "omp")
MEM_PREFERENCE = ("h_vmem", "mem_free", "virtual_free", "s_vmem", "h_rss", "mem_req", "m_mem_free")
PER_SLOT_LIMITS = ("h_vmem", "s_vmem", "h_rss")  # process limits SGE multiplies by the slots on a host
MAX_PE_QUERIES = 20
PBS_PRO_TEMPLATE = "select=1:ncpus={cores}:mem={mem},walltime={walltime}"
TRIAL = {"name": "labhq_trial", "cores": 1, "mem": "1G", "walltime": "00:05:00"}
NO_CLUSTER = ("클러스터가 없으면 hpc.scheduler: none입니다. 무거운 단계는 로컬 CLI로만 돌리고, "
              "로컬에서 할 수 없는 단계는 CSO가 계획에서 그렇다고 밝힙니다.")


def run_query(argv: list[str], timeout: float = 30) -> subprocess.CompletedProcess | None:
    """One read-only query with the scheduler command environment; None when it cannot run."""
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout, env=command_env())
    except (OSError, subprocess.SubprocessError):
        return None


def survey(scheduler: str, run: Run = run_query) -> dict:
    """Raw output of each read-only query; a query that fails leaves None and its candidates empty."""
    found: dict = {}
    for key, argv in SURVEY.get(scheduler, {}).items():
        p = run(argv)
        found[key] = p.stdout if p is not None and p.returncode == 0 else None
    if scheduler == "sge":
        rules = {}
        for pe in _lines(found.get("pes"))[:MAX_PE_QUERIES]:
            p = run(["qconf", "-sp", pe])
            m = re.search(r"^allocation_rule\s+(\S+)", p.stdout if p is not None and p.returncode == 0 else "", re.M)
            rules[pe] = m.group(1) if m else None
        found["pe_rules"] = rules
    return found


def _lines(text: str | None) -> list[str]:
    return [line.strip() for line in (text or "").splitlines() if line.strip()]


def _sge_complexes(text: str | None) -> dict[str, dict[str, str]]:
    rows = {}
    for line in _lines(text):
        parts = line.split()
        if line.startswith("#") or len(parts) < 6:
            continue
        rows[parts[0]] = {"type": parts[2].upper(), "requestable": parts[4].upper(), "consumable": parts[5].upper()}
    return rows


def _table_names(text: str | None) -> list[str]:
    """First column of the rows below a `----` rule (qstat -Q)."""
    names, body = [], False
    for line in _lines(text):
        if set(line) <= set("- "):
            body = True
        elif body:
            names.append(line.split()[0])
    return names


def _pbs_pro(nodes: str | None, server: str | None) -> bool | None:
    if nodes and "resources_available." in nodes:
        return True
    if nodes and re.search(r"^\s+np\s*=\s*\d+", nodes, re.M):
        return False
    m = re.search(r"^\s*pbs_version\s*=\s*(\d+)", server or "", re.M)
    return None if not m else int(m.group(1)) >= 13  # Torque ≤ 7, PBS Pro/OpenPBS ≥ 13


def draft(scheduler: str, found: dict) -> tuple[dict, list[str]]:
    """`hpc:` draft from survey output and notes for the PI; anything not found keeps labhq's default."""
    hpc: dict = {"scheduler": scheduler}
    notes: list[str] = []
    if scheduler == "sge":
        sge: dict = {}
        pes = _lines(found.get("pes"))
        rules = found.get("pe_rules") or {}
        single = [pe for pe in pes if rules.get(pe) == "$pe_slots"]  # one host: what labhq's -pe cores means
        pick = (next((pe for pe in PE_PREFERENCE if pe in single), None) or (single[0] if single else None)
                or next((pe for pe in PE_PREFERENCE if pe in pes), None))
        if pick:
            sge["pe"] = pick
            if pick not in single:
                notes.append(f"PE {pick}: allocation_rule이 $pe_slots인지 확인하세요(한 노드에 코어를 모아야 합니다).")
        else:
            notes.append(f"한 노드용 PE를 찾지 못했습니다(후보: {', '.join(pes) or '없음'}). 여러 코어 잡 전에 정하세요.")
        complexes = _sge_complexes(found.get("complexes"))
        if complexes:
            memory = {name: c for name, c in complexes.items() if c["type"] == "MEMORY" and c["requestable"] != "NO"}
            mem = (next((n for n in MEM_PREFERENCE if n in memory and memory[n]["consumable"] != "NO"), None)
                   or next((n for n in MEM_PREFERENCE if n in memory), None))
            if mem:
                sge["mem_resource"] = mem
                # Consumable YES and the h_vmem-style limits count per slot; consumable JOB and load values
                # such as mem_free (a host must have that much free) count once per job.
                consumable = memory[mem]["consumable"]
                sge["mem_per_slot"] = consumable == "YES" or (consumable == "NO" and mem in PER_SLOT_LIMITS)
            else:
                notes.append("요청 가능한 메모리 리소스를 찾지 못했습니다. hpc.sge.mem_resource를 확인하세요.")
            h_rt = complexes.get("h_rt")
            sge["runtime_resource"] = "h_rt" if h_rt and h_rt["requestable"] != "NO" else ""
        else:
            notes.append("qconf -sc를 읽지 못해 메모리·시간 리소스는 기본값(h_vmem, h_rt)입니다.")
        hpc["sge"] = sge
        queues = _lines(found.get("queues"))
        if queues:
            notes.append(f"큐 후보: {', '.join(queues)}. 비워 두면 SGE가 고릅니다(hpc.default_queue).")
    elif scheduler == "pbs":
        pro = _pbs_pro(found.get("nodes"), found.get("server"))
        if pro is True:
            hpc["pbs"] = {"pro": True, "resource_template": PBS_PRO_TEMPLATE}
        elif pro is False:
            hpc["pbs"] = {"pro": False}
        else:
            notes.append("Torque인지 PBS Pro인지 정하지 못해 Torque 기본값을 둡니다. PBS Pro면 hpc.pbs.pro: true.")
        queues = _table_names(found.get("queues"))
        m = re.search(r"^\s*default_queue\s*=\s*(\S+)", found.get("server") or "", re.M)
        if queues:
            notes.append(f"큐 후보: {', '.join(queues)}" + (f" (서버 기본 {m.group(1)})" if m else "") +
                         ". 비워 두면 서버 기본 큐를 씁니다(hpc.default_queue).")
    elif scheduler == "slurm":
        partitions = _lines(found.get("partitions"))
        if partitions:
            notes.append(f"partition 후보: {', '.join(partitions)} (*가 기본). 계정·QOS는 hpc.slurm.sbatch_args에 더하세요.")
    notes.append("로그인 노드에서만 제출된다면 hpc.ssh_host를, 데이터 계정 전환이 필요하면 README §8의 "
                 "submit_prefix·user·job_group을 직접 정하세요. 상담은 이것들을 바꾸지 않습니다.")
    HpcSettings.model_validate(hpc)  # the draft must load as it is
    return hpc, notes


def trial_job(hpc: HpcSettings, trial_dir: Path, confirm: Callable[[str], bool], *,
              policy: PolicySettings | None = None, backend: Scheduler | None = None,
              sleep: Callable[[float], None] = time.sleep, poll_s: float = 5.0, max_polls: int = 60) -> dict:
    """At most one PI-approved trial job, followed until it finishes or `max_polls` status checks pass."""
    if policy and touches_resolved({"path": str(trial_dir)}, restricted_paths(policy)):
        return {"outcome": "refused", "message": "시험 폴더가 통제 데이터 구역 안이라 제출하지 않았습니다."}
    backend = backend or Scheduler(hpc)

    def hide(text: str) -> str:  # init never prints local paths
        return text.replace(str(trial_dir), "<trial>").replace(str(Path.home()), "~")

    script, out, err = (trial_dir / f"{TRIAL['name']}.{ext}" for ext in ("sh", "out", "err"))
    job = (TRIAL["name"], TRIAL["cores"], TRIAL["mem"], TRIAL["walltime"], hpc.default_queue, str(out), str(err))
    if not confirm(f"시험 잡 1회(sleep 1, 1코어, 5분): {hide(shlex.join(backend.submit_args(str(script), *job)))}"):
        return {"outcome": "declined", "message": "PI가 거절해 아무것도 제출하지 않았습니다."}
    try:
        trial_dir.mkdir(parents=True, exist_ok=True)
        script.write_text(build_script("sleep 1", str(trial_dir)), encoding="utf-8")
        if os.name != "nt":
            script.chmod(0o750)
    except OSError as e:
        return {"outcome": "prepare_failed", "message": f"시험 스크립트를 쓰지 못해 제출하지 않았습니다: {hide(str(e))}"}
    try:
        job_id = backend.submit(str(script), *job)
    except (RuntimeError, OSError) as e:
        return {"outcome": "submit_failed", "message": hide(str(e))}
    max_polls = max(1, max_polls)
    for attempt in range(max_polls):
        try:
            info = backend.status(job_id)
        except (RuntimeError, OSError, subprocess.SubprocessError) as e:  # e.g. qstat timed out: never resubmit
            return {"outcome": "status_failed", "job_id": job_id, "message": hide(str(e))}
        if info.terminal:
            break
        if attempt + 1 < max_polls:
            sleep(poll_s)
    if not info.terminal:
        return {"outcome": "not_finished", "job_id": job_id, "state": info.state,
                "message": f"{max_polls}회 확인 동안 끝나지 않았습니다(마지막 상태 {info.state}). 잡은 그대로 둡니다."}
    ok = info.state == "completed"
    return {"outcome": "tracked" if ok else "finished_not_ok", "job_id": job_id, "state": info.state,
            "exit_status": info.exit_status,
            "message": "제출부터 종료까지 추적됐습니다." if ok else
                       f"끝까지 추적됐지만 {info.state}입니다. 로그 경로가 계산 노드에서도 보이는지(공유 FS) 확인하세요."}
