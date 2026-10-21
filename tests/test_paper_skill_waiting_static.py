"""Synchronous source/pure contracts; no product import, loop, runner, thread or process."""
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
    def scope(self):
        return definitions('labhq/recruit/paper_skills.py', {'build_task', 'ApprovedBuilds'},
                           {'copy': copy, 'Task': SimpleNamespace})

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
            '_budget_bound', '_unknown_reserve', '_request_budget_remaining', '_paper_build_allowance'}]
        scope = {}
        exec(compile(ast.Module(body=[cls], type_ignores=[]), '<budget-contract>', 'exec'), scope)
        owner = scope['Orchestrator']()
        owner.hub = SimpleNamespace(s=SimpleNamespace(policy=SimpleNamespace(
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
        ns = definitions('labhq/recruit/paper_skills.py',
            {'PaperSkillSelection', 'paper_id', 'selections'},
            {'BaseModel': BaseModel, 'ConfigDict': ConfigDict, 'Field': Field, 'Any': object, 're': re})
        plan = {'steps': [{'id': 's1', 'agent_id': 'worker'}], 'paper_skills': [
            {'paper_id': pid, 'title': 'Synthetic', 'reason': 'Method', 'step_ids': ['s1']}
            for pid in ('PMID:1', 'https://pubmed.ncbi.nlm.nih.gov/1/', 'PMID:2', 'PMID:3')]}
        evidence = {'papers': [{'title': 'Synthetic', 'citations': [f'PMID:{n}']}
                               for n in (1, 2, 3)]}
        first = ns['selections'](plan, evidence, [{'id': 'worker'}])
        self.assertEqual([r['paper_id'] for r in first], ['pmid:1', 'pmid:2'])
        revised = {**plan, 'paper_skills': plan['paper_skills'][-1:]}
        second = ns['selections'](revised, evidence, [{'id': 'worker'}])
        self.assertEqual([r['paper_id'] for r in second], ['pmid:3'])
        self.assertEqual(len(first) + len(second), 3)  # possible across plans, not a historical diagnosis


if __name__ == '__main__':
    unittest.main()
