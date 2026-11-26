"""Prepare and track one phase-6 audit/repair bundle without calling a model."""

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import sys
from contextlib import closing
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4


NANODOLLARS = 10**9
BUDGET = 10 * NANODOLLARS
LIMITS = {"audit": 4 * NANODOLLARS, "repair": 5 * NANODOLLARS}
FINDING_FIELDS = ("location", "problem", "evidence_quote", "revision")


def read(path):
    return path.read_text(encoding="utf-8-sig")


def dollars(amount):
    return format((Decimal(amount) / NANODOLLARS).normalize(), "f")


def charge(value):
    if not re.fullmatch(r"[0-9]{1,9}(?:\.[0-9]{1,9})?", value):
        raise argparse.ArgumentTypeError("use a nonnegative USD amount with up to nine decimal places")
    return int(Decimal(value) * NANODOLLARS)


def raise_walk_error(error):
    raise error


def snapshot(root):
    """Hash relative paths, empty directories and file bytes without following links."""
    digest = hashlib.sha256()
    root_info = root.lstat()
    if not stat.S_ISDIR(root_info.st_mode) or getattr(root_info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
        raise ValueError(f"expected an ordinary directory: {root}")
    for folder, dirs, files in os.walk(root, onerror=raise_walk_error):
        dirs.sort()
        for kind, names in (("d", dirs), ("f", sorted(files))):
            for name in names:
                path = Path(folder) / name
                info = path.lstat()
                shape = stat.S_ISDIR if kind == "d" else stat.S_ISREG
                if not shape(info.st_mode) or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                    raise ValueError(f"bundle contains a link or special entry: {path.relative_to(root)}")
                digest.update(json.dumps([kind, path.relative_to(root).as_posix()]).encode() + b"\n")
                if kind == "f":
                    contents = hashlib.sha256()
                    with path.open("rb") as stream:
                        for block in iter(lambda: stream.read(1024 * 1024), b""):
                            contents.update(block)
                    digest.update(contents.digest())
    return digest.hexdigest()


def prepare(args, run):
    source = args.source.resolve(strict=True)
    report = Path(args.report)
    if report.anchor or ".." in report.parts or report.suffix.lower() != ".md":
        raise ValueError("--report must be a relative Markdown path within the bundle")
    if run.is_relative_to(source):
        raise ValueError("run directory must sit outside the original bundle")
    source_hash = snapshot(source)
    if not (source / report).is_file():
        raise ValueError("report is missing from the original bundle")
    prompt = read(args.audit_prompt)
    if not prompt.strip():
        raise ValueError("the measurement-B audit prompt is empty")
    run.mkdir()
    for name in ("audit", "repair"):
        shutil.copytree(source, run / name, symlinks=True)
    state = {"source": str(source), "source_sha256": source_hash, "report": report.as_posix(),
             "audit_prompt": prompt, "analysis_session": str(args.session), "repair_session": str(uuid4()),
             "p1": {}, "events": [], "stop": None}
    check_integrity(state, run)
    with closing(sqlite3.connect(run / "ledger.sqlite3")) as db, db:
        db.execute("CREATE TABLE ledger (state TEXT NOT NULL)")
        db.execute("INSERT INTO ledger VALUES (?)", (json.dumps(state),))
    return status(state, run), 0


def check_integrity(state, run):
    for path in (Path(state["source"]), run / "audit"):
        if snapshot(path) != state["source_sha256"]:
            raise ValueError(f"baseline changed: {path}")
    repair_hash = snapshot(run / "repair")
    expected = state.get("result_sha256")
    if expected is not None:
        if repair_hash != expected:
            raise ValueError("final repair copy changed after recording")
    elif any(event["kind"] == "repair" for event in state["events"]):
        if status(state, run)["result_report"] is not None:
            raise ValueError("final repair copy has no recorded digest")
    elif repair_hash != state["source_sha256"]:
        raise ValueError("repair copy changed before its first reservation")
    if not (run / "repair" / state["report"]).is_file():
        raise ValueError("repair report is missing")
    return repair_hash


def status(state, run):
    events = state["events"]
    spent = sum(event.get("cost", 0) for event in events)
    pending = events[-1] if events and "cost" not in events[-1] else None
    repairs = sum(event["kind"] == "repair" for event in events)
    reason = state["stop"]
    if not reason and not pending and events:
        if not state["p1"]:
            reason = "complete"
        elif repairs == 2:
            reason = "repair_failed" if events[-1]["failed"] else "repair_limit"
        elif spent >= BUDGET:
            reason = "budget_exhausted"
    return {"state": reason or ("pending" if pending else "ready"), "spent_usd": dollars(spent),
            "remaining_usd": dollars(max(0, BUDGET - spent - (pending["limit"] if pending else 0))),
            "audit_attempts": int(bool(events)), "repair_attempts": repairs,
            "source_sha256": state["source_sha256"], "open_p1": sorted(state["p1"]),
            "pending": pending and pending["plan"],
            "result_report": str(run / "repair" / state["report"]) if reason in {"complete", "repair_limit"} else None}


def reserve(state, run):
    current = status(state, run)
    if current["state"] != "ready":
        raise ValueError(f"no new call allowed: {current['state']}")
    check_integrity(state, run)
    events = state["events"]
    kind = "repair" if events else "audit"
    limit = min(LIMITS[kind], charge(current["remaining_usd"]))
    plan = {"attempt": len(events) + 1, "kind": kind, "max_usd": dollars(limit),
            "working_dir": str(run / kind), "report": state["report"]}
    if kind == "audit":
        plan["prompt"] = (state["audit_prompt"].rstrip() + "\n\n"
                          f"Use {state['report']!r} as the report path for this bundle. Read its code and tables first.\n")
    else:
        needs_fork = not any(event.get("fork_created") for event in events)
        plan.update(resume_session=state["analysis_session"] if needs_fork else state["repair_session"],
                    fork_session=state["repair_session"] if needs_fork else None,
                    p1=state["p1"].copy())
    events.append({"kind": kind, "limit": limit, "plan": plan})
    return plan, 0


def apply_result(state, event, result_file):
    result = json.loads(read(result_file))
    if event["kind"] == "audit":
        issues = result.get("issues") if isinstance(result, dict) else None
        if not isinstance(issues, list):
            raise ValueError("measurement-B audit result must contain an issues list")
        p1 = {}
        for issue in issues:
            if (not isinstance(issue, dict) or issue.get("severity") not in ("P1", "P2")
                    or any(not isinstance(issue.get(field), str) for field in FINDING_FIELDS)
                    or not issue["location"].strip() or not issue["problem"].strip()):
                raise ValueError("invalid measurement-B finding: expected P1/P2, location, problem, quote and revision")
            if issue["severity"] == "P1":
                p1[f"P1-{len(p1) + 1}"] = {field: issue[field] for field in FINDING_FIELDS}
        state["p1"] = p1
    else:
        remaining = result.get("unresolved") if isinstance(result, dict) and set(result) == {"unresolved"} else None
        if not isinstance(remaining, list) or any(not isinstance(key, str) or key not in state["p1"]
                                                 for key in remaining) or len(remaining) != len(set(remaining)):
            raise ValueError('repair result must be {"unresolved": [IDs still open from the incoming P1 list]}')
        state["p1"] = {key: state["p1"][key] for key in remaining}


def record(args, state, run):
    pending = status(state, run)["pending"]
    if not pending or args.attempt != pending["attempt"]:
        raise ValueError("attempt does not match the pending call")
    event = state["events"][-1]
    if args.fork_created and not (args.failed and pending.get("fork_session")):
        raise ValueError("--fork-created applies only to a failed call that requested a fork")
    if args.cost_usd > event["limit"]:
        state["stop"] = "cost_overrun"
    try:
        repair_hash = check_integrity(state, run)
    except (ValueError, OSError) as error:
        state["stop"] = state["stop"] or "integrity_failed"
        event["integrity_error"] = str(error)
    if not state["stop"]:
        if args.result:
            apply_result(state, event, args.result)
        elif event["kind"] == "audit":
            state["stop"] = "audit_failed"
    event.update(cost=args.cost_usd, failed=args.failed,
                 fork_created=bool(pending.get("fork_session") and (args.result or args.fork_created)))
    result = status(state, run)
    if result["result_report"] is not None:
        state["result_sha256"] = repair_hash
    if "integrity_error" in event:
        result["integrity_error"] = event["integrity_error"]
    return result, int(bool(state["stop"]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("prepare")
    init.add_argument("source", type=Path)
    init.add_argument("run", type=Path)
    init.add_argument("--report", required=True)
    init.add_argument("--audit-prompt", required=True, type=Path)
    init.add_argument("--session", required=True, type=UUID)
    for name in ("next", "status"):
        commands.add_parser(name).add_argument("run", type=Path)
    receipt = commands.add_parser("record")
    receipt.add_argument("run", type=Path)
    receipt.add_argument("attempt", type=int)
    receipt.add_argument("--cost-usd", required=True, type=charge)
    outcome = receipt.add_mutually_exclusive_group(required=True)
    outcome.add_argument("--result", type=Path)
    outcome.add_argument("--failed", action="store_true")
    receipt.add_argument("--fork-created", action="store_true")
    args = parser.parse_args()
    run = args.run.resolve()
    try:
        if args.command == "prepare":
            result, code = prepare(args, run)
        else:
            with closing(sqlite3.connect((run / "ledger.sqlite3").as_uri() + "?mode=rw", uri=True)) as db, db:
                db.execute("BEGIN IMMEDIATE")
                state = json.loads(db.execute("SELECT state FROM ledger").fetchone()[0])
                if args.command == "record":
                    result, code = record(args, state, run)
                elif args.command == "next":
                    result, code = reserve(state, run)
                else:
                    if not state["stop"]:
                        check_integrity(state, run)
                    result, code = status(state, run), 0
                if args.command != "status":
                    db.execute("UPDATE ledger SET state = ?", (json.dumps(state),))
    except (OSError, ValueError, sqlite3.Error) as error:
        print(f"Bundle error: {error}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
