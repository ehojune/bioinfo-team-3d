"""Failure evidence snapshots with fake unreadable files; no workers or HPC."""
import json
from pathlib import Path

import pytest
import yaml

from scripts import ci_failure_diagnostics as d
from scripts.ci_selection import ROOT


def test_locked_input_does_not_lose_checkpoint_or_other_receipts(tmp_path, monkeypatch):
    source, target = tmp_path / 'source', tmp_path / 'snapshot'
    source.mkdir()
    (source / 'checkpoints-gw0.json').write_text('{"current":"test_wait"}', encoding='utf-8')
    (source / 'metrics-gw0.json').write_text('{"exitstatus":1}', encoding='utf-8')
    (source / 'stacks-gw0.log').write_text('locked', encoding='utf-8')
    (source / 'host-resources.lock').write_bytes(b'')
    real_open = Path.open

    def open_file(path, *args, **kwargs):
        if path == source / 'stacks-gw0.log':
            raise PermissionError(13, 'locked')
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', open_file)
    lines = []
    result = d.preserve(source, target, emit=lines.append)
    assert result['errors'] == [dict(path='stacks-gw0.log', error='PermissionError', errno=13)]
    assert result['skipped'] == [dict(path='host-resources.lock', reason='live lock/temporary file')]
    assert (target / 'metrics-gw0.json').read_bytes() == (source / 'metrics-gw0.json').read_bytes()
    assert 'test_wait' in ''.join(lines) and 'stacks-gw0.log' in ''.join(lines)
    assert not (target / 'host-resources.lock').exists()
    assert result['diagnostic_only'] and result['reusable'] is False
    assert not (target / 'windows-proof.json').exists()


def test_incomplete_session_console_is_bounded_redacted_and_not_a_runner_command(tmp_path, monkeypatch):
    source, target = tmp_path / 'source', tmp_path / 'snapshot'
    source.mkdir()
    monkeypatch.setenv('GITHUB_RUN_ID', '42')
    monkeypatch.setenv('GITHUB_RUN_ATTEMPT', '2')
    secret = 'gh' + 'p_' + 'a' * 24
    for worker in range(20):
        (source / f'stacks-gw{worker}.log').write_text('x' * 10000 + '\n::error::' + secret, encoding='utf-8')
    lines = []
    result = d.preserve(source, target, emit=lines.append)
    assert sum(len(line) - len('CI evidence: ') for line in lines) <= d.CONSOLE_BYTES
    assert secret not in ''.join(lines) and '[redacted]' in ''.join(lines)
    assert all(line.startswith('CI evidence: "') and '\n' not in line for line in lines)
    assert result['context']['GITHUB_RUN_ID'] == '42' and result['context']['GITHUB_RUN_ATTEMPT'] == '2'
    assert not list(target.glob('*receipt*')) and not list(target.glob('*proof*'))


def test_missing_source_is_diagnostic_and_staging_cannot_reuse_or_overlap(tmp_path):
    target = tmp_path / 'snapshot'
    result = d.preserve(tmp_path / 'absent', target)
    assert result['copied'] == [] and not result['reusable']
    with pytest.raises(FileExistsError):
        d.preserve(tmp_path / 'absent', target)
    with pytest.raises(ValueError, match='separate'):
        d.preserve(tmp_path, tmp_path / 'nested')


def test_workflow_preserves_original_failure_and_uploads_only_closed_snapshot():
    job = yaml.safe_load((ROOT / '.github/workflows/test.yml').read_text(encoding='utf-8'))['jobs']['pytest-windows']
    steps = job['steps']
    report = next(s for s in steps if s.get('id') == 'coverage')
    snapshot = next(s for s in steps if 'scripts.ci_failure_diagnostics' in s.get('run', ''))
    upload = next(s for s in steps if s.get('uses', '').startswith('actions/upload-artifact'))
    assert steps.index(report) < steps.index(snapshot) < steps.index(upload)
    assert report['if'] == 'success()' and snapshot['if'] == upload['if'] == 'always()'
    assert not job.get('continue-on-error') and not any(s.get('continue-on-error') for s in steps)
    assert upload['with']['path'] == snapshot['env']['CI_UPLOAD_DIR']
    assert 'CI_SUITE_ARTIFACT_DIR' in snapshot['run']
    assert job['outputs']['receipt'] == '${{ steps.coverage.outputs.receipt }}'


def test_snapshot_limits_never_publish_a_partial_receipt(tmp_path, monkeypatch):
    source, target = tmp_path / 'source', tmp_path / 'snapshot'
    source.mkdir()
    (source / 'metrics-gw0.json').write_bytes(b'0123456789')
    monkeypatch.setattr(d, 'MAX_FILE_BYTES', 5)
    result = d.preserve(source, target)
    assert result['errors'][0]['path'] == 'metrics-gw0.json'
    assert not (target / 'metrics-gw0.json').exists()
    assert json.loads((target / 'failure-preservation.json').read_text())['reusable'] is False
