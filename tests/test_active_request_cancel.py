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

    async def _afinal_review(self, rid, decision):
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
        legacy_review = next(n for n in fn.body if isinstance(n, ast.If)
                             and ast.unparse(n.test) == "not active_only and status == 'review_pending'")
        legacy_awaits = {id(n) for n in ast.walk(legacy_review) if isinstance(n, ast.Await)}
        self.assertEqual(len(legacy_awaits), 1)
        self.assertFalse(any(isinstance(n, ast.Await) and n.lineno < transition.lineno
                             and id(n) not in legacy_awaits for n in ast.walk(fn)))

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


# ---- #877: a wall-clock limit for the whole request, enforced by the gateway ----
class RequestDeadline(unittest.IsolatedAsyncioTestCase):
    CREATED = 1000.0

    def setUp(self):
        from unittest import mock

        self.now = [self.CREATED]
        patcher = mock.patch("labhq.gateway.server.time.time", side_effect=lambda: self.now[0])
        patcher.start()
        self.addCleanup(patcher.stop)

    def at(self, seconds):
        self.now[0] = self.CREATED + seconds
        return self.now[0]

    def hub(self, limit=120, state=None):
        import tempfile

        from labhq.gateway.server import Hub
        from labhq.settings import Settings

        settings = Settings()
        settings.gateway.state_dir = state or tempfile.mkdtemp()
        settings.orchestrator.request_wall_clock_s = limit
        hub = Hub(settings)

        async def publish(event, **kwargs):
            pass

        hub.publish = publish
        return hub

    def open_request(self, hub, status="running", **extra):
        hub.requests["r"] = {"id": "r", "status": status, "created_at": self.CREATED, "text": "x", "mode": "orchestrate",
                             "wall_clock": hub._new_wall_clock(self.CREATED), **extra}
        hub.save_request("r")
        return hub.requests["r"]

    def wait_for_pi(self, hub, req, seconds):
        self.at(seconds)
        req["pi_waits"] = {"card": {"kind": "approval", "since": self.now[0]}}
        hub._sync_hold_status(req)
        self.assertEqual(req["status"], "waiting_pi")

    def answered(self, hub, req, seconds):
        self.at(seconds)
        req.pop("pi_waits")
        hub._sync_hold_status(req)
        self.assertEqual(req["status"], "running")

    async def test_a_request_past_its_limit_is_cancelled_with_a_receipt_and_its_own_label(self):
        from labhq.request_status import request_display_label

        hub = self.hub(120)
        req = self.open_request(hub)
        self.assertEqual(req["wall_clock"], {"enabled": True, "limit_s": 120, "held_s": 0.0,
                                             "deadline_at": self.CREATED + 120})
        self.assertEqual(await hub.enforce_request_deadlines(now=self.at(119)), [])
        self.assertEqual(await hub.enforce_request_deadlines(now=self.at(121)), ["r"])
        self.assertEqual((req["status"], req["cancel_reason"]), ("cancelled", "request_deadline"))
        self.assertIn("시간 상한", req["error"])
        receipt = req["deadline_receipt"]
        self.assertEqual((receipt["limit_s"], receipt["termination"], receipt["cancelled_tasks"]),
                         (120, "no_running_tasks", []))
        self.assertEqual(request_display_label(req), "시간 상한으로 취소됨")
        self.assertEqual(request_display_label({"status": "cancelled"}), "취소됨")

    async def test_the_report_of_a_deadline_cancel_does_not_say_the_pi_cancelled(self):
        from labhq.gateway.server import Hub

        by_pi, _ = Hub._cancel_report({"cancelled_at": self.CREATED}, [], {})
        by_limit, _ = Hub._cancel_report({"cancelled_at": self.CREATED, "cancel_reason": "request_deadline",
                                          "wall_clock": {"limit_s": 5400}}, [], {})
        self.assertIn("PI가", by_pi)
        self.assertIn("요청 전체 시간 상한(90분)에 닿아 labhq가", by_limit)
        self.assertNotIn("PI가", by_limit)

    async def test_the_limit_is_read_from_the_request_not_from_the_config_that_is_now_set(self):
        hub = self.hub(120)
        self.open_request(hub)
        hub.s.orchestrator.request_wall_clock_s = 0
        self.assertEqual(await hub.enforce_request_deadlines(now=self.at(130)), ["r"])
        off = self.hub(0)
        self.assertEqual(off._new_wall_clock(5.0), {"enabled": False, "limit_s": 0, "held_s": 0.0, "deadline_at": None})
        self.open_request(off)
        self.assertEqual(await off.enforce_request_deadlines(now=self.at(10 ** 6)), [])

    async def test_time_waiting_for_the_pi_moves_the_deadline_from_the_real_transitions(self):
        hub = self.hub(120)
        req = self.open_request(hub)
        self.wait_for_pi(hub, req, 11)
        # Still waiting: the open interval keeps the deadline ahead of the clock however long it lasts.
        self.assertEqual(await hub.enforce_request_deadlines(now=self.at(150)), [])
        self.answered(hub, req, 101)  # 90 s waiting, entered between two looks of the watch
        self.assertEqual(req["wall_clock"]["held_s"], 90.0)
        self.assertEqual(req["wall_clock"]["deadline_at"], self.CREATED + 210)
        self.assertNotIn("pi_since", req["wall_clock"])
        self.assertEqual(await hub.enforce_request_deadlines(now=self.at(209)), [], "past 120 s, inside 120+90")
        self.assertEqual(await hub.enforce_request_deadlines(now=self.at(211)), ["r"])
        self.assertEqual(req["deadline_receipt"]["held_s"], 90.0)

    async def test_a_wait_that_starts_and_ends_between_two_looks_is_still_counted_in_full(self):
        hub = self.hub(120)
        req = self.open_request(hub)
        await hub.enforce_request_deadlines(now=self.at(10))
        self.wait_for_pi(hub, req, 30)
        self.answered(hub, req, 80)
        await hub.enforce_request_deadlines(now=self.at(90))
        self.assertEqual(req["wall_clock"]["held_s"], 50.0)
        # Cards that repeat add up exactly: a look right before one starts does not charge its running time.
        self.wait_for_pi(hub, req, 100)
        self.answered(hub, req, 130)
        self.assertEqual(req["wall_clock"]["held_s"], 80.0)
        self.assertEqual(await hub.enforce_request_deadlines(now=self.at(199)), [])
        self.assertEqual(await hub.enforce_request_deadlines(now=self.at(201)), ["r"])

    async def test_review_and_later_states_and_interrupted_requests_are_never_cut(self):
        for status in ("review_pending", "done", "failed", "interrupted"):
            hub = self.hub(120)
            req = self.open_request(hub, status=status)
            self.assertEqual(await hub.enforce_request_deadlines(now=self.at(10 ** 5)), [], status)
            self.assertEqual(req["status"], status)
        resumed = self.hub(120)
        req = self.open_request(resumed, status="interrupted")
        await resumed.enforce_request_deadlines(now=self.at(500))
        req["status"] = "running"  # the PI resumed it after the deadline: the same deadline applies at once
        self.assertEqual(await resumed.enforce_request_deadlines(now=self.at(501)), ["r"])

    async def test_a_restart_keeps_the_deadline_and_the_held_time_and_does_not_subtract_the_downtime(self):
        import tempfile

        state = tempfile.mkdtemp()
        first = self.hub(120, state)
        req = self.open_request(first)
        self.wait_for_pi(first, req, 10)
        self.answered(first, req, 40)
        first.save_request("r")
        self.assertEqual(req["wall_clock"]["held_s"], 30.0)
        second = self.hub(120, state)  # the process came back much later
        again = second.requests["r"]
        self.assertEqual((again["created_at"], again["wall_clock"]["held_s"]), (self.CREATED, 30.0))
        self.assertEqual(again["status"], "interrupted", "the restart interrupted it; the watch leaves it alone")
        self.assertEqual(await second.enforce_request_deadlines(now=self.at(500)), [])
        again["status"] = "running"  # the PI resumed it
        self.assertEqual(await second.enforce_request_deadlines(now=self.at(149)), [])
        self.assertEqual(await second.enforce_request_deadlines(now=self.at(151)), ["r"])

    async def test_a_waiting_pi_interval_that_a_restart_left_open_is_not_added(self):
        import tempfile

        state = tempfile.mkdtemp()
        first = self.hub(120, state)
        req = self.open_request(first)
        self.wait_for_pi(first, req, 10)
        first.save_request("r")
        second = self.hub(120, state)
        again = second.requests["r"]
        self.assertEqual(again["wall_clock"]["pi_since"], self.CREATED + 10)
        again["status"] = "running"  # the gateway interrupted it and the PI resumed it
        self.assertEqual(await second.enforce_request_deadlines(now=self.at(121)), ["r"])
        self.assertEqual(again["deadline_receipt"]["held_s"], 0.0)

    async def test_a_turn_that_never_reports_after_the_cancel_is_recorded_unconfirmed(self):
        hub = self.hub(120)
        hub.cancel_result_wait_s = 0.02
        self.open_request(hub)
        hub.store.put("task", "t1", {"request_id": "r", "completed": False, "payload": {"agent_id": "analyst"}})

        async def cancel_task(tid):
            return True

        hub.cancel_task = cancel_task
        self.assertEqual(await hub.enforce_request_deadlines(now=self.at(200)), ["r"])
        receipt = hub.requests["r"]["deadline_receipt"]
        self.assertEqual((receipt["termination"], receipt["cancelled_tasks"], receipt["unconfirmed_tasks"]),
                         ("unconfirmed", ["t1"], ["t1"]))

    async def test_a_deadline_cancel_that_a_restart_finishes_still_gets_its_receipt(self):
        hub = self.hub(120)
        req = self.open_request(hub, status="cancelled", cancel_reason="request_deadline", cancelled_at=self.at(130),
                                cancelled_tasks=["t1"], cancelling={"quota_steps": []}, error="x")
        hub.store.put("task", "t1", {"request_id": "r", "completed": False, "payload": {"agent_id": "analyst"}})
        self.assertEqual(await hub.finish_interrupted_cancels(), ["r"])
        receipt = req["deadline_receipt"]
        self.assertEqual((receipt["termination"], receipt["cancelled_tasks"], receipt["unconfirmed_tasks"],
                          receipt["fired_at"]), ("unconfirmed", ["t1"], ["t1"], self.CREATED + 130))

    async def test_the_watch_runs_in_the_background_and_stops_with_the_gateway(self):
        import asyncio

        hub = self.hub(120)
        hub.request_deadline_tick_s = 0.01
        req = self.open_request(hub)
        req["created_at"] = 1.0  # long past
        hub.start_deadline_watch()
        for _ in range(200):
            if req["status"] == "cancelled":
                break
            await asyncio.sleep(0.01)
        self.assertEqual(req["cancel_reason"], "request_deadline")
        hub.stopping = True
        await asyncio.wait_for(hub._deadline_task, 2)

    def test_the_setting_is_off_or_at_least_a_minute(self):
        from pydantic import ValidationError

        from labhq.settings import OrchestratorSettings

        self.assertEqual(OrchestratorSettings().request_wall_clock_s, 0)
        self.assertEqual(OrchestratorSettings(request_wall_clock_s=60).request_wall_clock_s, 60)
        for bad in (30, -1, 8 * 86400):
            with self.assertRaises(ValidationError):
                OrchestratorSettings(request_wall_clock_s=bad)


if __name__ == "__main__":
    unittest.main()
