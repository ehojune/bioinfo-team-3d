"""#780: production control/identity code extracted with inert fakes; no app import, async loop or process."""
import __future__
import ast
import copy
import hashlib
import json
import math
import unittest
from pathlib import Path
from types import SimpleNamespace as NS


ROOT = Path(__file__).resolve().parents[1]


def extracted(path, names, namespace=None, owner=None, synchronous=False):
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"), feature_version=(3, 10))
    body = tree.body if owner is None else next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == owner).body
    nodes = [copy.deepcopy(n) for n in body if getattr(n, "name", None) in names
             or isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id in names for t in n.targets)]
    class Inert(ast.NodeTransformer):
        def visit_ImportFrom(self, node): return None  # only refusal REASON import; injected constant, no product load
        def visit_Await(self, node): return self.visit(node.value)
        def visit_AsyncFunctionDef(self, node):
            return ast.FunctionDef(name=node.name, args=node.args, body=[self.visit(n) for n in node.body],
                                   decorator_list=[], returns=node.returns)
    if synchronous:
        nodes = [Inert().visit(n) for n in nodes]
        for n in nodes: n.body = [x for x in n.body if x is not None]
    for n in nodes:
        if hasattr(n, "decorator_list"): n.decorator_list = []
    scope = {"copy": copy, "hashlib": hashlib, "json": json, "math": math, **(namespace or {})}
    exec(compile(ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[])), path, "exec",
                 flags=__future__.annotations.compiler_flag), scope)
    return scope


def logic():
    names = {"refusal_context", "refusal_retry_pending", "prepare_refusal_retry", "restore_round", "_canonical",
             "_refusal_scope_sha256", "ROUND_CONTRACT_FIELDS", "ROUND_REQUEST_FIELDS"}
    scope = extracted("labhq/research/continuation.py", names)
    return NS(**{n: scope[n] for n in names})


def refused_request(trigger="review"):
    previous = {"round": 1, "plan_sha256": "a" * 64, "plan": {"steps": [{"id": "s1"}]},
                "results": {"s1": {"task_id": "old-step", "ok": True}}, "review": {"reviewer": "review", "issues": []},
                "approval": {"status": "approved"}, "cp2": {"decision": "revise"}, "budget_usd": 3}
    return {"id": "r", "created_at": 100, "text": "Original public research", "status": "failed",
            "outcome": "evidence_revision_requested" if trigger == "cp2_revise" else "research_review_revise",
            "plan": copy.deepcopy(previous["plan"]), "results": copy.deepcopy(previous["results"]),
            "mode": "orchestrate", "work_kind": "research", "cso_model": "old", "budget_usd": 3,
            "cost_usd": 1.25, "cost_by_task": {"old-step": 1, "refused-plan": .25},
            "cso_session_id": "refused-session", "cso_workdir": "old-workdir",
            "research_contract": {"round": 1, "plan_sha256": "a" * 64, "rounds": [previous],
              "continuation": {"round": 2, "from_plan_sha256": "a" * 64, "approval_id": "continue-card",
                "trigger": trigger, "pi_request": "Keep the approved scope",
                "ended_before_dispatch": {"outcome": "model_refused", "reason": "safe refusal",
                    "task_results": {"plan": {"task_id": "refused-plan", "error_kind": "model_refused"}}}}}}


class Card(NS):
    def __init__(self, **kw): super().__init__(id="refusal-card", **kw)
    def model_dump(self, **kw): return vars(self)


class Store:
    def __init__(self): self.tasks = {}
    def all(self, kind): assert kind == "task"; return self.tasks


def hub(req=None):
    h = NS(requests={"r": req or refused_request()}, store=Store(), approvals={}, events=[], saved=[], created=[],
           s=NS(orchestrator=NS(cso_models=["old", "new"], cso_agent="cso")), agents={"cso": {"model": "old"}})
    scope = extracted("labhq/gateway/server.py", {"_round_dispatched", "_continuation_refusal_context", "_model_refusal_request", "offer_model_refusal"},
                      {"research_continuation": logic(), "REASON": "safe refusal", "ApprovalRequest": Card,
                       "RequestIn": lambda **kw: NS(**kw)}, owner="Hub", synchronous=True)
    for name in ("_round_dispatched", "_continuation_refusal_context", "_model_refusal_request", "offer_model_refusal"):
        setattr(h, name, scope[name].__get__(h))
    h.save_request = lambda rid: h.saved.append(rid)
    h.save_approval = lambda aid: h.saved.append(aid)
    h.publish = lambda event: h.events.append(event)
    def create(body, extra): h.created.append((body, extra)); return "new-request"
    h.create_request = create
    return h


class ContinuationRefusal(unittest.TestCase):
    def test_end_preserves_explicit_cause_and_restores_both_previous_round_outcomes(self):
        c = logic()
        scope = extracted("labhq/orchestrator/cso.py", {"_continuation_unstarted", "_unstarted_continuation_end"},
                          {"research_continuation": c, "_research_issue_lines": lambda rows: []}, owner="Orchestrator")
        scope["Orchestrator"] = NS(_continuation_unstarted=scope["_continuation_unstarted"])
        for trigger in ("review", "cp2_revise"):
            req = refused_request(trigger)
            req["status"], req["outcome"] = "running", "model_refused"
            req["research_contract"]["round"] = 2
            report, results, review = scope["_unstarted_continuation_end"](
                req, "safe refusal", {"plan": {"task_id": "refused-plan", "error_kind": "model_refused"}}, None)
            req.update(status="failed", results=results)
            self.assertEqual(req["outcome"], "evidence_revision_requested" if trigger == "cp2_revise" else "research_review_revise")
            self.assertEqual(req["results"]["s1"]["task_id"], "old-step")
            self.assertEqual(c.refusal_context(req)["refusal_task_id"], "refused-plan")

    def test_card_exposes_same_continuation_but_never_dispatches(self):
        h = hub()
        h.offer_model_refusal("r"); h.offer_model_refusal("r")
        self.assertEqual(len(h.approvals), 1)
        card = h.approvals["refusal-card"]["approval"]
        self.assertEqual(card["detail"]["continuation"]["round"], 2)
        self.assertIn("같은 이어 가기", card["pi_outcome"])
        self.assertEqual(h.created, [])

    def test_other_model_keeps_request_round_results_budget_and_costs(self):
        h = hub(); h.offer_model_refusal("r")
        req = h.requests["r"]; before = copy.deepcopy(req)
        identity = h.approvals["refusal-card"]["approval"]["detail"]["continuation"]
        decision = {"action": "model", "cso_model": "new"}
        self.assertEqual(h._model_refusal_request("r", decision, approval_id="refusal-card", continuation=identity), "r")
        self.assertEqual(h.created, [])
        for field in ("plan", "results", "budget_usd", "cost_usd", "cost_by_task", "text"):
            self.assertEqual(req[field], before[field])
        self.assertEqual(req["research_contract"]["rounds"], before["research_contract"]["rounds"])
        self.assertEqual(req["research_contract"]["round"], 2)
        self.assertEqual(req["research_contract"]["continuation"]["refusal_history"][0]["outcome"], "model_refused")
        self.assertNotIn("cso_session_id", req)
        self.assertTrue(logic().refusal_retry_pending(req))
        self.assertEqual(h._model_refusal_request("r", decision, approval_id="refusal-card", continuation=identity), "r")

    def test_edit_is_explicitly_a_new_request_and_does_not_resume_round(self):
        h = hub(); h.offer_model_refusal("r")
        identity = h.approvals["refusal-card"]["approval"]["detail"]["continuation"]
        before = copy.deepcopy(h.requests["r"])
        rid = h._model_refusal_request("r", {"action": "edit", "text": "Changed public question"},
                                       approval_id="refusal-card", continuation=identity)
        self.assertEqual(rid, "new-request")
        self.assertEqual(h.requests["r"], before)
        self.assertEqual(h.created[0][0].text, "Changed public question")
        self.assertEqual(h.created[0][1], {"model_refusal_from": "r"})

    def test_stale_card_request_round_snapshot_and_task_identity_refuse(self):
        for change in ("created", "round", "archive", "task", "plan", "cancelled", "dispatched", "card", "note", "model"):
            h = hub(); h.offer_model_refusal("r")
            req = h.requests["r"]; identity = copy.deepcopy(h.approvals["refusal-card"]["approval"]["detail"]["continuation"])
            contract = req["research_contract"]; carried = contract["continuation"]
            if change == "created": req["created_at"] += 1
            if change == "round": carried["round"] += 1
            if change == "archive": contract["rounds"][-1]["results"]["s1"]["ok"] = False
            if change == "task": carried["ended_before_dispatch"]["task_results"]["plan"]["task_id"] = "other"
            if change == "plan": req["plan"] = {"steps": []}
            if change == "cancelled": req["status"] = "cancelled"
            if change == "dispatched": h.store.tasks["t"] = {"request_id": "r", "step_id": "s1", "payload": {"meta": {"research_round": "2"}}}
            if change == "card": req["model_refusal_approval_id"] = "other-card"
            if change == "note": carried["pi_request"] = "Changed scope"
            if change == "model": req["cso_model"] = "new"
            with self.subTest(change=change), self.assertRaises(ValueError):
                h._model_refusal_request("r", {"action": "model", "cso_model": "new"},
                                         approval_id="refusal-card", continuation=identity)
            self.assertEqual(h.created, [])

    def test_no_model_or_implicit_same_model_choice_is_accepted(self):
        for decision in (None, {}, {"action": "model", "cso_model": "old"},
                         {"action": "model", "cso_model": "unlisted"}, {"action": "resume"}):
            h = hub(); h.offer_model_refusal("r")
            identity = h.approvals["refusal-card"]["approval"]["detail"]["continuation"]
            with self.assertRaises(ValueError):
                h._model_refusal_request("r", decision, approval_id="refusal-card", continuation=identity)

    def test_recovery_revalidation_fails_on_drift_and_cancelled_replay(self):
        for field in ("created_at", "snapshot", "model", "cancelled"):
            h = hub(); h.offer_model_refusal("r")
            identity = h.approvals["refusal-card"]["approval"]["detail"]["continuation"]
            decision = {"action": "model", "cso_model": "new"}
            h._model_refusal_request("r", decision, approval_id="refusal-card", continuation=identity)
            req = h.requests["r"]
            if field == "created_at": req["created_at"] += 1
            if field == "snapshot": req["research_contract"]["rounds"][-1]["results"] = {}
            if field == "model": req["cso_model"] = "old"
            if field == "cancelled": req["status"] = "cancelled"
            with self.assertRaises(ValueError):
                h._model_refusal_request("r", decision, approval_id="refusal-card", continuation=identity)

    def test_initial_refusal_keeps_existing_new_request_behavior(self):
        req = refused_request(); req.pop("research_contract"); req["outcome"] = "model_refused"
        h = hub(req); h.offer_model_refusal("r")
        self.assertNotIn("continuation", h.approvals["refusal-card"]["approval"]["detail"])
        self.assertEqual(h._model_refusal_request("r", {"action": "model", "cso_model": "new"}), "new-request")

    def test_non_refusal_terminal_states_never_offer_a_card(self):
        for status, outcome in (("cancelled", "model_refused"), ("failed", "budget_denied"), ("done", "model_refused")):
            req = refused_request(); req["status"] = status
            req["research_contract"]["continuation"]["ended_before_dispatch"]["outcome"] = outcome
            h = hub(req); h.offer_model_refusal("r")
            self.assertEqual(h.approvals, {})

    def test_same_request_resume_uses_fresh_planning_and_durable_cost_seed(self):
        source = (ROOT / "labhq/orchestrator/cso.py").read_text(encoding="utf-8")
        self.assertIn('if not continuation or resume:', source)
        self.assertIn('if resume and not continuation and req.get("plan", {}).get("steps"):', source)
        gateway = (ROOT / "labhq/gateway/server.py").read_text(encoding="utf-8")
        self.assertIn('**({"continuation": True} if continuation_retry else {})', gateway)
        self.assertIn('{"continued_request_id": retry_id}', gateway)
        self.assertIn('self.track_request(retry_id, self.resume_when_ready(retry_id))', gateway)

    def test_ui_retains_explicit_new_request_edit_and_continuation_model_label(self):
        source = (ROOT / "labhq/web/ui/decide.js").read_text(encoding="utf-8")
        self.assertIn("다른 CSO 모델로 이어 가기 다시 계획", source)
        self.assertIn("['edit', '문구를 바꿔 새 요청']", source)
        self.assertIn("같은 요청의 이어 가기를 새 계획·CP1부터 재개", source)

    def test_recovery_does_not_readopt_refused_plan_but_restart_adopts_same_choice(self):
        h = hub(); h.offer_model_refusal("r")
        identity = h.approvals["refusal-card"]["approval"]["detail"]["continuation"]
        h._model_refusal_request("r", {"action": "model", "cso_model": "new"},
                                approval_id="refusal-card", continuation=identity)
        fn = extracted("labhq/orchestrator/cso.py", {"_with_research_round"}, owner="Orchestrator")["_with_research_round"]
        matches = extracted("labhq/gateway/server.py", {"_matches_recovery"}, owner="Hub")["_matches_recovery"]
        class Task(NS):
            def model_copy(self, *, update): return Task(**{**vars(self), **update})
        base = Task(request_id="r", meta={"kind": "plan"})
        task = fn(NS(hub=h), base)
        old = {"request_id": "r", "kind": "plan", "payload": {"meta": {"research_round": 2}}}
        self.assertFalse(matches(task, old))
        old["payload"]["meta"] = dict(task.meta)
        self.assertTrue(matches(task, old))
        restarted = fn(NS(hub=h), base)
        self.assertTrue(matches(restarted, old))
        h.requests["r"]["research_contract"]["continuation"]["refusal_retry"]["approval_id"] = "later-choice"
        self.assertFalse(matches(fn(NS(hub=h), base), old))
