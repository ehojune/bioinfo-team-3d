"""Preflight reads run on FILES with mock latency; no process/network."""
import asyncio
from pathlib import Path
import threading
import time

import pytest

from labhq.recruit import planning
from labhq import durable
from labhq.registry import Registry
from tests.test_runner_contract_durable import runner
from tests.test_runner_contract_durable import wait_entered
from tests.test_runner_plan_rehire_durable import plan_rehire


@pytest.mark.parametrize('outcome', ['active', 'hash_rejected', 'read_error'])
@pytest.mark.parametrize('delay', [0, .2])
async def test_preflight_read_heartbeat_and_decision(plan_rehire, monkeypatch, outcome, delay):
    r, task = plan_rehire
    talent = r.registry.talent_dir / 'x' / 'contract.yaml'
    live = r.registry.contract_dir / 'c_x.yaml'
    before = (live.read_bytes(), talent.read_bytes())
    original = Path.read_text
    loop = asyncio.get_running_loop()
    loop_thread = threading.get_ident()
    trace, times = [], {}
    delayed = False
    timer = None

    def heartbeat():
        times['heartbeat'] = time.monotonic()
        trace.append('heartbeat')

    def schedule_heartbeat():
        nonlocal timer
        timer = loop.call_later(.02, heartbeat)

    def read(path, *args, **kwargs):
        nonlocal delayed, timer
        if path == talent and not delayed:
            delayed = True
            assert threading.get_ident() != loop_thread
            loop.call_soon_threadsafe(schedule_heartbeat)
            times['enter'] = time.monotonic()
            trace.append('read-enter')
            time.sleep(delay)  # model a slow file read; no real disk latency claim
            trace.append('read-done')
            if outcome == 'read_error':
                raise OSError('mock talent read error')
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'read_text', read)
    if outcome == 'hash_rejected':
        monkeypatch.setattr(planning.provenance, 'provenance_changes', lambda spec: ['skill'])
    try:
        result = await planning.run_task(r, task)
        if outcome == 'active':
            assert result.structured == {'status': 'active', 'agent_id': 'c_x', 'reused': True}
            assert len(r.receipts) == 1
            assert r.registry.get('c_x').contract.expires_at > time.time()
        else:
            expected_error = 'OSError' if outcome == 'read_error' else '인재풀 수습·hash 확인 실패'
            assert result.structured == {'status': 'failed', 'error': expected_error}
            assert r.receipts == []
            assert (live.read_bytes(), talent.read_bytes()) == before
        trace.append('decision-preserved')
        await asyncio.sleep(.03)
        assert delayed
        print(f'outcome={outcome} delay={delay} decision=PASS '
              f'heartbeat_ms={(times["heartbeat"] - times["enter"]) * 1000:.2f} trace={trace}')
        if delay:
            assert trace.index('heartbeat') < trace.index('read-done'), trace
    finally:
        if timer is not None:
            timer.cancel()


@pytest.mark.parametrize('phase', ['probation', 'provenance'])
async def test_verification_heartbeat_before_read_finishes(plan_rehire, monkeypatch, phase):
    r, task = plan_rehire
    loop = asyncio.get_running_loop()
    loop_thread = threading.get_ident()
    beat = threading.Event()
    checked = []

    def check(*args):
        assert threading.get_ident() != loop_thread
        checked.append(phase)
        loop.call_soon_threadsafe(beat.set)
        assert beat.wait(3), 'loop did not serve heartbeat during verification'
        return (True, {}) if phase == 'probation' else []

    if phase == 'probation':
        monkeypatch.setattr(planning.paper2agent, 'probation', check)
    else:
        monkeypatch.setattr(planning.provenance, 'provenance_changes', check)
    result = await planning.run_task(r, task)
    assert result.structured == {'status': 'active', 'agent_id': 'c_x', 'reused': True}
    assert len(checked) >= 2  # preflight and writer revalidation


@pytest.mark.parametrize('interrupt', ['cancel', 'timeout'])
async def test_cancelled_preflight_never_starts_rehire(plan_rehire, monkeypatch, interrupt):
    r, task = plan_rehire
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    before = r.registry.get('c_x').model_dump(mode='json')
    original = Registry.talent_pool

    def read(self):
        entered.set()
        assert release.wait(3)
        try:
            return original(self)
        finally:
            finished.set()

    monkeypatch.setattr(Registry, 'talent_pool', read)
    monkeypatch.setattr(Registry, 'arehire', lambda *a, **k: pytest.fail('rehire after cancellation'))
    if interrupt == 'timeout':
        r.s.runner.task_timeout_s = .05
    pending = asyncio.create_task(planning.run_task(r, task))
    try:
        await wait_entered(entered)
        if interrupt == 'cancel':
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
        else:
            result = await pending
            assert result.structured == {'status': 'timed_out'}
    finally:
        release.set()
        await durable.run(durable.FILES, lambda: None)
    assert finished.is_set()
    assert r.registry.get('c_x').model_dump(mode='json') == before
    assert r.receipts == []


async def test_preflight_waits_for_earlier_writer(plan_rehire, monkeypatch):
    r, task = plan_rehire
    entered, release = threading.Event(), threading.Event()
    original = Registry.talent_pool
    trace = []
    updated = r.registry.get('c_x').model_copy(deep=True)
    updated.name = 'earlier writer'

    def write():
        entered.set()
        assert release.wait(3)
        staged = Registry(r.registry.agents_dir, r.registry.talent_dir, r.registry.contract_dir)
        staged.save_contract(updated)
        trace.append('written')

    def read(self):
        assert self is not r.registry
        assert self.agents == {} and self.paths == {}
        assert trace == ['written']
        trace.append('read')
        return original(self)

    monkeypatch.setattr(Registry, 'talent_pool', read)
    first = asyncio.create_task(durable.run(durable.FILES, write))
    pending = None
    try:
        await wait_entered(entered)
        pending = asyncio.create_task(planning.run_task(r, task))
        await asyncio.sleep(.01)
        assert trace == []
    finally:
        release.set()
        await first
    result = await pending
    assert result.structured['status'] == 'active'
    assert r.registry.get('c_x').name == 'earlier writer'


async def test_writer_rechecks_hash_changed_after_preflight(plan_rehire, monkeypatch):
    r, task = plan_rehire
    original = Registry.arehire
    before = r.registry.get('c_x').model_dump(mode='json')
    live = r.registry.contract_dir / 'c_x.yaml'
    talent = r.registry.talent_dir / 'x' / 'contract.yaml'
    files = (live.read_bytes(), talent.read_bytes())

    async def changed(self, *args, **kwargs):
        monkeypatch.setattr(planning.provenance, 'provenance_changes', lambda spec: ['skill'])
        return await original(self, *args, **kwargs)

    monkeypatch.setattr(Registry, 'arehire', changed)
    result = await planning.run_task(r, task)
    assert result.structured == {'status': 'failed', 'error': 'ValueError'}
    assert r.registry.get('c_x').model_dump(mode='json') == before
    assert (live.read_bytes(), talent.read_bytes()) == files
    assert r.receipts == []


@pytest.mark.parametrize('status', ['active', 'probation'])
async def test_live_candidate_decision_stays_on_loop(plan_rehire, monkeypatch, status):
    r, task = plan_rehire
    current = r.registry.get('c_x')
    current.contract.expires_at = time.time() + 86400
    current.contract.status = status
    loop_thread = threading.get_ident()
    original = r._send_roster

    def publish():
        assert threading.get_ident() == loop_thread
        original()

    monkeypatch.setattr(r, '_send_roster', publish)
    monkeypatch.setattr(Registry, 'arehire', lambda *a, **k: pytest.fail('active candidate rehired'))
    result = await planning.run_task(r, task)
    if status == 'active':
        assert result.structured == {'status': 'active', 'agent_id': 'c_x', 'reused': True}
        assert len(r.receipts) == 1
    else:
        assert result.structured['status'] == 'failed'
        assert r.receipts == []
    assert r.registry.get('c_x') is current


@pytest.mark.parametrize('reason', ['probation_failed', 'probation_status', 'missing_hashes'])
async def test_preflight_rejections_preserve_files(plan_rehire, monkeypatch, reason):
    r, task = plan_rehire
    if reason == 'probation_status':
        r.registry.get('c_x').contract.status = 'probation'
        r.registry.save_contract(r.registry.get('c_x'))
    elif reason == 'probation_failed':
        monkeypatch.setattr(planning.paper2agent, 'probation', lambda *a: (False, {}))
    else:
        monkeypatch.setattr(planning.provenance, 'missing_hashes', lambda spec: ['skill'])
    live = r.registry.contract_dir / 'c_x.yaml'
    talent = r.registry.talent_dir / 'x' / 'contract.yaml'
    before = (live.read_bytes(), talent.read_bytes())
    monkeypatch.setattr(Registry, 'arehire', lambda *a, **k: pytest.fail('rejected candidate rehired'))
    result = await planning.run_task(r, task)
    assert result.structured['status'] == 'failed'
    assert result.structured['error'] == '인재풀 수습·hash 확인 실패'
    assert (live.read_bytes(), talent.read_bytes()) == before
    assert r.receipts == []


@pytest.mark.parametrize('pool', ['empty', 'wrong_paper', 'wrong_payload', 'wrong_kind', 'no_contract'])
async def test_no_matching_candidate_still_uses_conversion(plan_rehire, monkeypatch, pool):
    r, task = plan_rehire
    task.request_id = 'mock-request'
    task.budget_usd = 1
    task.request_budget_remaining_usd = 1
    candidate = r.registry.get('c_x').model_copy(deep=True)
    if pool == 'wrong_paper':
        candidate.contract.paper = 'PMID:99999999'
    elif pool == 'wrong_payload':
        candidate.contract.verification['conversion_payload'] = {}
    elif pool == 'wrong_kind':
        candidate.contract.kind = 'skill'
    elif pool == 'no_contract':
        candidate.contract = None
    loop_thread = threading.get_ident()
    converted = []

    def read(self):
        assert threading.get_ident() != loop_thread
        return [] if pool == 'empty' else [candidate]

    class Conversion:
        def __init__(self, runner, payload):
            assert threading.get_ident() == loop_thread
            converted.append(payload)

        async def run(self):
            return None

    monkeypatch.setattr(Registry, 'talent_pool', read)
    monkeypatch.setattr(Registry, 'arehire', lambda *a, **k: pytest.fail('unmatched candidate rehired'))
    monkeypatch.setattr(planning.paper2agent, 'Recruitment', Conversion)
    result = await planning.run_task(r, task)
    assert len(converted) == 1
    assert result.structured['status'] == 'failed'  # mock conversion returns no active hire
