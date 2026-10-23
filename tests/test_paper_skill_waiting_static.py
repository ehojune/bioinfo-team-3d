"""Synchronous source/pure/schema contracts; no runtime, loop, runner, thread or process."""
import ast
import copy
import re
from pathlib import Path
from types import SimpleNamespace
import unittest


ROOT = Path(__file__).resolve().parents[1]


def definitions(path, names, scope):
    tree = ast.parse((ROOT / path).read_text(encoding='utf-8'), feature_version=(3, 10))
    body = [node for node in tree.body if getattr(node, 'name', None) in names]
    assert len(body) == len(names)
    exec(compile(ast.Module(body=body, type_ignores=[]), str(path), 'exec'), scope)
    return scope


def settings():
    return SimpleNamespace(recruit=SimpleNamespace(agent_id='recruiter', max_budget_usd=25),
                           runner=SimpleNamespace(task_timeout_s=30, max_parallel=4),
                           orchestrator=SimpleNamespace(max_parallel_steps=4))


def row(sid='s1'):
    return {'paper_id': 'pmid:1', 'selection_sha256': 'a' * 64, 'title': 'Synthetic',
            'reason': 'Synthetic method', 'step_ids': ['s1', 's2'], 'agent_ids': ['worker'],
            'request_id': 'r', 'step_id': sid, 'agent_id': 'worker', 'status': 'deferred'}


class WaitingStatic(unittest.TestCase):
    def test_lifecycle_plans_validate_before_the_approval_hash_is_sealed(self):
        from labhq.research.contract import ResearchPlan, plan_sha256
        from pydantic import ValidationError
        from tests.paper_skill_plan import plan_for_paper_rows
        source = ast.parse((ROOT / 'tests/test_paper_skill_waiting.py').read_text(encoding='utf-8'))
        hub = next(n for n in source.body if isinstance(n, ast.ClassDef) and n.name == 'Hub')
        init = next(n for n in hub.body if getattr(n, 'name', None) == '__init__')
        # Only plan-building assignments, never Settings/Hub/Orchestrator initialization or async code.
        assignments = [n for n in init.body if isinstance(n, ast.Assign)
                       and ast.unparse(n.targets[0]) in {'steps', 'self.plan'}]
        construction = compile(ast.Module(body=assignments, type_ignores=[]), '<fixture-plan>', 'exec')
        cases = ([{**row(), 'step_ids': ['s1']}], [row()],
                 [{**row(), 'step_ids': ['s1']},
                  {**row(), 'step_ids': ['s2'], 'paper_id': 'pmid:2', 'selection_sha256': 'b' * 64}],
                 [{**row(), 'step_ids': ['s1', 's3']},
                  {**row(), 'step_ids': ['s2'], 'paper_id': 'pmid:2', 'selection_sha256': 'b' * 64}])
        for rows in cases:
            with self.subTest(papers=len(rows), steps=sum(len(r['step_ids']) for r in rows)):
                scope = {'self': SimpleNamespace(), 'rows': rows, 'plan_for_paper_rows': plan_for_paper_rows}
                exec(construction, scope)
                plan = scope['self'].plan
                validated = ResearchPlan.model_validate(plan)
                ids = list(dict.fromkeys(sid for selected in rows for sid in selected['step_ids']))
                self.assertEqual([s.id for s in validated.steps], ids)
                self.assertEqual(plan_sha256(plan), plan_sha256(validated))
                # The original broken fixture must still fail the real, unchanged validator.
                with self.assertRaises(ValidationError):
                    ResearchPlan.model_validate({'steps': [{'id': sid, 'agent_id': 'worker'} for sid in ids],
                                                 'paper_skill_results': rows})

    def test_lifecycle_fixture_isolated_rows_and_scientific_fields_remain_hash_bound(self):
        from labhq.research.contract import plan_sha256
        from tests.paper_skill_plan import plan_for_paper_rows
        selected = row()
        plan = plan_for_paper_rows([selected])
        approved = plan_sha256(plan)
        selected['step_ids'].append('unapproved')
        self.assertEqual(plan['paper_skill_results'][0]['step_ids'], ['s1', 's2'])
        for change in ('selection', 'scope', 'input'):
            with self.subTest(change=change):
                changed = copy.deepcopy(plan)
                if change == 'selection':
                    changed['paper_skill_results'][0]['reason'] = 'Different method'
                elif change == 'scope':
                    changed['brief']['scope'] = 'Different scientific scope'
                else:
                    changed['steps'][0]['input_refs'] = ['different-input']
                self.assertNotEqual(plan_sha256(changed), approved)
        independent = plan_for_paper_rows([row()])
        independent['protocol']['resource_limits'].append('consumer mutation')
        self.assertEqual(plan_sha256(plan), approved)

    def scope(self):
        policy = definitions('labhq/recruit/paper2agent.py', {'enabled'}, {})
        return definitions('labhq/recruit/paper_skills.py', {'build_task', 'ApprovedBuilds'},
                           {'copy': copy, 'Task': SimpleNamespace,
                            'paper2agent': SimpleNamespace(enabled=policy['enabled'])})

    def test_dispatch_snapshot_never_suspends_and_later_completion_cannot_replace_it(self):
        tree = ast.parse((ROOT / 'labhq/recruit/paper_skills.py').read_text(encoding='utf-8'))
        cls = next(n for n in tree.body if getattr(n, 'name', None) == 'ApprovedBuilds')
        method = next(n for n in cls.body if getattr(n, 'name', None) == 'resolve')
        self.assertFalse(any(isinstance(n, (ast.Await, ast.AsyncFor, ast.AsyncWith)) for n in ast.walk(method)),
                         'optional conversion must never suspend ready-step admission')
        # Execute the identical non-suspending body synchronously, not a coroutine or event loop.
        plain = ast.FunctionDef(name='snapshot', args=method.args, body=method.body,
                               decorator_list=[], returns=method.returns)
        ns = self.scope()
        exec(compile(ast.fix_missing_locations(ast.Module(body=[plain], type_ignores=[])),
                     '<dispatch-snapshot>', 'exec'), ns)
        batch = ns['ApprovedBuilds']([row()], settings())
        class Job:
            available = False
            def done(self): return self.available
            def result(self):
                assert self.available
                return {'status': 'ready', 'skill_sha256': 'f' * 64}
            def cancel(self): raise AssertionError('dispatch must not cancel shared preparation')
        job = Job()
        batch.jobs['a' * 64] = job
        first = ns['snapshot'](batch, [row()])
        self.assertEqual((first[0]['status'], first[0]['error']), ('unavailable', 'paper_skill_late'))
        self.assertNotIn('skill_sha256', first[0])
        job.available = True
        self.assertEqual(ns['snapshot'](batch, [row()]), first)
        later = ns['snapshot'](batch, [row('s2')])
        self.assertEqual((later[0]['status'], later[0]['step_id']), ('ready', 's2'))
        self.assertNotIn('a' * 64, batch.disabled)
        cso = ast.parse((ROOT / 'labhq/orchestrator/cso.py').read_text(encoding='utf-8'))
        admission = next(n for n in ast.walk(cso) if isinstance(n, ast.AsyncFunctionDef)
                         and n.name == '_resolve_step_paper_skills')
        calls = {ast.unparse(n.func) for n in ast.walk(admission) if isinstance(n, ast.Call)}
        self.assertFalse(calls & {'self._start_paper_builds', 'self._close_paper_builds', 'batch.close'})
        self.assertTrue({'self._check_budget', 'self._verify_research_reuse'} <= calls)

    def test_gateway_recovery_binds_the_actual_id_and_moves_the_hold_once(self):
        tree = ast.parse((ROOT / 'labhq/orchestrator/cso.py').read_text(encoding='utf-8'))
        cls = next(n for n in tree.body if getattr(n, 'name', None) == 'Orchestrator')
        # _budget_bound also sums the #862 dispatch holds stored on the request record (none here).
        names = {'_bind_paper_dispatch_identity', '_budget_bound', '_unknown_reserve', '_dispatch_held'}
        cls.body = [n for n in cls.body if getattr(n, 'name', None) in names]
        self.assertEqual({n.name for n in cls.body}, names, 'recovered work needs an authoritative ID binding')
        amount = next(n for n in tree.body if getattr(n, 'name', None) == '_usd_amount')
        ns = {}
        prelude = ast.parse('from __future__ import annotations; import math').body
        exec(compile(ast.Module(body=[*prelude, amount, cls], type_ignores=[]), '<durable-hold>', 'exec'), ns)
        owner = ns['Orchestrator']()
        owner.paper_task_ids, owner.paper_budget_holds = {}, {'r': {'new': 5}}
        owner.cost, owner.cost_tasks, owner.cost_unknown = {}, {}, {}
        owner.hub = SimpleNamespace(requests={'r': {}}, s=SimpleNamespace(policy=SimpleNamespace(
            budget=SimpleNamespace(per_task_usd=5))))
        owner._bind_paper_dispatch_identity('r', 'new', 'durable')
        self.assertEqual(owner.paper_task_ids['r']['new'], 'durable')
        self.assertEqual(owner.paper_budget_holds['r'], {'durable': 5})
        self.assertEqual(owner._budget_bound('r', 10), (0, 0, 5))
        owner.cost_tasks, owner.cost_unknown = {'r': {'durable'}}, {'r': {'durable'}}
        owner._bind_paper_dispatch_identity('r', 'new', 'durable')
        self.assertEqual(owner._budget_bound('r', 10), (0, 1, 5))
        owner.cost, owner.cost_unknown = {'r': 0.25}, {}
        self.assertEqual(owner._budget_bound('r', 10), (0.25, 0, 0.25))
        gateway = ast.parse((ROOT / 'labhq/gateway/server.py').read_text(encoding='utf-8'))
        dispatch = next(n for n in ast.walk(gateway) if isinstance(n, ast.AsyncFunctionDef) and n.name == 'dispatch')
        bind = next(n for n in ast.walk(dispatch) if isinstance(n, ast.Call)
                    and ast.unparse(n.func) == 'bind_identity' and ast.unparse(n.args[0]) == 'tid')
        wait = next(n for n in ast.walk(dispatch) if isinstance(n, ast.Call)
                    and ast.unparse(n.func) == 'self._await_prior_task')
        self.assertLess(bind.lineno, wait.lineno)

    def test_assigned_steps_share_one_approved_build_without_mutable_aliases(self):
        ns = self.scope()
        first, second = row(), row('s2')
        batch = ns['ApprovedBuilds']([first, second], settings())
        self.assertEqual(len(batch.tasks), 1)
        self.assertEqual(ns['build_task'](first, settings()).meta['step_id'],
                         ns['build_task'](second, settings()).meta['step_id'])
        first['step_ids'].append('unapproved')
        second['title'] = 'changed'
        self.assertNotIn('unapproved', batch.rows['a' * 64]['step_ids'])
        self.assertEqual(batch.tasks['a' * 64].meta['paper_skill']['title'], 'Synthetic')
        changed = {**row(), 'selection_sha256': 'b' * 64}
        self.assertNotEqual(ns['build_task'](row(), settings()).meta['step_id'],
                            ns['build_task'](changed, settings()).meta['step_id'])

    def test_disabled_timeout_is_fail_closed_before_admission(self):
        s = settings()
        s.runner.task_timeout_s = 0
        batch = self.scope()['ApprovedBuilds']([row()], s)
        self.assertEqual(batch.disabled, {'a' * 64: 'invalid_task_timeout'})
        s.runner.task_timeout_s, s.runner.max_parallel = 30, 0
        batch = self.scope()['ApprovedBuilds']([row()], s)
        self.assertEqual(batch.disabled, {'a' * 64: 'paper_skill_parallel_unavailable'})

    def test_ambiguous_descriptor_and_more_than_two_papers_cannot_start(self):
        cls = self.scope()['ApprovedBuilds']
        batch = cls([row(), {**row('s2'), 'reason': 'different scientific purpose'}], settings())
        self.assertEqual(batch.disabled, {'a' * 64: 'paper_skill_ambiguous_selection'})
        batch = cls([{**row(), 'selection_sha256': ch * 64} for ch in 'abc'], settings())
        self.assertEqual(set(batch.disabled.values()), {'paper_skill_selection_limit'})

    def test_late_fallback_remains_unavailable_in_final_receipt_and_never_called(self):
        class Result(SimpleNamespace):
            def model_copy(self, *, update):
                return Result(**{**vars(self), **update})
        ns = definitions('labhq/recruit/paper_skills.py',
            {'with_preparation_outcome', 'records', 'appendix', 'reference_token'},
            {'copy': copy, 're': re, 'TaskResult': Result, 'Task': SimpleNamespace})
        selected = {**row(), 'status': 'unavailable', 'error': 'paper_skill_late'}
        original = Result(task_id='t', provenance={})
        result = ns['with_preparation_outcome'](original, SimpleNamespace(meta={'paper_skills': [selected]}))
        plan = {'paper_skill_results': [row()]}
        results = {'s1': vars(result)}
        report = ns['records'](plan, results)[0]
        self.assertEqual(report['status'], 'unavailable')
        self.assertFalse(report['built'] or report['called'] or report['attached_steps'])
        self.assertIn('paper_skill_late', ns['appendix'](plan, results))
        self.assertEqual(original.provenance, {})

    def test_inflight_dollars_are_not_zero_or_double_counted_as_recorded_cost(self):
        tree = ast.parse((ROOT / 'labhq/orchestrator/cso.py').read_text(encoding='utf-8'))
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'Orchestrator')
        cls.body = [n for n in cls.body if getattr(n, 'name', None) in {
            '_budget_bound', '_unknown_reserve', '_request_budget_remaining', '_paper_build_allowance',
            '_dispatch_held'}]  # _budget_bound also sums the #862 dispatch holds on the request record (none here)
        amount = next(n for n in tree.body if getattr(n, 'name', None) == '_usd_amount')
        scope = {}
        prelude = ast.parse('from __future__ import annotations; import math').body
        exec(compile(ast.Module(body=[*prelude, amount, cls], type_ignores=[]), '<budget-contract>', 'exec'), scope)
        owner = scope['Orchestrator']()
        owner.hub = SimpleNamespace(requests={'r': {}}, s=SimpleNamespace(policy=SimpleNamespace(
            budget=SimpleNamespace(per_task_usd=5))))
        owner.cost, owner.cost_unknown, owner.cost_tasks = {'r': 1}, {}, {}
        owner.paper_budget_holds = {'r': {'pending': 5}}
        self.assertEqual(owner._budget_bound('r', 10), (1, 0, 6))
        owner.cost_tasks = {'r': {'pending'}}
        owner.cost_unknown = {'r': {'pending'}}
        self.assertEqual(owner._budget_bound('r', 10), (1, 1, 6))
        owner.cost_unknown, owner.cost = {}, {'r': 3}
        self.assertEqual(owner._budget_bound('r', 10), (3, 0, 3))
        owner.hub.requests = {'r': {'budget_usd': 10}}
        owner.hub.s.recruit = SimpleNamespace(max_budget_usd=25)
        owner.cost, owner.cost_tasks, owner.paper_budget_holds = {}, {}, {}
        self.assertEqual(owner._paper_build_allowance('r', 1), 5)
        owner.paper_budget_holds = {'r': {'first': 5}}
        self.assertEqual(owner._paper_build_allowance('r', 1), 0)
        self.assertEqual(owner._request_budget_remaining('r'), 5)  # reserved for actual work, not a second paper
        owner.hub.requests['r']['budget_usd'] = 15
        self.assertEqual(owner._paper_build_allowance('r', 1), 5)
        self.assertEqual(owner._paper_build_allowance('r', 2), 0)

    def test_preparation_cannot_replace_the_task_cap_with_the_larger_recruit_cap(self):
        tree = ast.parse((ROOT / 'labhq/recruit/paper_skills.py').read_text(encoding='utf-8'))
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'PaperSkillBuild')
        prepare = next(n for n in cls.body if getattr(n, 'name', None) == 'prepare')
        values = [value for n in ast.walk(prepare) if isinstance(n, ast.Dict)
                  for key, value in zip(n.keys, n.values)
                  if isinstance(key, ast.Constant) and key.value == 'budget_usd']
        self.assertEqual(len(values), 1)
        expression = compile(ast.Expression(values[0]), '<cap-expression>', 'eval')
        for cap in (2, 25, 30):
            self.assertEqual(eval(expression, {'self': SimpleNamespace(task=SimpleNamespace(budget_usd=cap)),
                                             's': settings()}), min(cap, 25))

    def test_original_two_paper_selection_limit_and_no_preapproval_build_remain(self):
        source = (ROOT / 'labhq/recruit/paper_skills.py').read_text(encoding='utf-8')
        tree = ast.parse(source)
        selection = next(n for n in tree.body if getattr(n, 'name', None) == 'selections')
        self.assertIn('if len(chosen) == 2:', ast.unparse(selection))
        preparation = next(n for n in tree.body if getattr(n, 'name', None) == 'prepare_plan')
        calls = [ast.unparse(n.func) for n in ast.walk(preparation) if isinstance(n, ast.Call)]
        self.assertFalse(set(calls) & {'run', 'build_task', 'resolve_attachments', 'asyncio.create_task'})

    def test_two_papers_is_per_plan_not_a_lifetime_cap_and_ids_deduplicate(self):
        from pydantic import BaseModel, ConfigDict, Field
        ns = definitions('labhq/tools/annot_literature.py', {'source_access_metadata'}, {})
        ns.update({'BaseModel': BaseModel, 'ConfigDict': ConfigDict, 'Field': Field, 'Any': object, 're': re})
        ns = definitions('labhq/recruit/paper_skills.py',
            {'PaperSkillSelection', 'paper_id', 'selections'}, ns)
        plan = {'steps': [{'id': 's1', 'agent_id': 'worker'}], 'paper_skills': [
            {'paper_id': pid, 'title': 'Synthetic', 'reason': 'Method', 'step_ids': ['s1']}
            for pid in ('PMID:1', 'https://pubmed.ncbi.nlm.nih.gov/1/', 'PMID:2', 'PMID:3')]}
        evidence = {'papers': [{'title': 'Synthetic', 'citations': [f'PMID:{n}']}
                               for n in (1, 2, 3)]}
        first = ns['selections'](plan, evidence, [{'id': 'worker'}])
        self.assertEqual([r['paper_id'] for r in first], ['pmid:1', 'pmid:2'])
        self.assertEqual([r['access_status'] for r in first], ['undetermined', 'undetermined'])
        revised = {**plan, 'paper_skills': plan['paper_skills'][-1:]}
        second = ns['selections'](revised, evidence, [{'id': 'worker'}])
        self.assertEqual([r['paper_id'] for r in second], ['pmid:3'])
        self.assertEqual(len(first) + len(second), 3)  # possible across plans, not a historical diagnosis


if __name__ == '__main__':
    unittest.main()
