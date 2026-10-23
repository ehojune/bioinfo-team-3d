"""Counts-only, opt-in benchmark receipts. No commands, prompts, paths or request bodies are persisted here."""
from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from pathlib import Path

from .bench_permissions import inside
from .util import atomic_write_text


STAGES = ("planning", "skill", "analysis", "consult", "review", "report", "other")
KINDS = {"planning": {"briefing", "precedent", "plan", "replan", "scope"},
         "skill": {"paper_skill", "recruit", "recruit_check", "plan_recruit"},
         "analysis": {"step", "direct", "wake", "result_correction", "slot_repair"},
         "consult": {"consult"}, "review": {"review", "scientific_review", "sci_review", "blind_draft"},
         "report": {"synthesis", "report", "storage_editor", "result_editor"}}


def measure(value=None, *, reason="missing_evidence", applicable=True):
    if not applicable and reason == "missing_evidence":
        reason = "single_engine_arm"
    return {"status": "not_applicable" if not applicable else "not_collected" if value is None else "collected",
            "value": value if applicable else None, **({"reason": reason} if value is None or not applicable else {})}


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0


def plan_shape(steps: list[dict]) -> dict:
    """The #815 example contract, now shared by the executable bench recorder."""
    by_id = {step["id"]: step for step in steps}
    children = defaultdict(list)
    for step in steps:
        for parent in step.get("depends_on") or []:
            if parent in by_id:
                children[parent].append(step["id"])
    shape = dict.fromkeys(("steps", "handoffs", "boundary_handoffs", "handoff_outputs",
                           "coupled_splits", "pipeline_splits"), 0)
    shape["steps"] = len(steps)
    for step in steps:
        parents = [p for p in step.get("depends_on") or [] if p in by_id]
        for pid in parents:
            parent = by_id[pid]
            linear = parents == [pid] and children[pid] == [step["id"]]
            if parent["agent_id"] != step["agent_id"]:
                shape["handoffs"] += 1
                shape["handoff_outputs"] += len(parent.get("outputs") or [])
                if step["agent_id"] in {"qc_reviewer", "biologist"} or step.get("phase") in {"validation", "report"}:
                    shape["boundary_handoffs"] += 1
                    continue
            if linear:
                if step["agent_id"] == "bioinfo-agent" and parent["agent_id"] in {"analyst", "engineer", "bioinfo-agent"}:
                    shape["pipeline_splits"] += 1
                elif step["agent_id"] in {"analyst", "engineer"} and parent["agent_id"] in {"analyst", "engineer"}:
                    shape["coupled_splits"] += 1
    return shape


class Recorder:
    """One arm, one request; only bench.py installs its callbacks on its own runner."""
    def __init__(self, arm: str, request_id: str | None = None):
        self.arm, self.request_id = arm, request_id
        self.spawns: dict[tuple, dict] = {}
        self.ends: dict[str, float] = {}

    def spawn(self, *, task_id, request_id, spawn_id, state, prompt_bytes=None, argv_bytes=0, stdin_bytes=0):
        if request_id != self.request_id or state not in {"attempt", "started", "failed"}:
            return
        if not all(isinstance(n, int) and not isinstance(n, bool) and n >= 0
                   for n in (argv_bytes, stdin_bytes)) or (prompt_bytes is not None and
                   (not isinstance(prompt_bytes, int) or isinstance(prompt_bytes, bool) or prompt_bytes < 0)):
            return
        key = (task_id, spawn_id)
        row = self.spawns.setdefault(key, {})
        if state == "attempt":
            row.setdefault("bytes", (prompt_bytes, argv_bytes, stdin_bytes))
        elif row.get("state") is None:
            row["state"] = state

    def event(self, event):
        if event.get("request_id") != self.request_id or event.get("type") != "task.result":
            return
        tid, ts = event.get("task_id"), event.get("ts")
        if tid and _number(ts):
            self.ends[tid] = min(ts, self.ends.get(tid, ts))  # a replay cannot extend the elapsed time

    def cli(self, task_ids=None, *, applicable=True, coverage_known=True):
        rows = [row for (tid, _), row in self.spawns.items() if task_ids is None or tid in task_ids]
        covered = {tid for (tid, _), row in self.spawns.items() if row.get("state") in {"started", "failed"}}
        complete = coverage_known and bool(rows) and all("bytes" in row and row.get("state") for row in rows)
        complete = complete and (task_ids is None or set(task_ids) <= covered)
        started = [row for row in rows if row.get("state") == "started" and "bytes" in row]
        values = {"spawn_successes": len(started), "spawn_failures": sum(r.get("state") == "failed" for r in rows),
                  "prepared_prompt_utf8_bytes_proxy": (sum(r["bytes"][0] for r in started)
                                                       if all(r["bytes"][0] is not None for r in started) else None),
                  "argv_utf8_bytes": sum(r["bytes"][1] for r in started),
                  "stdin_bytes": sum(r["bytes"][2] for r in started)}
        return {key: {**measure(value if complete else None, applicable=applicable,
                               reason="mock_mode" if not applicable else "prompt_component_not_collected"
                               if complete and value is None else "incomplete_spawn_coverage"),
                      **({"observed": value} if applicable and not complete else {})}
                for key, value in values.items()}


def _approved_plans(approvals, rid):
    snapshots, seen = [], set()
    for entry in sorted(approvals.values(), key=lambda e: (
            e.get("decided_at") if _number(e.get("decided_at")) else 0, str((e.get("approval") or {}).get("id", "")))):
        card = entry.get("approval") or {}
        detail = card.get("detail") or {}
        if card.get("request_id") != rid or entry.get("approved") is not True:
            continue
        canonical = detail.get("plan_canonical")
        if not isinstance(canonical, str) or hashlib.sha256(canonical.encode()).hexdigest() != detail.get("target_sha256"):
            continue
        try:
            plan = json.loads(canonical)
            shape = plan_shape(plan["steps"])
        except (ValueError, TypeError, KeyError):
            continue
        continuation = detail.get("continuation")
        phase = "continuation" if continuation else "initial" if not snapshots else "replan"
        key = (detail["target_sha256"], (continuation or {}).get("round"))
        if key in seen:
            continue
        seen.add(key)
        rerun = (continuation or {}).get("rerun")
        valid_rerun = (isinstance(rerun, list) and all(isinstance(s, str) for s in rerun)
                       and len(set(rerun)) == len(rerun) and set(rerun) <= {s["id"] for s in plan["steps"]})
        expected = len(rerun) if valid_rerun else shape["steps"] if phase == "initial" else None
        snapshots.append({"phase": phase, "shape": shape, "expected_dispatches": measure(expected,
                          reason="rerun_set_not_recorded"), "plan_sha256": detail["target_sha256"]})
    return snapshots


def collect(recorder: Recorder, *, mode="real", tasks=None, approvals=None):
    """Aggregate an existing, request-filtered ledger; task counts are dispatch proxies, never spawn proof."""
    labhq = recorder.arm == "labhq"
    rows = {tid: row for tid, row in sorted((tasks or {}).items()) if row.get("request_id") == recorder.request_id}
    observed = labhq and tasks is not None
    stages = {}
    for stage in STAGES:
        selected = [row for row in rows.values()
                    if next((k for k, kinds in KINDS.items() if row.get("kind") in kinds), "other") == stage]
        elapsed, missing_time, known_usd, unknown_cost = 0.0, 0, 0.0, 0
        for tid, row in rows.items():
            if row not in selected:
                continue
            begin, end = row.get("dispatched_at"), recorder.ends.get(tid)
            if _number(begin) and _number(end) and end >= begin:
                elapsed += end - begin
            else:
                missing_time += 1
            result = row.get("result") or {}
            if result.get("cost_known") is True and _number(result.get("cost_usd")):
                known_usd += result["cost_usd"]
            else:
                unknown_cost += 1
        stages[stage] = {"recorded_tasks": measure(len(selected) if observed else None, applicable=labhq),
                         "elapsed_task_s": {**measure(round(elapsed, 6) if observed and not missing_time else None,
                                                       applicable=labhq),
                                            **({"observed": round(elapsed, 6)} if observed and missing_time else {})},
                         "cost_usd": measure(round(known_usd, 10) if observed and not unknown_cost else None,
                                             applicable=labhq),
                         "known_cost_usd": round(known_usd, 10) if observed else None,
                         "unknown_cost_tasks": unknown_cost if observed else None}
    waits, missing_waits, seen_waits = [], 0, set()
    for _, entry in sorted((approvals or {}).items()):
        card = entry.get("approval") or {}
        if card.get("request_id") != recorder.request_id:
            continue
        if not card.get("id"):
            missing_waits += 1
            continue
        if card["id"] in seen_waits:
            continue
        seen_waits.add(card["id"])
        start, end = card.get("created_at"), entry.get("decided_at")
        if _number(start) and _number(end) and end >= start:
            waits.append(end - start)
        else:
            missing_waits += 1
    plans = _approved_plans(approvals or {}, recorder.request_id) if labhq else []
    def proxy_bytes(key):
        if not observed or any(not isinstance((r.get("payload") or {}).get(key), str) for r in rows.values()):
            return None
        return sum(len(r["payload"][key].encode("utf-8")) for r in rows.values())
    attempts_known = observed and all(isinstance(r.get("attempt"), int) and not isinstance(r["attempt"], bool)
                                     and r["attempt"] >= 1 for r in rows.values())
    parents_known = observed and all("parent_task" in r for r in rows.values())
    return {"schema_version": 1, "arm": recorder.arm,
            "approved_plans": {"status": "collected" if plans else "not_collected" if labhq else "not_applicable",
                               "items": plans},
            "recorded_tasks": measure(len(rows) if observed else None, applicable=labhq),
            "retry_tasks": measure(sum(r["attempt"] > 1 for r in rows.values()) if attempts_known else None,
                                   applicable=labhq),
            "continuation_turns": measure(sum(bool(r.get("parent_task")) for r in rows.values()) if parents_known else None,
                                          applicable=labhq),
            "task_prompt_utf8_bytes_proxy": measure(proxy_bytes("prompt"), applicable=labhq),
            "handoff_context_utf8_bytes_proxy": measure(proxy_bytes("context"), applicable=labhq),
            "cli": recorder.cli(set(rows) if observed else None, applicable=mode == "real",
                                coverage_known=observed if labhq else True),
            "stages": stages,
            "approval_wait_s": {**measure(round(sum(waits), 6) if approvals is not None and not missing_waits else None,
                                          applicable=labhq),
                                **({"observed": round(sum(waits), 6)} if labhq and missing_waits else {})},
            "limits": ["Task ledger covers hosted dispatches, not successful CLI starts.",
                       "Only hash-bound approved canonical plans have plan metrics; missing snapshots stay uncollected.",
                       "Stage seconds sum task elapsed time, can overlap, and include task-internal waits.",
                       "CLI spawn means the launch process started, not authentication or a successful inner tool.",
                       "Argument bytes are UTF-8 representations including NUL separators, not Windows wire bytes.",
                       "Prompt pointers, environment, files and tool/SDK internal invocations are not full context measurements.",
                       "A direct baseline's prompt component is not separately measured; its full argv bytes are observed."]}


def render(receipt):
    if not isinstance(receipt, dict) or receipt.get("schema_version") != 1:
        return "not_collected (no saved bench receipt)"
    def show(metric):
        return (str(metric["value"]) if metric["status"] == "collected" else metric["status"] +
                (f" (observed {metric['observed']})" if "observed" in metric else ""))
    def cost(row):
        return show(row["cost_usd"]) + (f" (known {row['known_cost_usd']}, unknown tasks {row['unknown_cost_tasks']})"
                                       if row["unknown_cost_tasks"] else "")
    lines = ["| metric | value |", "|---|---:|", f"| recorded tasks | {show(receipt['recorded_tasks'])} |"]
    lines += [f"| {key} | {show(receipt[key])} |" for key in
              ("retry_tasks", "continuation_turns", "task_prompt_utf8_bytes_proxy",
               "handoff_context_utf8_bytes_proxy", "approval_wait_s")]
    lines += [f"| CLI {key} | {show(value)} |" for key, value in receipt["cli"].items()]
    plans = receipt["approved_plans"]
    lines += ["", "Approved plans: " + plans["status"], "",
              "| phase | steps | handoffs | boundary | outputs | coupled/pipeline splits | expected dispatch |",
              "|---|---:|---:|---:|---:|---|---:|"]
    for plan in plans["items"]:
        s = plan["shape"]
        lines.append(f"| {plan['phase']} | {s['steps']} | {s['handoffs']} | {s['boundary_handoffs']} | "
                     f"{s['handoff_outputs']} | {s['coupled_splits']}/{s['pipeline_splits']} | "
                     f"{show(plan['expected_dispatches'])} |")
    lines += ["", "| stage | tasks | elapsed task s | known/unknown cost USD |", "|---|---:|---:|---|"]
    lines += [f"| {name} | {show(row['recorded_tasks'])} | {show(row['elapsed_task_s'])} | {cost(row)} |"
              for name, row in receipt["stages"].items()]
    lines += ["", *[f"- {note}" for note in receipt["limits"]]]
    return "\n".join(lines)


def save(arm_dir: Path, receipt):
    """Write only in the caller's isolated arm directory. Rescore never calls this."""
    names = ("bench_metrics.json", "bench_metrics.md")
    if not all(inside(arm_dir, name) for name in names):
        raise ValueError("benchmark metric file escapes its arm")
    atomic_write_text(arm_dir / names[0], json.dumps(receipt, sort_keys=True, ensure_ascii=False, indent=2) + "\n")
    atomic_write_text(arm_dir / names[1], render(receipt) + "\n")
