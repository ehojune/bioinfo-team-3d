"""Pure fake-state tests of the real cancellation method; no server/process/network."""
import ast
import copy
import math
from pathlib import Path
from types import SimpleNamespace
import unittest

from labhq.request_status import is_active_request, is_terminal_request

ROOT = Path(__file__).resolve().parents[1]


def method():
    tree = ast.parse((ROOT / "labhq/gateway/server.py").read_text(encoding="utf-8"))
    hub = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Hub")
    node = copy.deepcopy(next(n for n in hub.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "cancel_request"))
    node.decorator_list = []
    scope = {"is_active_request": is_active_request, "is_terminal_request": is_terminal_request}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[])), "real_cancel_method", "exec"), scope)
    return scope["cancel_request"]


class BeforeTransition(Exception):
    pass


class FakeHub:
    def __init__(self, status):
        self.requests = {"r": {"id": "r", "created_at": 123.5, "status": status}}
        self.decisions = []

    def _final_review(self, rid, decision):
        self.decisions.append(decision)
        self.requests[rid]["status"] = decision
        return {"request_id": rid, "status": decision}

    def _restore_unstarted_continuation(self, rid, req):
        # Boundary immediately following the atomic guard, before the original transition.
        raise BeforeTransition()


class ActiveCancel(unittest.IsolatedAsyncioTestCase):
    async def test_legacy_reproduces_CP4_mutation_but_active_only_preserves_it(self):
        old = FakeHub("review_pending")
        await method()(old, "r")
        self.assertEqual(old.decisions, ["cancelled"])
        new = FakeHub("review_pending")
        out = await method()(new, "r", active_only=True, expected_created_at=123.5)
        self.assertTrue(out["skipped"])
        self.assertEqual(new.decisions, [])
        self.assertEqual(new.requests["r"]["status"], "review_pending")

    async def test_all_terminal_states_preserved(self):
        for status in ("done", "failed", "cancelled", "rejected", "review_pending"):
            hub = FakeHub(status)
            out = await method()(hub, "r", active_only=True, expected_created_at=123.5)
            self.assertTrue(out["skipped"])
            self.assertEqual(hub.requests["r"]["status"], status)

    async def test_poll_to_cancel_terminal_race_uses_current_server_state(self):
        hub = FakeHub("running")  # client's earlier GET
        hub.requests["r"]["status"] = "review_pending"  # before cancel is handled
        out = await method()(hub, "r", active_only=True, expected_created_at=123.5)
        self.assertTrue(out["skipped"])
        self.assertEqual(hub.decisions, [])

    async def test_creation_identity_mismatch_refuses_before_transition(self):
        for status in ("running", "review_pending"):
            hub = FakeHub(status)
            with self.assertRaisesRegex(ValueError, "identity changed"):
                await method()(hub, "r", active_only=True, expected_created_at=123.0)
            self.assertEqual(hub.decisions, [])

    async def test_active_state_reaches_original_transition(self):
        with self.assertRaises(BeforeTransition):
            await method()(FakeHub("running"), "r", active_only=True, expected_created_at=123.5)

    def test_distinct_route_and_no_await_before_guarded_transition(self):
        tree = ast.parse((ROOT / "labhq/gateway/server.py").read_text(encoding="utf-8"))
        source = (ROOT / "labhq/gateway/server.py").read_text(encoding="utf-8")
        self.assertIn('@app.post("/api/requests/{rid}/cancel-active", dependencies=[Depends(auth)])', source)
        hub = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Hub")
        fn = next(n for n in hub.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "cancel_request")
        transition = next(n for n in fn.body if isinstance(n, ast.Assign) and any(isinstance(x, ast.Name) and x.id == "now" for x in n.targets))
        self.assertFalse(any(isinstance(n, ast.Await) and n.lineno < transition.lineno for n in ast.walk(fn)))

    def test_cli_uses_distinct_route_and_never_falls_back_on_old_gateway(self):
        tree = ast.parse((ROOT / "labhq/cli.py").read_text(encoding="utf-8"))
        branch = next(n for n in ast.walk(tree) if isinstance(n, ast.If)
                      and ast.unparse(n.test) == "args.cmd == 'cancel'")
        node = ast.parse("def command():\n    pass").body[0]
        node.body = copy.deepcopy(branch.body)
        calls = []

        def api(settings, method, path, **kwargs):
            calls.append((path, kwargs))
            raise RuntimeError("old gateway 404")

        scope = {"args": SimpleNamespace(active_only=True, expected_created_at=123.5, request_id="r"),
                 "s": object(), "math": math, "_api": api}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[])), "real_cli_branch", "exec"), scope)
        with self.assertRaisesRegex(RuntimeError, "404"):
            scope["command"]()
        self.assertEqual(calls, [("/api/requests/r/cancel-active", {"json": {"expected_created_at": 123.5}})])


if __name__ == "__main__":
    unittest.main()
