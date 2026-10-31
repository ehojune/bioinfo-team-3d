import copy
import json
from pathlib import Path

import pytest

from labhq.runner.boundary_observer import ReplayStore, observe, verify_event

FIXTURE = json.loads((Path(__file__).parent / 'fixtures/process_boundary_seal/lifecycle-v1.json').read_text(encoding='utf-8'))
KEY = FIXTURE['test_key_utf8'].encode()


def collector(phase, identity):
    return dict(run_id='fixture-run', nonce='fixture-nonce', phase=phase, identity=identity,
                processes=[identity] if phase == 'launch' else [], resources=[], provenance='fixture-collector',
                sequence=1 if phase == 'launch' else 2, complete=True)


def invoke(events, store, collect=collector):
    return observe(events, run_id='fixture-run', nonce='fixture-nonce', producer_key_id='fixture-producer',
                   producer_key=KEY, observer_key_id='fixture-observer', observer_key=b'observer-fixture-key',
                   replay_store=store, collector=collect)


def test_vector_and_persistent_replay(tmp_path):
    events = copy.deepcopy(FIXTURE['events'])
    result = invoke(events, ReplayStore(tmp_path / 'replay.sqlite'))
    assert result['scope'] == 'synthetic_only'
    assert result['auth']['tag']
    with pytest.raises(ValueError, match='replay'):
        invoke(events, ReplayStore(tmp_path / 'replay.sqlite'))


@pytest.mark.parametrize('mutation', ['tag', 'run', 'sequence', 'identity', 'extra'])
def test_invalid_events_fail_closed(tmp_path, mutation):
    events = copy.deepcopy(FIXTURE['events'])
    if mutation == 'tag': events[0]['auth']['tag'] = '0' * 64
    elif mutation == 'run': events[0]['run_id'] = 'other'
    elif mutation == 'sequence': events.reverse()
    elif mutation == 'identity': events[1]['identity']['birth_id'] = 'other'
    else: events[0]['extra'] = True
    with pytest.raises(ValueError):
        invoke(events, ReplayStore(tmp_path / 'replay.sqlite'))


@pytest.mark.parametrize('problem', ['missing', 'remaining', 'timeout'])
def test_bad_collection_consumes_nonce(tmp_path, problem):
    store = ReplayStore(tmp_path / 'replay.sqlite')
    def bad(phase, identity):
        if problem == 'timeout': raise TimeoutError()
        result = collector(phase, identity)
        if problem == 'missing': del result['provenance']
        if problem == 'remaining' and phase == 'termination': result['processes'] = [identity]
        return result
    with pytest.raises((ValueError, TimeoutError)):
        invoke(copy.deepcopy(FIXTURE['events']), store, bad)
    with pytest.raises(ValueError, match='replay'):
        invoke(copy.deepcopy(FIXTURE['events']), store)


def test_collector_runs_between_stream_events(tmp_path):
    phases = []
    def stream():
        yield FIXTURE['events'][0]
        assert phases == ['launch']
        yield FIXTURE['events'][1]
    def collect(phase, identity):
        phases.append(phase)
        return collector(phase, identity)
    invoke(stream(), ReplayStore(tmp_path / 'replay.sqlite'), collect)
    assert phases == ['launch', 'termination']


@pytest.mark.parametrize('problem', ['incomplete', 'wrong_order', 'changed_source', 'missing_launch'])
def test_invalid_inventory_fails_closed(tmp_path, problem):
    def collect(phase, identity):
        snap = collector(phase, identity)
        if problem == 'incomplete': snap['complete'] = False
        if problem == 'wrong_order': snap['sequence'] = 1
        if problem == 'changed_source' and phase == 'termination': snap['provenance'] = 'other'
        if problem == 'missing_launch' and phase == 'launch': snap['processes'] = []
        return snap
    with pytest.raises(ValueError):
        invoke(copy.deepcopy(FIXTURE['events']), ReplayStore(tmp_path / 'replay.sqlite'), collect)


def test_stream_cannot_change_authenticated_launch_identity(tmp_path):
    events = copy.deepcopy(FIXTURE['events'])
    def stream():
        yield events[0]
        events[0]['identity']['birth_id'] = 'changed'
        yield events[1]
    result = invoke(stream(), ReplayStore(tmp_path / 'replay.sqlite'))
    assert result['identity'] == FIXTURE['events'][0]['identity']


def test_later_collection_cannot_change_earlier_snapshot(tmp_path):
    saved = []
    def collect(phase, identity):
        if saved:
            saved[0]['complete'] = False
            saved[0]['processes'].clear()
        result = collector(phase, identity)
        saved.append(result)
        return result
    evidence = invoke(copy.deepcopy(FIXTURE['events']), ReplayStore(tmp_path / 'replay.sqlite'), collect)
    assert evidence['snapshots'][0]['complete'] is True
    assert evidence['snapshots'][0]['processes'] == [evidence['identity']]
