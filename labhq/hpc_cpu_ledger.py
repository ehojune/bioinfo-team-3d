"""Durable, runner-local admission for Slurm jobs managed by LabHQ."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
import uuid
from collections import defaultdict
from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path

from .test_guard import guard_test_path

LEDGER_LOCK_WAIT_S = 30
_FINAL = frozenset({"BOOT_FAIL", "CANCELLED", "COMPLETED", "DEADLINE", "FAILED", "NODE_FAIL",
                    "OUT_OF_MEMORY", "PREEMPTED", "REVOKED", "TIMEOUT"})
# These flags can also appear while a job is being requeued. Keep counting a live row with either flag.
_ACCOUNTING_FINAL_FLAGS = frozenset({"LAUNCH_FAILED", "RECONFIG_FAIL"})
_JOB_ID = re.compile(r"[0-9]+(?:[_+][0-9]+)?\Z")
SUBMISSION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")
_LEGACY_CLOCK_TOLERANCE_S = 120


def submission_key(*parts: object) -> str:
    """Identify the logical submit independently of timestamped script and log paths."""
    payload = json.dumps(parts, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()


@dataclass
class Submission:
    key: str
    script_text: str
    receipt: dict
    legacy_identity: tuple | None = None
    fingerprint: str | None = None
    repeat_after_finish: bool = False


@dataclass(frozen=True)
class SlurmRow:
    job_id: str
    cpus: int
    submitted_at: int
    state: str
    name: str

    @property
    def parent(self) -> str:
        return re.split(r"[_+]", self.job_id, maxsplit=1)[0]

    @property
    def final(self) -> bool:
        return self.state.split(maxsplit=1)[0] in _FINAL

    @property
    def accounting_final(self) -> bool:
        return self.final or self.state in _ACCOUNTING_FINAL_FLAGS

    def matches_legacy(self, job: dict) -> bool:
        submitted_at = job.get("submitted_at")
        return (self.parent == str(job.get("job_id")) and self.name == job.get("name")
                and isinstance(submitted_at, (int, float))
                and abs(self.submitted_at - submitted_at) <= _LEGACY_CLOCK_TOLERANCE_S)


def slurm_rows(stdout: str) -> list[SlurmRow]:
    rows = []
    for line in stdout.splitlines():
        fields = [field.strip() for field in line.split("|", 4)]
        if (len(fields) != 5 or not _JOB_ID.fullmatch(fields[0]) or not fields[1].isascii()
                or not fields[1].isdigit() or not fields[2].isascii() or not fields[2].isdigit()
                or not fields[3] or not fields[4]):
            raise RuntimeError("unexpected Slurm CPU ledger query output")
        rows.append(SlurmRow(fields[0], int(fields[1]), int(fields[2]), fields[3], fields[4]))
    return rows


class CpuLedger:
    def __init__(self, state_path: Path, site: str, limit: int):
        self.state_path, self.site, self.limit = state_path, site, limit
        self.path = state_path.with_suffix(".hpc-cpus.sqlite3")
        guard_test_path(self.path)

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path, timeout=LEDGER_LOCK_WAIT_S)) as db, db:
            db.row_factory = sqlite3.Row
            db.execute("BEGIN IMMEDIATE")
            db.execute("""CREATE TABLE IF NOT EXISTS submissions (
                site TEXT NOT NULL, key TEXT NOT NULL, name TEXT NOT NULL, cpus INTEGER NOT NULL,
                reserved_at REAL NOT NULL, receipt TEXT NOT NULL, job_id TEXT, finished INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (site, key))""")
            yield db

    def _legacy_jobs(self) -> list[dict]:
        """Read this runner's trusted pre-ledger records, including when init runs without a daemon."""
        if not self.state_path.is_file():
            return []
        guard_test_path(self.state_path)
        with closing(sqlite3.connect(self.state_path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
            return [job for raw, in db.execute("SELECT body FROM state WHERE kind = 'job'")
                    if (job := json.loads(raw)).get("submitted_by_runner") is True
                    and job.get("scheduler") == "slurm" and job.get("site_profile") in (None, self.site)
                    and not job.get("cpu_submission_key")]

    def reserve(self, submission: Submission, cores: int, query: Callable) -> tuple[str | None, str]:
        """Reconcile and reserve under the same write lock across runner and init processes.

        A reservation commits before the scheduler is called. Its name lets either process find an accepted job even
        when the submitting process died before recording the ID. Missing evidence never erases a reservation.
        """
        problem = None
        with self._transaction() as db:
            rows = list(db.execute("SELECT * FROM submissions WHERE site = ?", (self.site,)))
            key = submission.key
            by_key = {row["key"]: row for row in rows}
            if submission.repeat_after_finish:
                # Keep each init trial's old reservation and ID, so completion cannot race with a new submit.
                while (next_key := submission_key("next init trial", key)) in by_key:
                    key = next_key
            existing = by_key.get(key)
            if existing:
                receipt = json.loads(existing["receipt"])
                recorded_fingerprint = receipt.pop("cpu_submission_fingerprint", None)
                if not submission.repeat_after_finish and recorded_fingerprint != submission.fingerprint:
                    raise RuntimeError("submission_id is already used with a different job payload")
            if existing and existing["job_id"] and not submission.repeat_after_finish:
                submission.receipt = {**receipt, "replayed": True}
                return existing["job_id"], existing["name"]
            legacy = self._legacy_jobs()
            if submission.legacy_identity is not None and any(
                tuple(job.get(field) for field in ("task_id", "agent_id", "name", "script_sha256"))
                == submission.legacy_identity for job in legacy
            ):
                raise RuntimeError("a pre-ledger Slurm submission matches this job; check the existing job before retrying")
            live, accounting = query(rows, legacy)
            used, recovered, finished = self._reconcile(db, rows, live, accounting, key)
            if submission.repeat_after_finish and existing and existing["job_id"] and finished:
                # If this call just recovered a lost receipt, return that ID once before starting another trial.
                key = next_key
                existing = None
            if existing:
                if (submission.repeat_after_finish and recorded_fingerprint is not None
                        and recorded_fingerprint != submission.fingerprint):
                    problem = "the pending init trial used different submission settings; check it before retrying"
                elif recovered:
                    submission.receipt = {**receipt, "replayed": True}
                    return recovered, existing["name"]
                else:
                    problem = "Slurm acceptance is unconfirmed; the CPU reservation is held and the job was not resent"
            else:
                legacy_used = self._legacy_cpus(legacy, live, accounting)
                if legacy_used is None:
                    problem = "CPU use of a previously submitted Slurm job is unconfirmed; no new job was submitted"
                elif used + legacy_used + cores > self.limit:
                    problem = (f"Slurm CPU limit {self.limit}: {used + legacy_used} reserved + {cores} requested; "
                               "no new job was submitted")
                else:
                    name = "labhq-" + uuid.uuid4().hex
                    receipt = dict(submission.receipt)
                    if submission.fingerprint is not None:
                        receipt["cpu_submission_fingerprint"] = submission.fingerprint
                    db.execute("INSERT INTO submissions (site, key, name, cpus, reserved_at, receipt) "
                               "VALUES (?, ?, ?, ?, ?, ?)",
                               (self.site, key, name, cores, time.time(), json.dumps(receipt)))
                    submission.key = key
                    return None, name
        raise RuntimeError(problem)

    def _reconcile(self, db, reservations, live, accounting, key) -> tuple[int, str | None, bool]:
        by_name = defaultdict(list)
        for match in (*live, *accounting):
            by_name[match.name].append(match)
        active_cpus = defaultdict(int)
        for row in live:
            if not row.final:
                active_cpus[row.name] += row.cpus
        used, recovered, current_finished = 0, None, False
        for reservation in reservations:
            name, job_id, finished = reservation["name"], reservation["job_id"], reservation["finished"]
            matches = by_name[name]
            if any(match.parent != (job_id or matches[0].parent) for match in matches):
                raise RuntimeError("conflicting Slurm IDs for one LabHQ submission")
            if matches:
                job_id = matches[0].parent
                finished = name not in active_cpus and all(match.accounting_final for match in matches)
            cpus = max(reservation["cpus"], active_cpus[name])
            db.execute("UPDATE submissions SET job_id = ?, finished = ?, cpus = ? WHERE site = ? AND key = ?",
                       (job_id, finished, cpus, self.site, reservation["key"]))
            if not finished:
                used += cpus
            if reservation["key"] == key:
                recovered, current_finished = job_id, bool(finished)
        return used, recovered, current_finished

    @staticmethod
    def _legacy_cpus(jobs: list[dict], live: list[SlurmRow], accounting: list[SlurmRow]) -> int | None:
        total = 0
        live_by_id, accounting_by_id = defaultdict(list), defaultdict(list)
        for row in live:
            live_by_id[row.parent].append(row)
        for row in accounting:
            accounting_by_id[row.parent].append(row)
        for job in jobs:
            jid = str(job.get("job_id"))
            family = live_by_id[jid]
            active = [row for row in family if not row.final]
            matches = [row for row in (*family, *accounting_by_id[jid]) if row.matches_legacy(job)]
            if active and matches:
                # A heterogeneous component can have its own name. Once the parent is identified, count every
                # live member. A changed submit time can indicate a requeue or an ID reused for a different job.
                if any(row.cpus < 1 or abs(row.submitted_at - job["submitted_at"]) > _LEGACY_CLOCK_TOLERANCE_S
                       for row in active):
                    return None
                total += sum(row.cpus for row in active)
            elif active and (not terminal_legacy(job) or any(row.name == job.get("name") for row in active)):
                return None
            elif any(not row.accounting_final for row in matches) or (not matches and not terminal_legacy(job)):
                return None
        return total

    def accept(self, key: str, job_id: str) -> None:
        with self._transaction() as db:
            updated = db.execute("UPDATE submissions SET job_id = ? WHERE site = ? AND key = ? "
                                 "AND (job_id IS NULL OR job_id = ?)", (job_id, self.site, key, job_id))
            if updated.rowcount != 1:
                raise RuntimeError("the Slurm job ID conflicts with the CPU reservation")

    def unsent(self, key: str) -> None:
        """Release after a pre-submit refusal or a confirmed Slurm rejection."""
        with self._transaction() as db:
            db.execute("DELETE FROM submissions WHERE site = ? AND key = ? AND job_id IS NULL", (self.site, key))


def terminal_legacy(job: dict) -> bool:
    return job.get("terminal") is True and job.get("state") in {"completed", "failed", "cancelled"}
