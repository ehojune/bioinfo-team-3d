from labhq.runner.lookup_admission import LookupAdmission


def test_precedent_share_preserves_request_capacity(tmp_path):
    admission = LookupAdmission(tmp_path / "lookup.sqlite3", 9, precedent_share=0.5)
    def send(stage):
        return admission.admit("request", "task", "agent", "search", "example.org", stage=stage)

    assert all(send("precedent")["admitted"] for _ in range(4))
    assert send("precedent")["reason"] == "stage share exhausted"
    assert all(send(None)["admitted"] for _ in range(5))
    assert send(None)["reason"] == "exhausted"


def test_migration_and_restart_preserve_counts(tmp_path):
    import sqlite3
    path = tmp_path / "old.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE sends (request_id TEXT NOT NULL, seq INTEGER NOT NULL, task_id TEXT, agent_id TEXT, tool TEXT NOT NULL, host TEXT NOT NULL, admitted_at REAL NOT NULL, http_status INTEGER, outcome TEXT, PRIMARY KEY(request_id, seq))")
        db.execute("INSERT INTO sends VALUES ('r', 1, 'old', 'a', 'search', 'example.org', 0, NULL, NULL)")
    ledger = LookupAdmission(path, 4, precedent_share=0.5)
    assert ledger.usage('r')['used'] == 1
    assert ledger.admit('r', 't1', 'a', 'search', 'example.org', stage='precedent')['admitted']
    ledger = LookupAdmission(path, 4, precedent_share=0.5)
    assert ledger.admit('r', 't2', 'a', 'search', 'example.org', stage='precedent')['admitted']
    assert ledger.admit('r', 't2', 'a', 'search', 'example.org', stage='precedent')['reason'] == 'stage share exhausted'
    assert ledger.admit('r', 'plan', 'a', 'search', 'example.org')['admitted']
    assert ledger.admit('r', 'plan', 'a', 'search', 'example.org')['reason'] == 'exhausted'


def test_concurrent_migration_and_admission(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    path = tmp_path / 'concurrent.sqlite3'
    def attempt(i):
        ledger = LookupAdmission(path, 9, precedent_share=0.5)
        return ledger.admit('r', str(i), 'a', 'search', 'example.org', stage='precedent')['admitted']
    with ThreadPoolExecutor(max_workers=12) as pool:
        assert sum(pool.map(attempt, range(24))) == 4
    assert LookupAdmission(path, 9, precedent_share=0.5).usage('r')['used'] == 4


def test_share_boundaries_and_prompt(tmp_path):
    from labhq.runner.lookup_admission import usage_note
    from labhq.settings import AnnotSettings
    from pydantic import ValidationError
    import pytest
    assert AnnotSettings().precedent_lookup_share == .5
    assert AnnotSettings(precedent_lookup_share=None).precedent_lookup_share is None
    for invalid in (-0.1, 1.1):
        with pytest.raises(ValidationError):
            AnnotSettings(precedent_lookup_share=invalid)
    for share, expected in [(None, True), (0, False), (0.5, False), (1, True)]:
        ledger = LookupAdmission(tmp_path / f'{share}.db', 1, precedent_share=share)
        assert ledger.admit('r', 't', 'a', 'search', 'example.org', stage='precedent')['admitted'] is expected
    ledger = LookupAdmission(tmp_path / 'prompt.db', 9, precedent_share=0.5)
    for _ in range(8):
        ledger.admit('r', 't', 'a', 'search', 'example.org')
    assert 'Precedent share: 0 of 4 used, 1 left' in usage_note(ledger, 'r', stage='precedent')
