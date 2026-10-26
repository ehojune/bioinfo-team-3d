"""Plan rehire uses mock latency only; no runner process or external conversion."""
import asyncio
import threading
import time
from types import SimpleNamespace

import pytest
import yaml

from labhq import durable
from labhq.recruit import planning
from labhq.registry import Registry
from tests.test_runner_contract_durable import runner, copies, wait_entered


@pytest.fixture
def plan_rehire(runner, monkeypatch):
    runner.s.recruit.agent_id = 'recruiter'
    runner.s.runner = SimpleNamespace(task_timeout_s=10)
    runner.s.policy = SimpleNamespace(budget=SimpleNamespace(reserve_before_dispatch=False))
    row = {'paper': 'PMID:12345678'}
    spec = runner.registry.get('c_x')
    spec.contract.paper = row['paper']
    spec.contract.kind = 'full'
    spec.contract.expires_at = 1
    spec.contract.status = 'active'
    spec.contract.verification = {'conversion_payload': planning.conversion_payload(row)}
    runner.registry.save_contract(spec)
    monkeypatch.setattr('labhq.doctor._paper2agent_row', lambda *args: {'status': 'pass'})
    monkeypatch.setattr(planning.paper2agent, 'probation', lambda *args: (True, {}))
    monkeypatch.setattr(planning.provenance, 'missing_hashes', lambda spec: [])
    monkeypatch.setattr(planning.provenance, 'provenance_changes', lambda spec: [])
    task = SimpleNamespace(id='plan-hire', agent_id='recruiter',
                           meta={'kind': 'plan_recruit', 'activate_on_pass': True, 'recruit': row})
    return runner, task


@pytest.mark.parametrize('active', [False, True])
async def test_plan_rehire_keeps_heartbeat_running(plan_rehire, monkeypatch, active):
    r, task = plan_rehire
    assert durable.shutdown(5)
    if active:
        await durable.run(durable.FILES, lambda: None)
    original = Registry._write_contract_copies
    trace = []

    def slow(self, spec, text):
        trace.append('write')
        time.sleep(.15)
        result = original(self, spec, text)
        trace.append('committed')
        return result

    monkeypatch.setattr(Registry, '_write_contract_copies', slow)
    timer = asyncio.get_running_loop().call_later(.02, trace.append, 'heartbeat')
    try:
        result = await planning.run_task(r, task)
        await asyncio.sleep(.01)
        assert trace.index('heartbeat') < trace.index('committed'), trace
        assert result.structured == {'status': 'active', 'agent_id': 'c_x', 'reused': True}
        assert len(r.receipts) == 1
    finally:
        timer.cancel()


@pytest.mark.parametrize('fail_at', [None, 1, 2])
@pytest.mark.parametrize('cancel', [False, True])
@pytest.mark.parametrize('interruptible_cleanup', [False, True])
async def test_rehire_commit_order_errors_and_repeated_cancel(plan_rehire, monkeypatch, fail_at, cancel,
                                                            interruptible_cleanup):
    import labhq.registry as registry_module
    r, task = plan_rehire
    original = registry_module.atomic_write_text
    before = r.registry.get('c_x').model_dump(mode='json')
    entered, release = threading.Event(), threading.Event()
    writes = []
    loop_thread = threading.get_ident()

    if interruptible_cleanup:
        async def legacy_wait_for(coro, timeout):
            # Model Python 3.10's interruptible wait_for cancellation cleanup on newer Python too.
            inner = asyncio.create_task(coro)
            try:
                return await asyncio.shield(inner)
            except asyncio.CancelledError:
                inner.cancel()
                await asyncio.shield(inner)
                raise

        monkeypatch.setattr(planning, 'asyncio', SimpleNamespace(
            create_task=asyncio.create_task, shield=asyncio.shield, wait_for=legacy_wait_for,
            CancelledError=asyncio.CancelledError, TimeoutError=asyncio.TimeoutError))

    def write(path, text):
        assert threading.get_ident() != loop_thread
        assert r.registry.get('c_x').model_dump(mode='json') == before
        assert r.receipts == []
        entered.set()
        assert release.wait(3)
        writes.append(path)
        if len(writes) == fail_at:
            raise OSError('mock rehire failure')
        original(path, text)

    monkeypatch.setattr(registry_module, 'atomic_write_text', write)
    pending = asyncio.create_task(planning.run_task(r, task))
    try:
        await wait_entered(entered)
        if cancel:
            for _ in range(3):
                pending.cancel()
                await asyncio.sleep(.001)
                assert not pending.done()
        assert r.receipts == []
    finally:
        release.set()
    if cancel and fail_at is None:
        with pytest.raises(asyncio.CancelledError):
            await pending
    else:
        result = await pending
        assert result.structured['status'] == ('failed' if fail_at else 'active')
    if fail_at:
        assert r.registry.get('c_x').model_dump(mode='json') == before
        assert r.receipts == []
        monkeypatch.setattr(registry_module, 'atomic_write_text', original)
        result = await planning.run_task(r, task)
        assert result.structured['status'] == 'active'
        live, talent = copies(r)
        assert live.read_bytes() == talent.read_bytes()
    else:
        live, talent = copies(r)
        assert writes == [live, talent]
        assert live.read_bytes() == talent.read_bytes()
        saved = r.registry.get('c_x').model_dump(mode='json')
        assert r.receipts == [{'c_x': saved}]
        restarted = Registry(r.registry.agents_dir, r.registry.talent_dir, r.registry.contract_dir)
        restarted.load()
        assert restarted.get('c_x').model_dump(mode='json') == saved


@pytest.mark.parametrize('earlier', ['update', 'hire', 'rehire'])
async def test_rehire_reads_after_earlier_same_id_write(plan_rehire, monkeypatch, earlier):
    r, _ = plan_rehire
    original = Registry._write_contract_copies
    entered, release = threading.Event(), threading.Event()
    count = 0

    def blocked(self, spec, text):
        nonlocal count
        count += 1
        if count == 1:
            entered.set()
            assert release.wait(3)
        return original(self, spec, text)

    monkeypatch.setattr(Registry, '_write_contract_copies', blocked)
    hired = r.registry.get('c_x').model_copy(deep=True)
    hired.name = 'new hire'
    if earlier == 'update':
        first = asyncio.create_task(r._on_message({'type': 'contract.update', 'action': 'extend',
                                                  'agent_id': 'c_x', 'days': 2}))
    elif earlier == 'hire':
        first = asyncio.create_task(r.registry.asave_contract(hired))
    else:
        first = asyncio.create_task(r.registry.arehire('x', 2))
    second = None
    try:
        await wait_entered(entered)
        second = asyncio.create_task(r.registry.arehire('x', 3, on_commit=r._send_roster))
        await asyncio.sleep(.01)
    finally:
        release.set()
    await asyncio.gather(first, second)
    saved = r.registry.get('c_x')
    assert saved.name == ('new hire' if earlier == 'hire' else 'X')
    assert saved.contract.expires_at - saved.contract.hired_at == 3 * 86400
    live, talent = copies(r)
    assert live.read_bytes() == talent.read_bytes()
    assert yaml.safe_load(live.read_text(encoding='utf8'))['contract']['expires_at'] == saved.contract.expires_at
    assert r.receipts[-1]['c_x'] == saved.model_dump(mode='json')


@pytest.mark.parametrize('later', ['update', 'hire'])
async def test_later_same_id_publication_wins(plan_rehire, monkeypatch, later):
    r, _ = plan_rehire
    original = Registry._write_contract_copies
    entered, release = threading.Event(), threading.Event()
    count = 0

    def blocked(self, spec, text):
        nonlocal count
        count += 1
        if count == 1:
            entered.set()
            assert release.wait(3)
        return original(self, spec, text)

    monkeypatch.setattr(Registry, '_write_contract_copies', blocked)
    first = asyncio.create_task(r.registry.arehire('x', 3, on_commit=r._send_roster))
    second = None
    try:
        await wait_entered(entered)
        if later == 'update':
            second = asyncio.create_task(r._on_message({'type': 'contract.update', 'action': 'extend',
                                                       'agent_id': 'c_x', 'days': 2}))
        else:
            hired = r.registry.get('c_x').model_copy(deep=True)
            hired.name = 'later hire'
            second = asyncio.create_task(r.registry.asave_contract(hired))
        await asyncio.sleep(.01)
    finally:
        release.set()
    rehired, _ = await asyncio.gather(first, second)
    saved = r.registry.get('c_x')
    if later == 'update':
        assert saved.contract.expires_at == rehired.contract.expires_at + 2 * 86400
        assert r.receipts[-1]['c_x'] == saved.model_dump(mode='json')
    else:
        assert saved.name == 'later hire'
    live, talent = copies(r)
    assert live.read_bytes() == talent.read_bytes()
    assert yaml.safe_load(live.read_text(encoding='utf8'))['name'] == saved.name


async def test_rehire_read_verification_and_snapshot_stay_on_writer(plan_rehire, monkeypatch):
    r, _ = plan_rehire
    original = Registry.rehire
    loop_thread = threading.get_ident()
    entered, release = threading.Event(), threading.Event()

    def blocked(self, slug, days, **kwargs):
        assert threading.get_ident() != loop_thread
        assert self is not r.registry
        entered.set()
        assert release.wait(3)
        return original(self, slug, days, **kwargs)

    monkeypatch.setattr(Registry, 'rehire', blocked)
    old_live, old_talent = copies(r)
    pending = asyncio.create_task(r.registry.arehire('x', 3, on_commit=r._send_roster))
    try:
        await wait_entered(entered)
        r.registry.contract_dir = old_live.parent / 'changed'
        r.registry.talent_dir = old_talent.parent / 'changed'
        r.registry.get('c_x').name = 'mutated loop object'
    finally:
        release.set()
    saved = await pending
    assert saved.name == 'X'
    assert old_live.read_bytes() == old_talent.read_bytes()
    assert r.registry.paths['c_x'] == old_live
    assert r.receipts[-1]['c_x']['name'] == 'X'


async def test_newer_sync_rehire_survives_queued_async_publication(plan_rehire, monkeypatch):
    r, _ = plan_rehire
    original = Registry._write_contract_copies
    entered, release = threading.Event(), threading.Event()
    loop_thread = threading.get_ident()

    def blocked(self, spec, text):
        if threading.get_ident() == loop_thread:
            release.set()
        else:
            entered.set()
            assert release.wait(3)
        return original(self, spec, text)

    monkeypatch.setattr(Registry, '_write_contract_copies', blocked)
    pending = asyncio.create_task(r.registry.arehire('x', 2, on_commit=r._send_roster))
    try:
        await wait_entered(entered)
        newer = r.registry.rehire('x', 3)
    finally:
        release.set()
    await pending
    assert r.registry.get('c_x') is newer
    live, talent = copies(r)
    assert live.read_bytes() == talent.read_bytes()
    assert r.receipts[-1]['c_x'] == newer.model_dump(mode='json')


async def test_cancel_before_submission_does_not_rehire(plan_rehire, monkeypatch):
    r, _ = plan_rehire
    monkeypatch.setattr(Registry, 'rehire', lambda *args: pytest.fail('unexpected rehire'))
    async with r.registry._rehire_lock:
        pending = asyncio.create_task(r.registry.arehire('x', 2))
        await asyncio.sleep(0)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
    assert r.receipts == []


async def test_earlier_queued_hire_callback_does_not_suppress_rehire(plan_rehire, monkeypatch):
    r, _ = plan_rehire
    loop = asyncio.get_running_loop()
    schedule = loop.call_soon_threadsafe
    callbacks = []

    def defer(callback, *args, **kwargs):
        if callback == r.registry._remember:
            callbacks.append((callback, args))
            return None
        return schedule(callback, *args, **kwargs)

    monkeypatch.setattr(loop, 'call_soon_threadsafe', defer)
    hired = r.registry.get('c_x').model_copy(deep=True)
    hired.name = 'earlier hire'
    await r.registry.asave_contract(hired)
    monkeypatch.setattr(loop, 'call_soon_threadsafe', schedule)
    assert len(callbacks) == 1
    original = Registry._write_contract_copies
    entered, release = threading.Event(), threading.Event()

    def blocked(self, spec, text):
        entered.set()
        assert release.wait(3)
        return original(self, spec, text)

    monkeypatch.setattr(Registry, '_write_contract_copies', blocked)
    pending = asyncio.create_task(r.registry.arehire('x', 3, on_commit=r._send_roster))
    try:
        await wait_entered(entered)
        callback, args = callbacks[0]
        callback(*args)  # older FIFO publication is serviced after rehire's snapshot
    finally:
        release.set()
    saved = await pending
    assert r.registry.get('c_x') is saved
    assert saved.name == 'earlier hire'
    assert r.receipts[-1]['c_x'] == saved.model_dump(mode='json')


@pytest.mark.parametrize('invalid', ['probation', 'payload', 'kind', 'hash', 'paper'])
async def test_rehire_rechecks_actual_writer_snapshot(plan_rehire, monkeypatch, invalid):
    r, task = plan_rehire
    entered, release = threading.Event(), threading.Event()

    def block():
        entered.set()
        assert release.wait(3)

    blocker = durable.submit(durable.FILES, block)
    await wait_entered(entered)
    replacement = r.registry.get('c_x').model_copy(deep=True)
    if invalid == 'probation':
        replacement.contract.status = 'probation'
    elif invalid == 'payload':
        replacement.contract.verification['conversion_payload'] = {'different': True}
    elif invalid == 'kind':
        replacement.contract.kind = 'reader'
    elif invalid == 'paper':
        replacement.contract.paper = 'PMID:87654321'
    else:
        replacement.name = 'missing hashes'
        monkeypatch.setattr(planning.provenance, 'missing_hashes', lambda spec: spec.name == 'missing hashes')
    hire = asyncio.create_task(r.registry.asave_contract(replacement))
    await asyncio.sleep(0)  # queue replacement after blocker, before rehire's barrier
    pending = asyncio.create_task(planning.run_task(r, task))
    try:
        await asyncio.sleep(.01)  # preflight still sees the original talent file
        assert not pending.done()
    finally:
        release.set()
    await asyncio.wrap_future(blocker)
    await hire
    result = await pending
    assert result.structured['status'] == 'failed'
    assert r.receipts == []
    assert r.registry.get('c_x').model_dump(mode='json') == replacement.model_dump(mode='json')
    live, talent = copies(r)
    assert live.read_bytes() == talent.read_bytes()
    assert yaml.safe_load(live.read_text(encoding='utf8'))['contract']['expires_at'] == 1
