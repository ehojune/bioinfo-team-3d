"""Pure durable-ledger doubles; no FastAPI server, HTTP, process, Job or model."""
import ast
import copy
import hashlib
import json
import math
from pathlib import Path
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]


def projection():
    tree = ast.parse((ROOT / "labhq/gateway/server.py").read_text(encoding="utf-8"))
    hub = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Hub")
    node = copy.deepcopy(next(n for n in hub.body if isinstance(n, ast.FunctionDef) and n.name == "request_task_attempts"))
    scope = {"copy": copy, "hashlib": hashlib, "json": json, "math": math}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[])), "real_projection", "exec"), scope)
    return scope["request_task_attempts"]


def row(tid, kind="step", attempt=1):
    return {"request_id": "r", "runner_id": "runner", "runner_incarnation": "generation1",
            "kind": kind, "attempt": attempt, "revision": 0, "parse_attempt": 0,
            "step_id": "same_step", "accepted": True, "completed": True,
            "payload": {"id": tid, "request_id": "r", "agent_id": "worker", "prompt": "SECRET_PROMPT", "argv": ["SECRET_ARGV"]},
            "result": {"task_id": tid, "agent_id": "worker", "ok": True, "text": "SECRET_RESULT",
                       "workdir": "SECRET_WORKDIR", "cleanup_evidence": {"status": "job_close_contract_enforced",
                           "job": "active", "wrapper_pid": 42, "wrapper_created": 1234567,
                           "wait": "completed", "returncode": -1, "active_processes_measured": False,
                           "secret": "SECRET_RECEIPT"}}}


class LedgerHub:
    def __init__(self, rows):
        self.rows = rows
        self.requests = {"r": {"id": "r", "created_at": 123.5, "status": "running", "text": "SECRET_REQUEST"}}
        self.reservations = {}
        self.store = SimpleNamespace(all=lambda kind: copy.deepcopy(
            self.rows if kind == "task" else self.reservations))
        self.s = SimpleNamespace(policy=SimpleNamespace(budget=SimpleNamespace(
            per_task_usd=1, per_request_usd=3, reserve_before_dispatch=True)))


class Projection(unittest.TestCase):
    def test_translation_reservation_projection_and_orphan_enumeration(self):
        h = LedgerHub({"t": row("t", "pi_interpretation")})
        h.rows['t']['payload']['budget_usd'] = 0.05
        h.reservations['t'] = dict(reservation_id='t', task_id='t', request_id='r',
            agent_id='worker', attempt=1, ceiling_usd=0.05, state='reserved', source='pi_translation',
            prompt='SECRET_RESERVATION')
        out = projection()(h, 'r', 123.5)
        self.assertTrue(out['complete'])
        self.assertIn('translation_reservations_v1', out['capabilities'])
        self.assertNotIn('SECRET_RESERVATION', json.dumps(out))
        self.assertEqual(out['accounting']['translation_reservations']['t']['ceiling_usd'], 0.05)
        del h.rows['t']
        out = projection()(h, 'r', 123.5)
        self.assertFalse(out['complete'])
        self.assertEqual(out['missing_attempt_ids'], ['t'])
        h.rows['t'] = row('t', 'pi_interpretation')
        h.rows['t']['payload']['budget_usd'] = 0.1
        self.assertIn('translation_reservation_invalid', projection()(h, 'r', 123.5)['problems'])

    def test_all_kinds_and_retries_not_collapsed(self):
        rows = {str(n): row(str(n), kind, n + 1) for n, kind in enumerate(
            ("briefing", "review", "pi_interpretation", "step", "step"))}
        rows["other"] = {**row("other"), "request_id": "other_request"}
        out = projection()(LedgerHub(rows), "r", 123.5)
        self.assertTrue(out["complete"])
        self.assertEqual(out["total_attempts"], 5)
        self.assertEqual({r["attempt"] for r in out["attempts"]}, {1, 2, 3, 4, 5})
        self.assertNotIn("SECRET", json.dumps(out))
        self.assertFalse(out["attempts"][0]["result"]["cleanup_evidence"]["active_processes_measured"])

    def test_same_snapshot_costs_holds_and_content_revision(self):
        h = LedgerHub({"t": row("t")})
        h.requests["r"].update(cost_usd=0.2, cost_known=True, cost_items={"t": {"status": "actual", "usd": 0.2}},
                               cost_by_task={"t": 0.2}, budget_holds={"t": 1, "next": 1})
        first = projection()(h, "r", 123.5)
        self.assertEqual(first["accounting"]["budget_holds"], {"t": 1, "next": 1})
        self.assertEqual(first["accounting"]["cost_items"]["t"]["usd"], 0.2)
        h.requests["r"]["budget_holds"]["next"] = 0.5
        self.assertNotEqual(first["snapshot_revision"], projection()(h, "r", 123.5)["snapshot_revision"])

    def test_missing_cost_or_receipt_is_null_not_zero(self):
        h = LedgerHub({"t": row("t")});h.rows["t"]["result"].pop("cleanup_evidence")
        out = projection()(h, "r", 123.5)
        self.assertIsNone(out["accounting"]["cost_items"])
        self.assertIsNone(out["accounting"]["budget_holds"])
        self.assertIsNone(out["attempts"][0]["result"]["cleanup_evidence"])
    def test_merged_receipt_model_survives_durable_result_shape(self):
        from labhq.models import TaskResult
        h = LedgerHub({"t": row("t")})
        h.rows["t"]["result"] = TaskResult.model_validate(h.rows["t"]["result"]).model_dump(mode="json")
        out = projection()(h, "r", 123.5)
        receipt = out["attempts"][0]["result"]["cleanup_evidence"]
        self.assertEqual(receipt["status"], "job_close_contract_enforced")
        self.assertEqual(receipt["wrapper_created"], 1234567)
        source = (ROOT / "labhq/gateway/server.py").read_text(encoding="utf-8")
        self.assertIn('result = TaskResult.model_validate(msg["data"])', source)
        self.assertIn('"completed": True, "result": result.model_dump(mode="json")', source)

    def test_payload_id_mismatch_is_unknown(self):
        h = LedgerHub({"t": row("t")})
        h.rows["t"]["payload"]["id"] = "different"
        out = projection()(h, "r", 123.5)
        self.assertFalse(out["complete"])
        self.assertFalse(out["attempts"][0]["identity_verified"])

    def test_exact_request_creation_guard(self):
        with self.assertRaisesRegex(ValueError, "identity changed"):
            projection()(LedgerHub({}), "r", 123.0)
    def test_deleted_or_missing_task_is_unknown_from_cost_hold_and_request_ids(self):
        h = LedgerHub({})
        h.requests["r"].update(cost_items={"lost": {"status": "unknown", "usd": None}},
                               budget_holds={"presend": 1}, cancelled_tasks=["cancelled"],
                               results={"s": {"task_id": "result_without_row"}})
        out = projection()(h, "r", 123.5)
        self.assertFalse(out["complete"])
        self.assertEqual(out["total_attempts"], 4)
        self.assertEqual(out["missing_attempt_count"], 4)
        self.assertTrue(all(r["record_status"] == "MISSING_DURABLE_ROW" for r in out["attempts"]))

    def test_partial_or_missing_identity_never_complete(self):
        h = LedgerHub({"a": row("a"), "b": row("b")})
        self.assertFalse(projection()(h, "r", 123.5, limit=1)["complete"])
        h.rows["a"]["result"]["task_id"] = "different_attempt"
        self.assertFalse(projection()(h, "r", 123.5)["complete"])
        h.rows["a"].pop("result")
        self.assertIn("completed_result_missing", projection()(h, "r", 123.5)["problems"])

    def test_oversized_projection_is_explicitly_incomplete(self):
        h = LedgerHub({str(n): row(str(n)) for n in range(1000)})
        for value in h.rows.values():
            for key in ("runner_id", "runner_incarnation", "step_id", "kind", "parent_task"):
                value[key] = "x" * 128
            value["payload"]["agent_id"] = value["result"]["agent_id"] = "a" * 128
        out = projection()(h, "r", 123.5)
        self.assertFalse(out["complete"])
        self.assertEqual(out["problems"], ["response_size_limit"])
        self.assertEqual(out["attempts"], [])
        self.assertIsNone(out["accounting"])

    def test_projection_is_read_only_synchronous_and_endpoint_authenticated(self):
        source = (ROOT / "labhq/gateway/server.py").read_text(encoding="utf-8")
        self.assertIn('@app.get("/api/requests/{rid}/task-attempts", dependencies=[Depends(auth)])', source)
        h = LedgerHub({"t": row("t")});before = copy.deepcopy(h.requests)
        projection()(h, "r", 123.5)
        self.assertEqual(h.requests, before)


if __name__ == "__main__":
    unittest.main()
