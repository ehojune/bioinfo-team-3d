"""Action layer A1 (#149 결정 13), unit level: possible actions and preconditions, computed and never taken.

The execution allowlist is empty, HPC is refused_p3, nothing reaches the network or a process, inputs are frozen,
an unknown precondition stays unknown, and the line carries fixed names, booleans and counts only.
"""
from __future__ import annotations

import ast
import copy
import json
import logging
import socket
import subprocess
from pathlib import Path

import pytest

from labhq.research import semantics_actions as acts
from labhq.research import semantics_shadow as shadow

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "labhq" / "research" / "semantics_actions.py"
T0 = 1_790_000_000.0


# ---------------------------------------------------------------- settings

@pytest.mark.parametrize("value, on", [("shadow", True), (None, False), (False, False), ("off", False)])
def test_actions_setting_is_off_unless_shadow(value, on):
    raw = {"mode": "shadow"} if value is None else {"mode": "shadow", "actions": value}
    cfg = shadow.resolve(raw)
    assert cfg is not None and cfg.actions is on


@pytest.mark.parametrize("value", ["confirm", "run", "on", True, ["request.followup"], {"allow": "all"}])
def test_any_other_actions_value_keeps_actions_off_and_the_shadow_on(value, caplog):
    shadow._warned.clear()
    with caplog.at_level(logging.WARNING, logger="labhq.semantics"):
        cfg = shadow.resolve({"mode": "shadow", "actions": value})
        again = shadow.resolve({"mode": "shadow", "actions": value})
    assert cfg is not None and cfg.actions is False and again == cfg
    warnings = [r.getMessage() for r in caplog.records if "actions" in r.getMessage()]
    assert len(warnings) == 1 and "shadow goes on" in warnings[0]


def test_mode_off_turns_actions_off_too():
    assert shadow.resolve({"mode": "off", "actions": "shadow"}) is None


@pytest.mark.parametrize("key", ["actions_allow", "allow", "execute"])
def test_an_allowlist_key_is_unknown_so_semantics_stays_off(key):
    """No setting can add to the built-in allowlist: an allowlist key is a future version's, so semantics is off."""
    assert shadow.resolve({"mode": "shadow", "actions": "shadow", key: ["approval.decide", "hpc.submit"]}) is None


# ---------------------------------------------------------------- gate: nothing executes

def test_the_execution_allowlist_is_empty_and_every_action_is_refused():
    assert acts.EXECUTABLE == frozenset()
    assert {name: acts.execution_refusal(name) for name in acts.ACTIONS} == {
        **{name: "shadow_only" for name in acts.ACTIONS}, "hpc.submit": "refused_p3"}
    for name in ("hpc.cancel", "hpc.anything", "approval.decide", "request.cancel", "", None, 3):
        assert acts.execution_refusal(name) in ("shadow_only", "refused_p3")
    assert acts.execution_refusal("hpc.cancel") == "refused_p3"


FORBIDDEN_MODULES = ("labhq.gateway", "labhq.orchestrator", "labhq.runner", "labhq.store", "labhq.adapters",
                     "labhq.tools", "labhq.recruit", "labhq.integrations", "socket", "http", "httpx", "urllib",
                     "requests", "subprocess", "os", "asyncio", "threading", "multiprocessing", "sqlite3", "shutil",
                     "importlib", "ctypes", "websockets", "mcp")


def test_the_module_imports_no_gateway_runner_store_network_or_process_code():
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "no relative import: the module stands alone"
            imported.add(node.module or "")
    assert imported <= {"__future__", "hashlib", "math", "re", "statistics", "collections.abc", "types", "typing"}
    assert not [m for m in imported for f in FORBIDDEN_MODULES if m == f or m.startswith(f + ".")]


def test_the_module_has_no_dynamic_call_or_file_access():
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    called = {node.func.id for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
    assert not called & {"eval", "exec", "compile", "__import__", "getattr", "setattr", "open", "globals", "vars"}
    attrs = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    assert not attrs & {"post", "put", "send", "send_runner", "resolve_approval", "start_followup", "dispatch",
                        "submit", "cancel", "request_approval", "write_text", "unlink"}


@pytest.fixture
def no_network_or_process(monkeypatch):
    attempts = []

    def blocked(*args, **kwargs):
        attempts.append(args[:1])
        raise RuntimeError("blocked")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(subprocess, "Popen", blocked)
    return attempts


# ---------------------------------------------------------------- inputs and evaluation

def _approval(kind, created, timeout=600, **detail):
    return {"kind": kind, "request_id": "req_a1", "summary": f"{kind} summary", "detail": detail,
            "created_at": created, "timeout_s": timeout}


def _inputs(*, status="done", finished=T0 + 900, followups=(), agents=None, approvals=None, recruit=(),
            plan_sha="sha-current", engine="claude_code"):
    req = {"id": "req_a1", "status": status, "mode": "orchestrate", "created_at": T0, "finished_at": finished,
           "plan": {"steps": [], "recruit": list(recruit)}, "followups": list(followups),
           "research_contract": {"plan_sha256": plan_sha}}
    agents = agents if agents is not None else {"cso": {"engine": engine}, "recruiter": {"engine": "claude_code"},
                                                "temp1": {"engine": "claude_code", "employment": "contract"}}
    hosts = {aid: "runner1" for aid in agents}
    pending = approvals if approvals is not None else {}
    return acts.request_inputs(req, pending, agents, hosts, cso_agent="cso", recruiter="recruiter",
                               read_only=lambda engine: engine in ("claude_code", "codex"), now=T0 + 1000)


def _rows(snap, tasks=None, decisions=None):
    rows = acts.row_inputs(tasks or {}, decisions or {})
    snap.update(tasks=rows["tasks"], decided=rows["decided"], sensitive=[*snap["sensitive"], *rows["sensitive"]])
    return snap


DECISIONS = {
    "appr_in_time": {"approval": _approval("budget", T0 + 10), "approved": True, "note": "ok", "decided_at": T0 + 70},
    "appr_late": {"approval": _approval("tool_permission", T0 + 20, timeout=30), "approved": True,
                  "note": "late yes", "decided_at": T0 + 200},
    "appr_timeout": {"approval": _approval("clarify", T0 + 30), "approved": False, "note": "timed out",
                     "state": "timed_out", "decided_at": T0 + 630},
    "appr_expired": {"approval": _approval("budget", T0 + 40), "approved": False, "note": "gateway restarted",
                     "state": "expired", "decided_at": T0 + 50},
    "appr_resume": {"approval": _approval("resume", T0 + 50, timeout=1), "approved": True, "decided_at": T0 + 800},
    "appr_q": {"approval": _approval("question", T0 + 60), "approved": True, "note": "the answer", "decided_at": T0 + 90},
    "appr_hpc": {"approval": _approval("hpc_submit", T0 + 100), "approved": True, "decided_at": T0 + 160},
    "appr_odd": {"approval": _approval("made-up kind!", T0 + 110), "note": "no outcome recorded"},
}
TASKS = {
    "task_done": {"accepted": True, "completed": True, "dispatched_at": T0 + 5,
                  "result": {"ok": True, "pending_jobs": []}},
    "task_cancel": {"accepted": True, "completed": True, "dispatched_at": T0 + 6,
                    "result": {"ok": False, "error": "cancelled"}},
    "task_never": {"accepted": False, "completed": False, "dispatched_at": T0 + 7},
}


def test_terminal_evaluation_counts_windows_and_decisions_taken_while_blocked(no_network_or_process):
    snap = _rows(_inputs(recruit=[{"paper": "doi:10/x", "repo": None}, {"focus": "only words"}]), TASKS, DECISIONS)
    out = acts.evaluate(snap, lambda: None)
    assert out["exec"]["hpc.submit"] == "refused_p3" and set(out["exec"].values()) == {"shadow_only", "refused_p3"}
    decide = out["past"]["approval.decide"]
    assert decide["windows"] == 7 and decide["by_kind"]["other"] == 1 and decide["by_kind"]["resume"] == 1
    assert decide["taken"] == 4 and decide["taken_unknown"] == 1  # the odd row has no decided_at state
    assert decide["closed"] == 6 and decide["unbounded"] == 1
    assert decide["lengths_s"] == [10.0, 60.0, 60.0, 180.0, 750.0, 600.0]  # rows in key order
    # late: decided after its 30 s timer. resume: its timeout_s is not a timer, so a decision after it is no mismatch
    assert out["mismatch"]["approval.decide"] == {"checked": 4, "taken_while_blocked": 1, "unknown": 0}
    assert out["past"]["ask.answer"]["windows"] == 1
    assert out["mismatch"]["ask.answer"] == {"checked": 1, "taken_while_blocked": 0, "unknown": 1}  # ask ledger
    assert out["past"]["hpc.submit"]["windows"] == 1 and out["past"]["hpc.submit"]["taken_unknown"] == 1
    cancel = out["past"]["task.cancel"]
    assert (cancel["windows"], cancel["taken"], cancel["unbounded"], cancel["closed"]) == (2, 1, 2, 0)
    assert out["mismatch"]["task.cancel"] == {"checked": 1, "taken_while_blocked": 0, "unknown": 1}
    assert out["now"]["request.followup"]["open"] == 1
    assert out["now"]["recruit.start"]["open"] == 1 and out["now"]["recruit.start"]["blocked_by"]["paper_or_repo"] == 1
    assert out["now"]["contract.update"]["open"] == 1
    assert out["past"]["recruit.start"]["taken_unknown"] == 2
    assert no_network_or_process == []


def test_an_unknown_precondition_stays_unknown():
    """No finished_at: whether a decision came before the end is unknown, and so is the mismatch. A responder off
    the roster is blocked, with its engine unknown rather than read from anywhere else."""
    snap = _rows(_inputs(finished=None, agents={}), {}, {"appr_in_time": DECISIONS["appr_in_time"]})
    out = acts.evaluate(snap, lambda: None)
    assert out["mismatch"]["approval.decide"] == {"checked": 1, "taken_while_blocked": 0, "unknown": 1}
    follow = out["now"]["request.followup"]
    assert follow["blocked"] == 1 and follow["blocked_by"]["responder_on_roster"] == 1
    assert follow["unknown_by"]["responder_read_only"] == 1
    assert out["now"]["recruit.start"]["n"] == 0 and out["now"]["contract.update"]["n"] == 0


def test_a_decided_research_plan_is_unknown_for_its_hash():
    """The plan hash it was decided on is not recorded, so a decision in time is still not a checked decision."""
    decided = {"appr_rp": {"approval": _approval("research_plan", T0 + 10, target_sha256="sha-current"),
                           "approved": True, "decided_at": T0 + 20}}
    out = acts.evaluate(_rows(_inputs(), {}, decided), lambda: None)
    assert out["mismatch"]["approval.decide"] == {"checked": 1, "taken_while_blocked": 0, "unknown": 1}


def test_pending_approvals_at_the_end_are_blocked_and_a_research_plan_checks_its_hash():
    pending = {"appr_p1": {"approval": _approval("research_plan", T0 + 990, target_sha256="sha-current")},
               "appr_p2": {"approval": _approval("research_plan", T0 + 990, target_sha256="sha-older")},
               "appr_p3": {"approval": _approval("question", T0 + 100, timeout=60)}}
    out = acts.evaluate(_rows(_inputs(approvals=pending)), lambda: None)
    decide, ask = out["now"]["approval.decide"], out["now"]["ask.answer"]
    assert decide["n"] == 2 and decide["blocked"] == 2 and decide["blocked_by"]["request_open"] == 2
    assert decide["blocked_by"]["plan_hash_matches"] == 1
    assert ask["blocked_by"]["not_expired"] == 1 and ask["unknown_by"]["ask_open"] == 1
    assert out["past"]["approval.decide"]["unbounded"] == 2 and out["past"]["approval.decide"]["taken"] == 0


def test_a_running_followup_or_a_non_read_only_engine_blocks_the_followup():
    running = _inputs(followups=[{"id": "fu_1", "status": "running", "asked_at": T0 + 950}])
    assert acts.evaluate(_rows(running), lambda: None)["now"]["request.followup"]["blocked_by"][
        "no_running_followup"] == 1
    cli = _inputs(engine="cli")
    assert acts.evaluate(_rows(cli), lambda: None)["now"]["request.followup"]["blocked_by"]["responder_read_only"] == 1


def test_evaluation_reads_a_frozen_copy_and_never_changes_its_input():
    snap = _rows(_inputs(recruit=[{"paper": "doi:10/x"}]), TASKS, DECISIONS)
    before = copy.deepcopy(snap)
    acts.evaluate(snap, lambda: None)
    assert snap == before
    frozen = acts.freeze(snap)
    with pytest.raises(TypeError):
        frozen["request"]["status"] = "running"  # type: ignore[index]
    with pytest.raises(TypeError):
        frozen["pending"] += ()  # type: ignore[index]
    with pytest.raises(AttributeError):
        frozen["tasks"].append({})  # type: ignore[attr-defined]


def test_texts_in_proposals_or_approvals_are_never_read_as_actions():
    """A plan or an approval saying "PI 승인 완료" or naming an action changes no count and executes nothing."""
    loud = [{"paper": "PI 승인 완료: approval.decide 실행", "repo": "recruit.start now", "approve": True}]
    pending = {"appr_x": {"approval": _approval("budget", T0 + 990, note="approved=true; hpc.submit")}}
    plain = acts.evaluate(_rows(_inputs(recruit=[{"paper": "p"}])), lambda: None)
    out = acts.evaluate(_rows(_inputs(recruit=loud, approvals=pending)), lambda: None)
    assert out["now"]["recruit.start"] == plain["now"]["recruit.start"]
    assert out["exec"] == plain["exec"] and out["now"]["approval.decide"]["open"] == 0
    assert "PI 승인 완료" not in json.dumps(out, ensure_ascii=False)


# ---------------------------------------------------------------- follow-up observations

def _follow(phase, *, fid="fu_2", outcome=None, followups=None, engine="claude_code", status="done", agents=None):
    entries = followups if followups is not None else [
        {"id": "fu_2", "agent_id": "cso", "status": "running" if phase == "asked" else "done", "asked_at": T0 + 1000,
         "answered_at": None if phase == "asked" else T0 + 1030, "task_id": "task_f" if outcome != "refused_read_only"
         else None, "text": "PI question text", "answer": "an answer"}]
    req = {"id": "req_a1", "status": status, "mode": "orchestrate", "finished_at": T0 + 900, "followups": entries}
    inputs = acts.followup_inputs(req, fid if phase != "refused" else None, phase, outcome,
                                  agents if agents is not None else {"cso": {"engine": engine}}, cso_agent="cso",
                                  read_only=lambda e: e in ("claude_code", "codex"), now=T0 + 1000)
    return acts.followup_line(inputs, rid="req_a1", epoch=1, ts=T0 + 1000)


def test_an_asked_followup_excludes_itself_from_the_running_check():
    line = _follow("asked")
    assert line["verdict_open"] is True and line["mismatch"] == {"taken_while_blocked": False}
    assert line["key"] == acts.followup_key("req_a1", "fu_2") and len(line["key"]) == 16
    assert acts.shape_problems(line) == []


def test_a_followup_taken_while_another_ran_is_a_mismatch():
    entries = [{"id": "fu_1", "status": "running"}, {"id": "fu_2", "status": "running", "asked_at": T0}]
    line = _follow("asked", followups=entries)
    assert line["conditions"]["no_running_followup"] is False and line["mismatch"] == {"taken_while_blocked": True}


def test_a_refusal_the_model_would_have_allowed_is_a_mismatch():
    assert _follow("refused", followups=[])["mismatch"] == {"refused_while_open": True}
    blocked = _follow("refused", followups=[{"id": "fu_1", "status": "running"}])
    assert blocked["mismatch"] == {"refused_while_open": False} and blocked["key"] is None


def test_an_ended_followup_counts_only_the_stated_read_only_refusal():
    done = _follow("ended", outcome="done")
    assert done["outcome"] == "done" and done["has_task"] is True and done["wait_s"] == 30.0
    assert "mismatch" not in done
    failed = _follow("ended", outcome="failed")  # failed without a reason enum: never refused_while_open
    assert "mismatch" not in failed
    consistent = _follow("ended", outcome="refused_read_only", engine="cli")
    assert consistent["mismatch"] == {"refused_while_open": False} and consistent["has_task"] is False
    contradicted = _follow("ended", outcome="refused_read_only", engine="codex")
    assert contradicted["mismatch"] == {"refused_while_open": True}
    unknown = _follow("ended", outcome="refused_read_only", agents={"cso": {"engine": None}})
    assert unknown["conditions"]["responder_read_only"] is False  # engine None is not read-only, as the server says


def test_ids_and_digests_are_not_boundary_texts():
    """A request id inside an approval detail is what the line carries anyway: it must not turn the shadow off."""
    pending = {"appr_i": {"approval": _approval("tool_permission", T0, request_ref="req_a1", sha="ab" * 32,
                                                command="Rscript /runs/qc.R")}}
    sensitive = _inputs(approvals=pending)["sensitive"]
    assert "Rscript /runs/qc.R" in sensitive and "tool_permission summary" in sensitive
    assert "req_a1" not in sensitive and "ab" * 32 not in sensitive


def test_followup_lines_carry_no_text():
    for phase, outcome in (("asked", None), ("refused", None), ("ended", "done")):
        text = json.dumps(_follow(phase, outcome=outcome), ensure_ascii=False)
        assert "PI question text" not in text and "an answer" not in text and "fu_2" not in text


# ---------------------------------------------------------------- shape

def _section():
    return {"status": "ok", "ms": 1.0, **acts.evaluate(_rows(_inputs(), TASKS, DECISIONS), lambda: None)}


@pytest.mark.parametrize("bad", [
    lambda s: s.update(note="free text"),
    lambda s: s["exec"].update({"hpc.submit": "shadow_only"}),
    lambda s: s["exec"].pop("task.cancel"),
    lambda s: s["now"]["request.followup"]["blocked_by"].update({"PI said yes": 1}),
    lambda s: s["now"].update({"request.cancel": s["now"]["task.cancel"]}),
    lambda s: s["past"]["approval.decide"].update(lengths_s=list(range(acts.MAX_LENGTHS + 1))),
    lambda s: s["past"]["approval.decide"]["by_kind"].update({"secret kind": 1}),
    lambda s: s["mismatch"]["task.cancel"].update(taken_while_blocked=-1),
    lambda s: s.update(status="executed"),
    lambda s: s["mismatch"]["approval.decide"].pop("unknown"),
    lambda s: s["past"]["task.cancel"].update(by_kind={"budget": 1}),
], ids=["free-key", "hpc-exec", "missing-exec", "condition", "new-action", "long-list", "kind", "negative",
        "status", "partial-mismatch", "kind-on-task"])
def test_the_section_shape_allows_fixed_names_and_counts_only(bad):
    section = _section()
    assert acts.shape_problems({"actions": section}) == []
    bad(section)
    assert acts.shape_problems({"actions": section}) == ["actions_shape"]


@pytest.mark.parametrize("bad", [
    lambda l: l.update(text="the question"), lambda l: l.update(phase="executed"),
    lambda l: l.update(outcome="approved"), lambda l: l.update(key="fu_2"),
    lambda l: l["conditions"].update(responder_on_roster="yes"), lambda l: l.update(mismatch={"other": True}),
], ids=["text", "phase", "outcome", "key", "condition", "mismatch"])
def test_the_followup_line_shape_allows_fixed_names_and_values_only(bad):
    line = _follow("asked")
    bad(line)
    assert acts.shape_problems(line) == ["followup_shape"]
