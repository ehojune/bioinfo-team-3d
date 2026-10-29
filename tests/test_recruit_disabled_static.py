"""#869: extracted production guards, fake callbacks only; no app/Hub/runner/process imports or event loop."""
import __future__
import ast
import copy
import re
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from typing import Literal
from unittest.mock import Mock

from pydantic import BaseModel, StrictBool, ValidationError, field_validator

ROOT = Path(__file__).resolve().parents[1]


def extracted(path, names, scope=None, owner=None):
    tree = ast.parse((ROOT / path).read_text(encoding='utf8'), feature_version=(3, 10))
    body = tree.body
    if owner:
        body = next(n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == owner).body
    nodes = [copy.deepcopy(n) for n in body if getattr(n, 'name', None) in names]
    assert len(nodes) == len(names)
    for n in nodes:
        n.decorator_list = []
    scope = {} if scope is None else scope
    exec(compile(ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[])), path, 'exec',
                 flags=__future__.annotations.compiler_flag), scope)
    return scope


def immediate(coro):
    """Disabled async guards must complete without ever suspending or creating a loop."""
    try:
        value = coro.send(None)
    except StopIteration as done:
        return done.value
    finally:
        coro.close()
    raise AssertionError(f'guard unexpectedly suspended: {type(value).__name__}')


def settings(value=False):
    return NS(recruit=NS(enabled=value, agent_id='recruiter', max_budget_usd=1, default_ttl_days=30),
              runner=NS(force_engine=None, task_timeout_s=30, max_parallel=1),
              orchestrator=NS(max_parallel_steps=1))


def policy():
    ns = extracted('labhq/recruit/paper2agent.py', {'enabled', 'require_enabled', 'disabled_task', 'recruiter_engine'})
    return NS(**{k: v for k, v in ns.items() if not k.startswith('__')})


class Result(NS):
    def model_dump(self, **kw): return vars(self)
    def model_copy(self, *, update): return Result(**{**vars(self), **update})


class Refusal(Exception):
    def __init__(self, code, detail): self.status_code, self.detail = code, detail


class DisabledRecruit(unittest.TestCase):
    def test_setting_defaults_on_and_rejects_coercion(self):
        ns = extracted('labhq/settings.py', {'RecruitSettings'},
                       {'BaseModel': BaseModel, 'StrictBool': StrictBool, 'field_validator': field_validator,
                        'Literal': Literal, 're': re})
        cls = ns['RecruitSettings']
        cls.model_rebuild(_types_namespace=ns)
        self.assertIs(cls().enabled, True)
        self.assertIs(cls(enabled=False).enabled, False)
        for value in ('false', 'true', 0, 1, None, [], {}):
            with self.subTest(value=value), self.assertRaises(ValidationError): cls(enabled=value)

    def test_default_missing_recruiter_still_fails_and_mock_stays_explicit(self):
        p = policy()
        for value in (True,):
            with self.assertRaisesRegex(ValueError, 'missing from the roster'):
                p.recruiter_engine(settings(value), None)
        old = settings(); del old.recruit.enabled
        with self.assertRaisesRegex(ValueError, 'missing from the roster'): p.recruiter_engine(old, None)
        old.runner.force_engine = 'mock'
        self.assertEqual(p.recruiter_engine(old, None), 'mock')
        with self.assertRaisesRegex(ValueError, 'disabled'): p.recruiter_engine(settings(), None)

    def test_doctor_disabled_fix_never_checks_or_installs_and_default_still_fails(self):
        p = policy()
        p.skill_path = Mock(side_effect=AssertionError('must not inspect or install'))
        p.setup_paper2agent = Mock(side_effect=AssertionError('must not install'))
        ns = extracted('labhq/doctor.py', {'_paper2agent_row'},
                       {'paper2agent': p, '_row': lambda group, name, status, detail, hint: NS(status=status, detail=detail),
                        'Engine': NS(mock='mock'), 'subprocess': NS(CalledProcessError=type('CalledProcessError', (Exception,), {}))})
        for fix in (False, True):
            row = ns['_paper2agent_row'](settings(), [], fix=fix)
            self.assertEqual(row.status, 'skip'); self.assertIn('recruit.enabled=false', row.detail)
        self.assertEqual(ns['_paper2agent_row'](settings(True), [], fix=True).status, 'fail')
        p.setup_paper2agent.assert_not_called(); p.skill_path.assert_not_called()

    def test_disabled_setup_and_recruitment_refuse_before_import_or_files(self):
        p = policy()
        ns = extracted('labhq/recruit/paper2agent.py', {'setup_paper2agent'}, {'require_enabled': p.require_enabled})
        with self.assertRaisesRegex(ValueError, 'disabled'): ns['setup_paper2agent'](settings())
        ns = extracted('labhq/recruit/paper2agent.py', {'run'}, {'require_enabled': p.require_enabled}, owner='Recruitment')
        # No r/registry/talent attribute exists: any filesystem precursor would fail this test.
        with self.assertRaisesRegex(ValueError, 'disabled'): immediate(ns['run'](NS(s=settings())))

    def test_forged_task_kinds_and_attachments_denied_even_on_other_staff(self):
        p = policy(); task = NS(agent_id='worker', meta={})
        for kind in ('recruit', 'recruit_check', 'plan_recruit', 'paper_skill'):
            task.meta = {'kind': kind}; self.assertTrue(p.disabled_task(settings(), task))
        for rows in ([{'status': 'ready'}], [{'status': 'deferred'}], 'forged'):
            task.meta = {'paper_skills': rows}; self.assertTrue(p.disabled_task(settings(), task))
        task.meta = {}; task.agent_id = 'recruiter'; self.assertTrue(p.disabled_task(settings(), task))
        task.agent_id = 'worker'; self.assertFalse(p.disabled_task(settings(), task))
        task.meta = {'kind': 'paper_skill'}; self.assertFalse(p.disabled_task(settings(True), task))

    def test_gateway_dispatch_refuses_before_recovery_or_send_but_binds_current_id(self):
        ns = extracted('labhq/gateway/server.py', {'dispatch'}, {'paper2agent': policy(), 'TaskResult': Result}, owner='Hub')
        task = NS(id='new', agent_id='worker', request_id='request', meta={'paper_skills': [{'status': 'ready'}]})
        bound = []
        # No requests/recovery/store/runners attributes: the disabled branch must precede all of them.
        result = immediate(ns['dispatch'](NS(s=settings()), task, bind_identity=bound.append))
        self.assertEqual(bound, ['new']); self.assertFalse(result.ok)
        self.assertEqual(result.cost_usd, 0); self.assertTrue(result.cost_known)

    def routes(self):
        tree = ast.parse((ROOT / 'labhq/gateway/server.py').read_text(encoding='utf8'))
        app = next(n for n in tree.body if getattr(n, 'name', None) == 'create_app')
        scope = {'settings': settings(), 'paper2agent': policy(), 'HTTPException': Refusal}
        nodes = [copy.deepcopy(n) for n in app.body if getattr(n, 'name', None) in {'recruit', 'contract'}]
        for n in nodes: n.decorator_list = []
        exec(compile(ast.Module(body=nodes, type_ignores=[]), '<routes-only>', 'exec',
                     flags=__future__.annotations.compiler_flag), scope)
        return scope

    def test_disabled_routes_refuse_before_hub_access(self):
        ns = self.routes()
        with self.assertRaises(Refusal) as failure: immediate(ns['recruit'](object()))
        self.assertEqual(failure.exception.status_code, 403)
        for action in ('activate', 'rehire', 'extend', 'unknown'):
            with self.assertRaises(Refusal) as failure: immediate(ns['contract']('worker', NS(action=action)))
            self.assertEqual(failure.exception.status_code, 403)

    def test_default_route_still_requires_a_real_recruiter(self):
        ns = self.routes(); ns['settings'] = settings(True); ns['hub'] = NS(agent_runner={})
        with self.assertRaises(Refusal) as failure:
            immediate(ns['recruit'](NS(paper='paper', repo=None)))
        self.assertEqual(failure.exception.status_code, 409)

    def test_contract_release_remains_available_on_gateway_and_runner(self):
        ns = self.routes(); sent = []
        async def send(*args): sent.append(args)
        ns['hub'] = NS(agent_runner={'worker': 'runner'}, send_runner=send)
        body = Result(action='release')
        self.assertEqual(immediate(ns['contract']('worker', body)), {'ok': True})
        self.assertEqual(sent[0][1]['action'], 'release')
        ns = extracted('labhq/runner/daemon.py', {'_contract_update'}, {'paper2agent': policy(), 'log': Mock()}, owner='Runner')
        registry = Mock(); runner = NS(s=settings(), registry=registry)
        for action in ('extend','rehire','activate'):
            ns['_contract_update'](runner, {'action': action})
        self.assertEqual(registry.mock_calls, [])
        ns['_contract_update'](runner, {'action': 'release', 'agent_id': 'worker'})
        registry.release.assert_called_once_with('worker')

    def test_runner_refuses_before_workspace_import_and_private_execution(self):
        ns = extracted('labhq/runner/daemon.py', {'run_task','_run_task','_recruit'},
                       {'paper2agent': policy(), 'TaskResult': Result, 'Event': Result}, owner='Runner')
        events = []
        async def emit(event): events.append(event)
        async def no_environment_refusal(task): return None
        runner = NS(s=settings(), emit=emit, _refuse_unprotected_environment=no_environment_refusal)
        for name in ('run_task','_run_task'):
            for task in (NS(id='t',agent_id='recruiter',request_id='r',meta={}),
                         NS(id='t',agent_id='worker',request_id='r',meta={'kind':'paper_skill'}),
                         NS(id='t',agent_id='worker',request_id='r',meta={'paper_skills':[{'status':'ready'}]})):
                result = immediate(ns[name](runner, task)); self.assertFalse(result.ok);self.assertTrue(result.cost_known)
        immediate(ns['_recruit'](runner, {'request_id':'r'}))
        self.assertEqual(events[-1].type, 'recruit.failed')
        self.assertEqual(len(events), 7)

    def test_plan_recruitment_disabled_before_any_hub_state_or_approval(self):
        ns = extracted('labhq/recruit/planning.py', {'prepare_plan','run_task'},
                       {'paper2agent': policy(), 'copy': copy, '_proposals': lambda p:p['recruit'], 'TaskResult': Result})
        plan = {'steps':[{'id':'s','agent_id':'worker'}], 'recruit':[{'paper':'p','step_ids':['s']}]}
        snapshot = copy.deepcopy(plan)
        out = immediate(ns['prepare_plan'](plan, 'r', NS(s=settings()), Mock(side_effect=AssertionError())))
        self.assertEqual(plan, snapshot);self.assertEqual(out['steps'], snapshot['steps'])
        self.assertEqual(out['recruit_results'][0]['error'], 'recruit_disabled')
        task=NS(id='t',agent_id='worker',meta={'kind':'plan_recruit'})
        result=immediate(ns['run_task'](NS(s=settings()),task));self.assertFalse(result.ok)

    def test_paper_plan_and_frozen_attachments_disabled_before_selection_or_dispatch(self):
        ns=extracted('labhq/recruit/paper_skills.py', {'prepare_plan','resolve_attachments','card_text'}, {'paper2agent':policy()})
        plan={'paper_skills':[{'paper_id':'pmid:1'}]}
        out=immediate(ns['prepare_plan'](plan,None,None,'r',settings(),None))
        self.assertEqual(out['paper_skill_results'][0]['skip_reason'],'recruit_disabled')
        self.assertIn('recruit_disabled', ns['card_text'](out))
        self.assertEqual(plan, {'paper_skills':[{'paper_id':'pmid:1'}]})
        for status in ('deferred','ready','reused'):
            result=immediate(ns['resolve_attachments']([{'status':status}],settings(),None))
            self.assertEqual(result[0]['status'],'unavailable')

    def test_deferred_batches_never_create_tasks_or_spawn_and_old_delivery_is_refused(self):
        ns=extracted('labhq/recruit/paper_skills.py', {'ApprovedBuilds'},
                     {'paper2agent':policy(),'copy':copy,'build_task':Mock(side_effect=AssertionError('no Task')),
                      'asyncio':NS(get_running_loop=lambda:NS(time=lambda:0),Semaphore=lambda n:object())})
        row={'selection_sha256':'a'*64,'status':'deferred','step_id':'s','agent_id':'worker'}
        batch=ns['ApprovedBuilds']([row],settings())
        spawn=Mock(side_effect=AssertionError('no spawn')); progress_rows=[]
        async def progress(*a):progress_rows.append(a)
        immediate(batch.start(None,spawn,None,progress,1));spawn.assert_not_called()
        self.assertEqual(batch.tasks,{});self.assertEqual(batch.disabled,{'a'*64:'recruit_disabled'})
        batch.delivered[('a'*64,'s','worker')]={'status':'ready'}
        self.assertEqual(immediate(batch.resolve([row]))[0]['error'],'recruit_disabled')

    def test_paper_build_prepare_and_finish_never_touch_cache_or_evidence_when_disabled(self):
        ns=extracted('labhq/recruit/paper_skills.py', {'prepare','finish'}, {'paper2agent':policy()}, owner='PaperSkillBuild')
        build=NS(runner=NS(s=settings()),task=object(),result=lambda row:row)
        prepared=ns['prepare'](build);self.assertEqual(prepared[2]['error'],'recruit_disabled')
        original=Result(cost_usd=.25,cost_known=True,provenance={'runs':{'spent':{}}})
        finished=ns['finish'](build,original)
        self.assertEqual(finished.structured['error'],'recruit_disabled')
        self.assertEqual((finished.cost_usd,finished.cost_known,finished.provenance),
                         (original.cost_usd,original.cost_known,original.provenance))


if __name__ == '__main__': unittest.main()
