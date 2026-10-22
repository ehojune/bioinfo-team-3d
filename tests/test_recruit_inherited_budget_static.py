"""#862: extracted production budget/Task/accounting code with inert fakes, no runtime imports or event loop."""
import ast
import copy
import math
import unittest
from decimal import Decimal, ROUND_FLOOR
from pathlib import Path
from types import SimpleNamespace as NS
from typing import Annotated, Any

from pydantic import BaseModel, Field


ROOT = Path(__file__).resolve().parents[1]


def extract(path, names, scope=None, owner=None):
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"), feature_version=(3, 10))
    body = tree.body if owner is None else next(n for n in tree.body if isinstance(n, ast.ClassDef)
                                               and n.name == owner).body
    nodes = [copy.deepcopy(n) for n in body if getattr(n, "name", None) in names]
    assert len(nodes) == len(names)
    for node in nodes:
        node.decorator_list = []
    namespace = {"math": math, "Decimal": Decimal, "ROUND_FLOOR": ROUND_FLOOR, **(scope or {})}
    exec(compile(ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[])), path, "exec",
                 flags=__import__("__future__").annotations.compiler_flag), namespace)
    return namespace


def settings(reserve=True, maximum=25):
    return NS(policy=NS(budget=NS(reserve_before_dispatch=reserve)),
              recruit=NS(max_budget_usd=maximum, agent_id="recruiter", permission_mode="default"))


def budget(settings, parent, cap, remaining):
    return extract("labhq/recruit/paper2agent.py", {"conversion_budget"})["conversion_budget"](
        settings, parent, cap, remaining)


def task_factory(s, cap, remaining, parent="outer"):
    # The production Task declaration, without importing its module or any runtime. Path metadata is inert here:
    # no path serialization or security operation is exercised by this numeric-budget construction test.
    task_ns = extract("labhq/models.py", {"Task"}, {"BaseModel": BaseModel, "Field": Field, "Any": Any,
                      "Annotated": Annotated, "RunnerPathFields": object(), "time": NS(time=lambda: 1),
                      "new_id": lambda _: "inner"})
    task_type = task_ns["Task"]
    task_type.model_rebuild(_types_namespace=task_ns)
    fn = extract("labhq/recruit/paper2agent.py", {"conversion_task"}, owner="Recruitment",
                 scope={"conversion_budget": budget, "Task": task_type,
                        "OFFER_SCHEMA": {}, "build_prompt": lambda *args: "fake conversion"})["conversion_task"]
    recruitment = NS(s=s, plan_task_id=parent, parent_budget=cap, parent_remaining=remaining,
                     request_id="request", paper="paper", repo="repo", focus="focus", slug="hire")
    return fn(recruitment, "unused-build")


def claude_budget_argv(task):
    # Exact production assignment and branch, not a second implementation of the adapter's precedence/format.
    tree = ast.parse((ROOT / "labhq/adapters/claude_code.py").read_text(encoding="utf-8"))
    method = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "build_command")
    index = next(i for i, n in enumerate(method.body) if isinstance(n, ast.Assign)
                 and any(isinstance(t, ast.Name) and t.id == "budget" for t in n.targets))
    ns = {"t": task, "a": NS(max_budget_usd=99), "self": NS(settings=NS(policy=NS(budget=NS(per_task_usd=100)))),
          "cmd": []}
    exec(compile(ast.Module(body=copy.deepcopy(method.body[index:index + 2]), type_ignores=[]),
                 "claude-budget-slice", "exec"), ns)
    return ns["cmd"]


class InheritedRecruitBudget(unittest.TestCase):
    def test_nested_factory_and_fake_runner_receive_outer_hold_in_claude_argv(self):
        class Runner:
            def run_task(self, task):
                self.task, self.argv = task, claude_budget_argv(task)
        runner = Runner()
        runner.run_task(task_factory(settings(), 1, 1))
        self.assertEqual(runner.task.request_id, "request")
        self.assertEqual((runner.task.budget_usd, runner.task.request_budget_remaining_usd), (1, 1))
        self.assertEqual(runner.argv, ["--max-budget-usd", "1.00"])
        self.assertEqual(runner.task.meta["kind"], "recruit")

    def test_each_parent_remaining_and_recruit_limit_bounds_nested_turn(self):
        for cap, remaining, maximum, expected in [(3, 1, 25, 1), (1, 3, 25, 1), (3, 3, .5, .5)]:
            with self.subTest(cap=cap, remaining=remaining, maximum=maximum):
                child = task_factory(settings(maximum=maximum), cap, remaining)
                self.assertEqual(child.budget_usd, expected)
                self.assertEqual(child.request_budget_remaining_usd, expected)

    def test_invalid_present_allowances_fail_closed_instead_of_defaulting(self):
        for value in (0, -1, float("nan"), float("inf"), -float("inf"), True, "1", [], {}):
            for slot in range(3):
                values = [1, 1, 25]
                values[slot] = value
                with self.subTest(value=value, slot=slot), self.assertRaises(ValueError):
                    budget(settings(maximum=values[2]), "outer", values[0], values[1])

    def test_absent_reserved_allowance_is_distinct_from_invalid_or_manual(self):
        for cap, remaining in ((None, 1), (1, None), (None, None)):
            with self.assertRaisesRegex(ValueError, "requires a parent budget"):
                budget(settings(), "outer", cap, remaining)
        self.assertEqual(budget(settings(), None, None, None), (25, None))
        self.assertEqual(budget(settings(False), "outer", None, None), (25, None))

    def test_cent_rendering_never_rounds_above_the_reserved_amount(self):
        for cap in (.009, .0001):
            with self.assertRaises(ValueError):
                task_factory(settings(), cap, cap)
        for cap, expected in ((.019, "0.01"), (.335, "0.33"), (1.999, "1.99"), (.29, "0.29"), (1.15, "1.15")):
            self.assertEqual(claude_budget_argv(task_factory(settings(), cap, cap)),
                             ["--max-budget-usd", expected])

    def test_manual_and_reservation_off_keep_configured_conversion_budget(self):
        for s, parent in ((settings(False), "outer"), (settings(), None)):
            child = task_factory(s, None, None, parent)
            self.assertEqual(child.budget_usd, 25)
            self.assertIsNone(child.request_budget_remaining_usd)
            self.assertEqual(claude_budget_argv(child), ["--max-budget-usd", "25.00"])

    def test_lost_or_cancelled_nested_result_keeps_outer_cost_unknown(self):
        pending = extract("labhq/recruit/paper2agent.py", {"conversion_cost_pending"})["conversion_cost_pending"]
        self.assertFalse(pending(None))
        self.assertFalse(pending(NS(conversion_started=False, conversion_result=None)))
        self.assertTrue(pending(NS(conversion_started=True, conversion_result=None)))
        for result in (NS(cost_usd=.25, cost_known=True), NS(cost_usd=None, cost_known=False)):
            self.assertFalse(pending(NS(conversion_started=True, conversion_result=result)))
        tree = ast.parse((ROOT / "labhq/recruit/planning.py").read_text(encoding="utf-8"))
        run = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "run_task")
        finish = ast.FunctionDef(name="finish", args=ast.arguments(posonlyargs=[], args=[], kwonlyargs=[],
                                 kw_defaults=[], defaults=[]), body=copy.deepcopy(run.body[-2:]), decorator_list=[])
        ns = {"runner": NS(s=settings()), "paper2agent": NS(conversion_cost_pending=pending),
              "conversion": NS(conversion_started=True, conversion_result=None), "TaskResult": lambda **kw: NS(**kw),
              "task": NS(id="outer", agent_id="recruiter"), "receipt": {"status": "timed_out"}}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[finish], type_ignores=[])), "outer-result", "exec"), ns)
        result = ns["finish"]()
        self.assertIsNone(result.cost_usd)
        self.assertFalse(result.cost_known)
        ns["conversion"].conversion_result = NS(cost_known=False)  # already accounted by its own nested ID
        result = ns["finish"]()
        self.assertEqual(result.cost_usd, 0)
        self.assertTrue(result.cost_known)

    def test_planning_propagates_both_allowances_and_never_copies_nested_spend(self):
        tree = ast.parse((ROOT / "labhq/recruit/planning.py").read_text(encoding="utf-8"))
        outer = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "run_task")
        payload = next(n for n in ast.walk(outer) if isinstance(n, ast.Call)
                       and isinstance(n.func, ast.Attribute) and n.func.attr == "Recruitment").args[1]
        pairs = {k.value: ast.unparse(v) for k, v in zip(payload.keys, payload.values) if isinstance(k, ast.Constant)}
        self.assertEqual(pairs["parent_budget_usd"], "task.budget_usd")
        self.assertEqual(pairs["parent_budget_remaining_usd"], "task.request_budget_remaining_usd")
        result = outer.body[-1].value
        keywords = {k.arg: ast.unparse(k.value) for k in result.keywords}
        self.assertEqual(keywords["cost_usd"], "None if pending else 0")
        self.assertEqual(keywords["cost_known"], "not pending")

    def test_cap_validation_precedes_filesystem_and_dispatch_marks_uncertainty(self):
        tree = ast.parse((ROOT / "labhq/recruit/paper2agent.py").read_text(encoding="utf-8"))
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Recruitment")
        run = next(n for n in cls.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "run")
        text = [ast.unparse(n) for n in run.body]
        validation = next(i for i, t in enumerate(text) if t.startswith("conversion_budget("))
        mkdir = next(i for i, t in enumerate(text) if "build.mkdir(" in t)
        started = next(i for i, t in enumerate(text) if t == "self.conversion_started = True")
        dispatch = next(i for i, t in enumerate(text) if "asyncio.ensure_future(" in t)
        waited = next(i for i, t in enumerate(text) if t == "result = await turn")
        recorded = next(i for i, t in enumerate(text) if t == "self.conversion_result = result")
        self.assertLess(validation, mkdir)
        self.assertLess(started, dispatch)
        self.assertEqual(recorded, waited + 1)

    def test_existing_request_cost_collection_deduplicates_nested_and_outer_ids(self):
        scope = {"task_cost_item": lambda result, agent: {"status": "unknown" if not result.cost_known else "reported",
                                                         "usd": result.cost_usd}}
        methods = extract("labhq/orchestrator/cso.py", {"_count_cost", "_count_recorded_costs"}, scope,
                          owner="Orchestrator")
        for inner in ({"status": "reported", "usd": .75}, {"status": "unknown", "usd": None}):
            with self.subTest(inner=inner):
                req = {"cost_items": {"inner": inner, "outer": {"status": "reported", "usd": 0}},
                       "cost_by_task": {"inner": inner["usd"], "outer": 0}}
                cso = NS(hub=NS(requests={"r": req}), cost_tasks={}, cost_unknown={}, cost={})
                cso._count_cost = lambda *a: methods["_count_cost"](cso, *a)
                methods["_count_recorded_costs"](cso, "r")
                methods["_count_recorded_costs"](cso, "r")
                self.assertEqual(cso.cost_tasks["r"], {"inner", "outer"})
                self.assertEqual(cso.cost["r"], .75 if inner["status"] == "reported" else 0)
                self.assertEqual(cso.cost_unknown.get("r", set()), {"inner"} if inner["status"] == "unknown" else set())

    def test_gateway_records_nested_spend_or_unknown_and_outer_zero_once(self):
        def item(result, agent):
            return {"status": "reported" if result.cost_known else "unknown", "usd": result.cost_usd}
        fn = extract("labhq/gateway/server.py", {"_record_task_cost"}, owner="Hub",
                     scope={"task_cost_item": item, "outcome_unknown_item": item,
                            "request_cost_summary": lambda req: None, "time": NS(time=lambda: 1)})["_record_task_cost"]
        for amount in (.75, None):
            hub = NS(requests={"r": {}}, agents={}, store=None, save_request=lambda rid: None)
            inner = NS(agent_id="recruiter", cost_known=amount is not None, cost_usd=amount,
                       usage_known=True, usage={})
            outer = NS(agent_id="recruiter", cost_known=True, cost_usd=0, usage_known=True, usage={})
            for _ in range(2):  # replay must not charge either task twice
                fn(hub, "r", "inner", inner)
                fn(hub, "r", "outer", outer)
            self.assertEqual(hub.requests["r"]["cost_usd"], amount or 0)
            self.assertEqual(hub.requests["r"]["cost_known"], amount is not None)
            self.assertEqual(hub.requests["r"]["cost_by_task"], {"inner": amount, "outer": 0})
