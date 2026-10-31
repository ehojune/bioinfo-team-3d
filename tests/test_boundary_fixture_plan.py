import json
import os
import subprocess
import sys
from pathlib import Path

from scripts.boundary_fixture_plan import validate


def plan():
    return {
        'version': 1, 'fixture_id': 'review-vm', 'image_digest': 'sha256:' + 'a' * 64,
        'owner': 'reviewer', 'expires_at': '2999-01-01T00:00:00Z',
        'principals': {'supervisor': 'supervisor', 'engine': 'engine', 'writer': 'writer'},
        'isolation': {'storage': 'supervisor_controlled_linux', 'shared_paths': False,
                      'interop': False, 'network': False},
        'teardown': ['vm', 'run_storage', 'credentials'],
        'observations': ['identity', 'writer_denial', 'parent_replacement', 'links',
                         'bytes_during_run', 'network_denial', 'interop_denial', 'cleanup'],
    }


def test_valid_is_not_protection_pass():
    assert validate(plan()) == {'plan_valid': True, 'protection': 'UNVERIFIED', 'issues': []}


def test_missing_conflicts_unknown_and_wrong_types():
    p = plan()
    del p['owner']
    p['principals']['writer'] = 'supervisor'
    p['isolation']['network'] = True
    p['isolation']['interop'] = 0
    p['teardown'].remove('vm')
    p['observations'] = 'everything'
    p['extra'] = 'unknown'
    errors = ' '.join(validate(p)['issues'])
    for expected in ('owner: missing', 'must be distinct', 'network:', 'interop:',
                     'missing vm', 'expected string array', 'unknown'):
        assert expected in errors


def test_cli_reports_unicode_key_on_ascii_stdout(tmp_path):
    p = plan()
    p['추가'] = 'unknown'
    source = tmp_path / 'plan.json'
    source.write_text(json.dumps(p, ensure_ascii=False), encoding='utf-8')
    script = Path(__file__).resolve().parents[1] / 'scripts' / 'boundary_fixture_plan.py'
    proc = subprocess.run([sys.executable, str(script), str(source)], capture_output=True,
                          env={**os.environ, 'PYTHONIOENCODING': 'ascii'})
    assert proc.returncode == 1
    result = json.loads(proc.stdout)
    assert result['protection'] == 'UNVERIFIED'
    assert any('추가' in issue and 'unknown' in issue for issue in result['issues'])


def test_expired_and_bad_digest():
    p = plan()
    p['expires_at'] = '2000-01-01T00:00:00Z'
    p['image_digest'] = 'sha256:abc'
    result = validate(p)
    assert not result['plan_valid']
    assert any('expired' in x for x in result['issues'])
    assert any('digest' in x for x in result['issues'])


def test_date_only_expiry_is_reported():
    p = plan()
    p['expires_at'] = '2027-01-01Z'
    result = validate(p)
    assert not result['plan_valid']
    assert any('expected UTC' in x for x in result['issues'])


def test_principal_whitespace_does_not_hide_conflict():
    p = plan()
    p['principals']['writer'] = ' supervisor '
    assert any('must be distinct' in x for x in validate(p)['issues'])


def test_cli_never_executes_command_string(tmp_path):
    marker = tmp_path / 'executed'
    p = plan()
    p['command'] = f'echo unsafe > "{marker}"'
    source = tmp_path / 'plan.json'
    source.write_text(json.dumps(p), encoding='utf-8')
    script = Path(__file__).resolve().parents[1] / 'scripts' / 'boundary_fixture_plan.py'
    proc = subprocess.run([sys.executable, str(script), str(source)], capture_output=True, text=True)
    assert proc.returncode == 1
    assert 'unknown' in json.loads(proc.stdout)['issues'][0]
    assert not marker.exists()


def test_malformed_json_cli(tmp_path):
    source = tmp_path / 'bad.json'
    source.write_text('{', encoding='utf-8')
    script = Path(__file__).resolve().parents[1] / 'scripts' / 'boundary_fixture_plan.py'
    proc = subprocess.run([sys.executable, str(script), str(source)], capture_output=True, text=True)
    assert proc.returncode == 1
    assert json.loads(proc.stdout)['protection'] == 'UNVERIFIED'
