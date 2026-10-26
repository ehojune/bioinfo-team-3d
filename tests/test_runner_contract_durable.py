"""Runner contract boundary: mock slow writes, no server or staff process."""
import asyncio
import threading
import time
from types import SimpleNamespace

import pytest
import yaml

from labhq import durable
from labhq.models import AgentSpec, ContractInfo, Employment
from labhq.registry import Registry
from labhq.runner.daemon import Runner


@pytest.fixture
def runner(tmp_path):
    r = Runner.__new__(Runner)
    r.s = SimpleNamespace(recruit=SimpleNamespace(enabled=True, default_ttl_days=1))
    r.registry = Registry(tmp_path / 'agents', tmp_path / 'talent', tmp_path / 'contracts')
    r._contract_lock = asyncio.Lock()
    now = time.time()
    spec = AgentSpec(id='c_x', name='X', role='test', employment=Employment.contract,
                     contract=ContractInfo(hired_at=now, expires_at=now + 3600,
                                           status='probation', talent_dir=str(tmp_path / 'talent/x')))
    r.registry.save_contract(spec)
    r.receipts = []
    r._send_roster = lambda: r.receipts.append({key: spec.model_dump(mode='json')
                                               for key, spec in r.registry.agents.items()})
    yield r
    assert durable.shutdown(5)


@pytest.mark.parametrize('active', [False, True])
async def test_slow_contract_allows_heartbeat(runner, monkeypatch, active):
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
        await runner._on_message({'type': 'contract.update', 'action': 'activate', 'agent_id': 'c_x'})
        await asyncio.sleep(.01)
        assert trace.index('heartbeat') < trace.index('committed'), trace
        assert runner.receipts[-1]['c_x']['contract']['status'] == 'active'
    finally:
        timer.cancel()


def copies(runner):
    return (runner.registry.contract_dir / 'c_x.yaml', runner.registry.talent_dir / 'x/contract.yaml')


async def wait_entered(entered):
    for _ in range(1000):
        if entered.is_set():
            return
        await asyncio.sleep(.001)
    pytest.fail('mock writer did not enter')


@pytest.mark.parametrize('action', ['activate', 'extend', 'release', 'rehire'])
async def test_lifecycle_publishes_after_files(runner, monkeypatch, action):
    import labhq.registry as registry_module
    original = registry_module.atomic_write_text
    writes = []
    before = runner.registry.get('c_x').model_dump(mode='json')

    def write(path, text):
        assert runner.registry.get('c_x').model_dump(mode='json') == before
        assert runner.receipts == []
        original(path, text)
        writes.append(path)

    monkeypatch.setattr(registry_module, 'atomic_write_text', write)
    await runner._on_message({'type': 'contract.update', 'action': action, 'agent_id': 'c_x',
                              'slug': 'x', 'days': 2})
    live, talent = copies(runner)
    assert len(runner.receipts) == 1
    if action == 'release':
        assert writes == [talent]
        assert not live.exists()
        assert runner.receipts == [{}]
        assert yaml.safe_load(talent.read_text(encoding='utf8'))['contract']['status'] == 'archived'
    else:
        assert writes == [live, talent]
        saved = runner.registry.get('c_x').model_dump(mode='json')
        assert AgentSpec.model_validate(yaml.safe_load(live.read_text(encoding='utf8'))).model_dump(mode='json') == saved
        assert AgentSpec.model_validate(yaml.safe_load(talent.read_text(encoding='utf8'))).model_dump(mode='json') == saved
        assert runner.receipts == [{'c_x': saved}]
        if action == 'extend':
            assert saved['contract']['expires_at'] == before['contract']['expires_at'] + 2 * 86400


@pytest.mark.parametrize('fail_at', [1, 2])
@pytest.mark.parametrize('cancel', [False, True])
async def test_failure_keeps_registry_and_no_roster_then_retry(runner, monkeypatch, fail_at, cancel):
    import labhq.registry as registry_module
    original = registry_module.atomic_write_text
    entered, release = threading.Event(), threading.Event()
    count = 0
    before = runner.registry.get('c_x').model_dump(mode='json')

    def write(path, text):
        nonlocal count
        count += 1
        if count == fail_at:
            entered.set()
            assert release.wait(3)
            raise OSError('mock contract write failure')
        original(path, text)

    monkeypatch.setattr(registry_module, 'atomic_write_text', write)
    msg = {'type': 'contract.update', 'action': 'activate', 'agent_id': 'c_x'}
    task = asyncio.create_task(runner._on_message(msg))
    try:
        await wait_entered(entered)
        if cancel:
            for _ in range(3):
                task.cancel()
                await asyncio.sleep(0)
                assert not task.done()
        assert runner.registry.get('c_x').model_dump(mode='json') == before
        assert runner.receipts == []
    finally:
        release.set()
    with pytest.raises(OSError, match='mock contract write failure'):
        await task
    assert runner.registry.get('c_x').model_dump(mode='json') == before
    assert runner.receipts == []
    monkeypatch.setattr(registry_module, 'atomic_write_text', original)
    await runner._on_message(msg)
    live, talent = copies(runner)
    assert live.read_bytes() == talent.read_bytes()
    assert runner.receipts[-1]['c_x']['contract']['status'] == 'active'


async def test_repeated_cancel_snapshot_and_competing_extensions(runner, monkeypatch):
    original = Registry._write_contract_copies
    entered, release = threading.Event(), threading.Event()
    initial = runner.registry.get('c_x').contract.expires_at
    writes = []

    def blocked(self, spec, text):
        if not writes:
            entered.set()
            assert release.wait(3)
        writes.append(spec.contract.expires_at)
        return original(self, spec, text)

    monkeypatch.setattr(Registry, '_write_contract_copies', blocked)
    msg = {'type': 'contract.update', 'action': 'extend', 'agent_id': 'c_x', 'days': 1}
    first = asyncio.create_task(runner._on_message(msg))
    second = None
    try:
        await wait_entered(entered)
        msg['days'] = 100  # caller mutation cannot change the queued snapshot
        second = asyncio.create_task(runner._on_message({**msg, 'days': 2}))
        await asyncio.sleep(0)
        for _ in range(3):
            first.cancel()
            await asyncio.sleep(0)
            assert not first.done()
        assert runner.registry.get('c_x').contract.expires_at == initial
        assert runner.receipts == []
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await first
    await second
    assert writes == [initial + 86400, initial + 3 * 86400]
    assert [r['c_x']['contract']['expires_at'] for r in runner.receipts] == writes
    live, talent = copies(runner)
    assert live.read_bytes() == talent.read_bytes()
    restarted = Registry(runner.registry.agents_dir, runner.registry.talent_dir, runner.registry.contract_dir)
    restarted.load()
    assert restarted.get('c_x').contract.expires_at == writes[-1]


async def test_rehire_warning_uses_event_loop_after_roster(runner, monkeypatch):
    from labhq.recruit import provenance
    monkeypatch.setattr(provenance, 'provenance_changes', lambda spec: ['skill'])
    loop_thread = threading.get_ident()
    warnings = []

    async def emit(event):
        assert threading.get_ident() == loop_thread
        assert runner.receipts[-1]['c_x']['contract']['status'] == 'active'
        warnings.append(event)

    runner.emit = emit
    await runner._on_message({'type': 'contract.update', 'action': 'rehire', 'slug': 'x'})
    assert warnings[0].data['changed'] == ['skill']


@pytest.mark.parametrize('action', ['activate', 'extend', 'release', 'rehire'])
async def test_cancel_waits_for_lifecycle_and_roster(runner, monkeypatch, action):
    import labhq.registry as registry_module
    original = registry_module.atomic_write_text
    entered, release = threading.Event(), threading.Event()
    before = runner.registry.get('c_x').model_dump(mode='json')

    def write(path, text):
        entered.set()
        assert release.wait(3)
        original(path, text)

    monkeypatch.setattr(registry_module, 'atomic_write_text', write)
    task = asyncio.create_task(runner._on_message({'type': 'contract.update', 'action': action,
                                                  'agent_id': 'c_x', 'slug': 'x'}))
    try:
        await wait_entered(entered)
        for _ in range(3):
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
        assert runner.registry.get('c_x').model_dump(mode='json') == before
        assert runner.receipts == []
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    live, talent = copies(runner)
    assert len(runner.receipts) == 1
    if action == 'release':
        assert not live.exists() and runner.receipts == [{}]
    else:
        assert live.read_bytes() == talent.read_bytes()
        assert runner.receipts[-1]['c_x'] == runner.registry.get('c_x').model_dump(mode='json')


async def test_disabled_update_keeps_objects_and_release_still_works(runner):
    runner.s.recruit.enabled = False
    spec = runner.registry.get('c_x')
    for action in ('activate', 'extend', 'rehire'):
        await runner._on_message({'type': 'contract.update', 'action': action,
                                  'agent_id': 'c_x', 'slug': 'x'})
        assert runner.registry.get('c_x') is spec
    await runner._on_message({'type': 'contract.update', 'action': 'release', 'agent_id': 'c_x'})
    assert runner.receipts[-1] == {}


async def test_cancel_while_waiting_for_lock_does_not_write(runner, monkeypatch):
    monkeypatch.setattr(Registry, '_write_contract_copies', lambda *args: pytest.fail('unexpected write'))
    async with runner._contract_lock:
        task = asyncio.create_task(runner._on_message({'type': 'contract.update', 'action': 'activate',
                                                       'agent_id': 'c_x'}))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert runner.receipts == []


async def test_update_snapshots_after_earlier_same_id_recruitment(runner, monkeypatch):
    original = Registry._write_contract_copies
    entered, release = threading.Event(), threading.Event()
    hired = runner.registry.get('c_x').model_copy(deep=True)
    hired.name = 'new hire'
    hired.contract.source_commit = 'a' * 40
    hired.contract.verification = {'new': 'provenance'}

    def blocked(self, spec, text):
        if spec.name == 'new hire' and not entered.is_set():
            entered.set()
            assert release.wait(3)
        return original(self, spec, text)

    monkeypatch.setattr(Registry, '_write_contract_copies', blocked)
    hire = asyncio.create_task(runner.registry.asave_contract(hired))
    update = None
    try:
        await wait_entered(entered)
        update = asyncio.create_task(runner._on_message({'type': 'contract.update', 'action': 'activate',
                                                         'agent_id': 'c_x'}))
        await asyncio.sleep(.02)
    finally:
        release.set()
    await asyncio.gather(hire, update)
    saved = runner.registry.get('c_x')
    assert saved.name == 'new hire'
    assert saved.contract.source_commit == 'a' * 40
    assert saved.contract.verification == {'new': 'provenance'}
    assert saved.contract.status == 'active'
    live, talent = copies(runner)
    assert live.read_bytes() == talent.read_bytes()
    assert yaml.safe_load(live.read_text(encoding='utf8'))['name'] == 'new hire'


async def test_later_hire_publication_cannot_be_overwritten_by_update_waiter(runner, monkeypatch):
    original = Registry._write_contract_copies
    entered, release, hired_written = threading.Event(), threading.Event(), threading.Event()
    hired = runner.registry.get('c_x').model_copy(deep=True)
    hired.name = 'later hire'
    hired.contract.source_commit = 'b' * 40

    def blocked(self, spec, text):
        if spec.name != 'later hire':
            entered.set()
            assert release.wait(3)
        result = original(self, spec, text)
        if spec.name == 'later hire':
            hired_written.set()
        return result

    monkeypatch.setattr(Registry, '_write_contract_copies', blocked)
    update = asyncio.create_task(runner._on_message({'type': 'contract.update', 'action': 'activate',
                                                     'agent_id': 'c_x'}))
    hire = None
    try:
        await wait_entered(entered)
        hire = asyncio.create_task(runner.registry.asave_contract(hired))
        await asyncio.sleep(0)  # queue the later FILES job
    finally:
        release.set()
    # Let the writer finish both files before the loop processes its callbacks. This controls the callback race,
    # not a disk latency test: publication must follow the lane even when the loop is busy with other callbacks.
    assert hired_written.wait(3)
    await asyncio.gather(update, hire)
    assert runner.registry.get('c_x').name == 'later hire'
    assert runner.registry.get('c_x').contract.source_commit == 'b' * 40
    live, talent = copies(runner)
    assert live.read_bytes() == talent.read_bytes()
    assert yaml.safe_load(live.read_text(encoding='utf8'))['name'] == 'later hire'


async def test_sync_plan_rehire_is_preserved_when_update_callback_is_queued(runner, monkeypatch):
    original = Registry._write_contract_copies
    entered, release = threading.Event(), threading.Event()
    loop_thread = threading.get_ident()

    def blocked(self, spec, text):
        if threading.get_ident() == loop_thread:
            # Registry.rehire reads its spec before synchronous writes wait behind the existing FILES job.
            release.set()
        else:
            entered.set()
            assert release.wait(3)
        return original(self, spec, text)

    monkeypatch.setattr(Registry, '_write_contract_copies', blocked)
    update = asyncio.create_task(runner._on_message({'type': 'contract.update', 'action': 'activate',
                                                     'agent_id': 'c_x'}))
    try:
        await wait_entered(entered)
        rehired = runner.registry.rehire('x', 2)
    finally:
        release.set()
    await update
    assert runner.registry.get('c_x') is rehired
    assert runner.receipts[-1]['c_x']['contract']['expires_at'] == rehired.contract.expires_at
    live, talent = copies(runner)
    assert live.read_bytes() == talent.read_bytes()
    assert yaml.safe_load(live.read_text(encoding='utf8'))['contract']['expires_at'] == rehired.contract.expires_at
