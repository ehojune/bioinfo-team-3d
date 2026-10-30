"""Bounded manifest diagnostics with fake git only; no CI or process execution."""
import copy
import json

import pytest

from scripts import ci_skip as cs


@pytest.fixture
def producer(monkeypatch, tmp_path):
    from tests.test_ci_windows_phases import fake_context, make_receipts
    from scripts import ci_windows_phases as phases
    sha = lambda char: char * 40
    current = {"checkout_sha": sha("a"), "tree": sha("b"),
               "policy": {path: sha(f'{i + 1:x}') for i, path in enumerate(cs.POLICY_FILES)}}
    repo = {"id": 123, "full_name": cs.REPOSITORY}
    event = {"pull_request": {"number": 793,
             "base": {"ref": "main", "sha": sha("c"), "repo": repo},
             "head": {"ref": "feature", "sha": sha("d"), "repo": repo}}}
    needs = {name: {"result": "success", "outputs": {"tree": current["tree"], **({"profile": cs.COVERAGE[name]} if name in cs.COVERAGE else {})}}
             for name in (*cs.GATE_IDS, "changes", "readme", "patch-notes")}
    needs["changes"]["outputs"]["skip"] = "false"
    env = {"GITHUB_EVENT_NAME": "pull_request", "GITHUB_REPOSITORY": cs.REPOSITORY,
           "GITHUB_SHA": current["checkout_sha"], "GITHUB_WORKFLOW_SHA": current["checkout_sha"],
           "GITHUB_WORKFLOW_REF": f"{cs.REPOSITORY}/{cs.WORKFLOW}@refs/pull/793/merge",
           "GITHUB_RUN_ID": "42", "GITHUB_RUN_ATTEMPT": "1"}
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    context = fake_context(current, checkout=current['checkout_sha'])
    context['workflow_ref'] = env['GITHUB_WORKFLOW_REF']
    proof = make_receipts(tmp_path, context)
    monkeypatch.setenv('CI_WINDOWS_RECEIPT_DIR', str(tmp_path))
    needs['pytest-windows']['outputs'].update(execution=phases.EXECUTION, receipt=phases.digest(proof))
    git_results = {"parents": f"{sha('a')} {sha('c')} {sha('d')}",
                   "blob": current["policy"][cs.WORKFLOW]}
    calls = []
    def git(*args):
        calls.append(args)
        if args == ("rev-list", "--parents", "-n", "1", "HEAD"):
            return git_results["parents"]
        assert args == ("rev-parse", current["checkout_sha"] + ":" + cs.WORKFLOW)
        return git_results["blob"]
    monkeypatch.setattr(cs, "git", git)
    return current, needs, event, git_results, calls


def test_bound_identity_and_all_receipts_still_produce_the_same_manifest(producer):
    current, needs, event, _, calls = producer
    result = cs.produce_manifest(current, needs, event)
    assert result == cs.expected_manifest({"id": 42, "run_attempt": 1}, current,
                                         cs.pr_snapshot(event["pull_request"]), current["checkout_sha"], result['windows_execution'])
    assert len(calls) == 2


@pytest.mark.parametrize("case,reason", [
    ("checkout-shape", "checkout-sha"), ("run", "run-id"), ("attempt", "run-attempt"),
    ("workflow-sha", "workflow-sha"), ("workflow-ref", "workflow-ref"),
    ("one-parent", "checkout-parents"), ("reverse-parents", "checkout-parents"),
    ("base-parent", "checkout-parents"), ("head-parent", "checkout-parents"),
    ("blob", "workflow-blob"),
])
def test_each_identity_failure_names_its_exact_failed_check(monkeypatch, producer, case, reason):
    current, needs, event, values, calls = producer
    if case == "checkout-shape":
        current["checkout_sha"] = "not-a-sha"
        monkeypatch.setenv("GITHUB_SHA", current["checkout_sha"])
    elif case in {"run", "attempt"}:
        monkeypatch.setenv("GITHUB_RUN_ID" if case == "run" else "GITHUB_RUN_ATTEMPT", "0")
    elif case.startswith("workflow-"):
        monkeypatch.setenv("GITHUB_WORKFLOW_SHA" if case.endswith("sha") else "GITHUB_WORKFLOW_REF",
                           "untrusted-value-that-must-not-be-logged")
    elif case == "blob":
        values["blob"] = "f" * 40
    else:
        parts = values["parents"].split()
        if case == "one-parent":
            parts.pop()
        elif case == "reverse-parents":
            parts[1:] = reversed(parts[1:])
        else:
            parts[1 if case == "base-parent" else 2] = "f" * 40
        values["parents"] = " ".join(parts)
    with pytest.raises(ValueError, match="unbound checkout/workflow identity: " + reason) as error:
        cs.produce_manifest(current, needs, event)
    assert len(str(error.value)) < 200
    assert "untrusted-value" not in str(error.value)
    if case not in {"one-parent", "reverse-parents", "base-parent", "head-parent", "blob"}:
        assert not calls  # no git lookup of an unbound workflow revision


@pytest.mark.parametrize("source,role", [("d", "pr-head"), ("c", "pr-base"), ("f", "other")])
def test_source_roles_are_diagnostic_only_not_new_allow_branches(monkeypatch, producer, source, role):
    current, needs, event, _, calls = producer
    monkeypatch.setenv("GITHUB_WORKFLOW_SHA", source * 40)
    monkeypatch.setenv("GITHUB_WORKFLOW_REF", f"{cs.REPOSITORY}/{cs.WORKFLOW}@refs/heads/feature")
    with pytest.raises(ValueError) as error:
        cs.produce_manifest(current, needs, event)
    assert str(error.value) == ("unbound checkout/workflow identity: workflow-sha, workflow-ref "
                                f"(source={role}, ref=pr-head)")
    assert not calls


@pytest.mark.parametrize("gate", [*cs.GATE_IDS, "readme", "patch-notes"])
def test_identity_diagnostics_do_not_bypass_any_gate(producer, gate):
    current, needs, event, _, calls = producer
    changed = copy.deepcopy(needs)
    changed[gate]["result"] = "skipped"
    with pytest.raises(ValueError, match="incomplete full gate receipts"):
        cs.produce_manifest(current, changed, event)
    assert not calls


@pytest.mark.parametrize("gate", cs.GATE_IDS)
@pytest.mark.parametrize("profile", [None, "windows-full-v1", "windows-boundary-v1", "serial-legacy"])
def test_coverage_receipts_must_match_every_expected_gate(producer, gate, profile):
    current, needs, event, _, calls = producer
    needs[gate]["outputs"]["profile"] = profile
    with pytest.raises(ValueError, match="coverage profile receipts"):
        cs.produce_manifest(current, needs, event)
    assert not calls


def test_parent_mismatch_stays_denied_and_logs_only_bounded_public_shas(producer, capsys):
    current, needs, event, values, _ = producer
    values["parents"] = f"{current['checkout_sha']} {'f' * 40} {'d' * 40} arbitrary-private-value"
    with pytest.raises(ValueError, match="checkout-parents"):
        cs.produce_manifest(current, needs, event)
    output = capsys.readouterr().err
    assert '"length": 4' in output and '"expected":' in output
    assert 'f' * 40 in output and 'c' * 40 in output
    assert "arbitrary-private-value" not in output and len(output) < 500


@pytest.fixture
def upload():
    original = dict(name='pytest-windows', run_id=42, run_attempt=1, head_sha='d' * 40,
        status='completed', conclusion='success', created_at='2026-10-25T01:00:00Z',
        started_at='2026-10-25T01:00:01Z', completed_at='2026-10-25T01:08:00Z')
    copied = dict(original, run_attempt=3, created_at='2026-10-25T03:00:00Z')
    artifact = dict(id=77, name='coverage-windows-42-1', expired=False,
        created_at='2026-10-25T01:07:55Z', updated_at='2026-10-25T01:07:55Z',
        workflow_run=dict(id=42, head_sha='d' * 40, repository_id=123, head_repository_id=123))
    data = {1: original, 3: copied, 'artifacts': [artifact]}
    def request(path):
        if path.endswith('/artifacts?per_page=100'):
            return dict(total_count=len(data['artifacts']), artifacts=data['artifacts'])
        attempt = int(path.split('/attempts/')[1].split('/')[0])
        return dict(total_count=1, jobs=[data[attempt]])
    return data, request


def select(request, sleep=lambda seconds: None):
    # A test never really waits: the poll is bounded by a count, so a stubbed sleep keeps its meaning (#1127).
    return cs.windows_artifact(dict(id=42, run_attempt=3), 'd' * 40, 123, request, sleep)


def test_copied_success_resolves_original_upload(upload):
    _, request = upload
    assert select(request) == (77, 1)


def test_fresh_execution_keeps_current_attempt(upload):
    data, request = upload
    data[3] = dict(data[1], run_attempt=3)
    data['artifacts'][0]['name'] = 'coverage-windows-42-3'
    assert select(request) == (77, 3)


@pytest.mark.parametrize('reverse', [False, True])
def test_old_failed_upload_does_not_block_fresh_success(upload, reverse):
    data, request = upload
    data[1]['conclusion'] = 'failure'
    data[3].update(started_at='2026-10-25T03:00:01Z', completed_at='2026-10-25T03:08:00Z')
    fresh = copy.deepcopy(data['artifacts'][0])
    fresh.update(id=78, name='coverage-windows-42-3',
                 created_at='2026-10-25T03:07:55Z', updated_at='2026-10-25T03:07:55Z')
    data['artifacts'].append(fresh)
    if reverse:
        data['artifacts'].reverse()
    assert select(request) == (78, 3)
    data['artifacts'] = [a for a in data['artifacts'] if a['id'] != 78]
    with pytest.raises(ValueError, match='missing/ambiguous bound'):
        select(request)


@pytest.mark.parametrize('case', ['missing', 'expired', 'foreign-run', 'foreign-head', 'foreign-repo',
    'failed', 'running', 'newer-execution', 'copied-only', 'before-job', 'after-job', 'duplicate',
    'wrong-job-run', 'wrong-job-head', 'wrong-job-attempt'])
def test_unbound_or_missing_upload_stays_denied(upload, case):
    data, request = upload
    artifact = data['artifacts'][0]
    if case == 'missing':
        data['artifacts'] = []
    elif case == 'expired':
        artifact['expired'] = True
    elif case.startswith('foreign-'):
        artifact['workflow_run'][{'foreign-run': 'id', 'foreign-head': 'head_sha',
                                  'foreign-repo': 'repository_id'}[case]] = 'bad'
    elif case in {'failed', 'running'}:
        data[3]['conclusion'] = 'failure' if case == 'failed' else None
        data[3]['status'] = 'completed' if case == 'failed' else 'in_progress'
    elif case == 'newer-execution':
        data[3]['started_at'] = '2026-10-25T03:00:01Z'
    elif case == 'copied-only':
        artifact['name'] = 'coverage-windows-42-3'
    elif case == 'before-job':
        artifact['created_at'] = '2026-10-25T00:00:00Z'
    elif case == 'after-job':
        artifact['updated_at'] = '2026-10-25T02:00:00Z'
    elif case == 'duplicate':
        data['artifacts'].append(copy.deepcopy(artifact))
    else:
        data[1][{'wrong-job-run': 'run_id', 'wrong-job-head': 'head_sha',
                 'wrong-job-attempt': 'run_attempt'}[case]] = 'bad'
    with pytest.raises(ValueError):
        select(request)


def test_incomplete_api_page_stays_denied(upload):
    _, request = upload
    def incomplete(path):
        result = request(path)
        result['total_count'] += 1
        return result
    with pytest.raises(ValueError, match='incomplete producer jobs'):
        select(incomplete)


def test_verified_partial_receipt_is_validated_but_never_reusable(producer, upload, monkeypatch, tmp_path):
    current, needs, event, _, _ = producer
    _, request = upload
    event_path = tmp_path / 'event.json'
    event_path.write_text(json.dumps(event), encoding='utf-8')
    monkeypatch.setenv('GITHUB_EVENT_PATH', str(event_path))
    monkeypatch.setenv('GITHUB_RUN_ATTEMPT', '3')
    monkeypatch.setenv('CI_WINDOWS_ARTIFACT_ID', '77')
    monkeypatch.setattr(cs, 'api', request)
    with pytest.raises(cs.NonReusablePartialRerun):
        cs.produce_manifest(current, needs, event)
    output = tmp_path / 'output.txt'
    monkeypatch.setenv('GITHUB_OUTPUT', str(output))
    monkeypatch.setenv('CI_NEEDS', json.dumps(needs))
    monkeypatch.setattr(cs, 'identity', lambda: current)
    monkeypatch.setattr(cs.sys, 'argv', ['ci_skip.py', 'manifest'])
    monkeypatch.chdir(tmp_path)
    cs.main()
    assert output.read_text(encoding='utf-8') == 'reusable=false\nreason=partial-rerun\n'
    assert not (tmp_path / 'ci-result.json').exists()
    monkeypatch.setenv('CI_WINDOWS_ARTIFACT_ID', '78')
    with pytest.raises(ValueError, match='unbound prior-attempt'):
        cs.produce_manifest(current, needs, event)
    monkeypatch.setenv('CI_WINDOWS_ARTIFACT_ID', '77')
    needs['pytest-windows']['outputs']['receipt'] = 'bad'
    with pytest.raises(ValueError, match='missing/mismatched Windows'):
        cs.produce_manifest(current, needs, event)


def test_fresh_windows_retry_cannot_certify_copied_other_gates(producer, monkeypatch, tmp_path):
    from tests.test_ci_windows_phases import fake_context, make_receipts
    from scripts import ci_windows_phases as phases
    current, needs, _, _, _ = producer
    context = fake_context(current, checkout=current['checkout_sha'])
    context['attempt'] = 3
    context['workflow_ref'] = cs.os.environ['GITHUB_WORKFLOW_REF']
    directory = tmp_path / 'fresh-windows'
    proof = make_receipts(directory, context)
    needs['pytest-windows']['outputs']['receipt'] = phases.digest(proof)
    monkeypatch.setenv('GITHUB_RUN_ATTEMPT', '3')
    monkeypatch.setenv('CI_WINDOWS_RECEIPT_DIR', str(directory))
    # Other gate outputs still carry their original success and tree.
    with pytest.raises(cs.NonReusablePartialRerun):
        cs.produce_manifest(current, needs, producer[2])


def _lagging(upload, steps):
    """A request whose attempt-3 job list answers each read from `steps` (a list of job lists; the last one repeats)."""
    data, request = upload
    reads = []

    def lagged(path):
        if '/attempts/3/jobs' in path:
            reads.append(path)
            jobs = steps[min(len(reads), len(steps)) - 1]
            return dict(total_count=len(jobs), jobs=jobs)
        return request(path)
    return data, lagged, reads


def test_a_copy_that_appears_late_is_waited_for_and_then_checked_as_before(upload):
    # #1127: the copied pytest-windows job was created seconds after the manifest job asked for it.
    data, _ = upload
    _, request, reads = _lagging(upload, [[], [], [data[3]]])
    sleeps = []
    assert select(request, sleeps.append) == (77, 1)
    assert len(reads) == 3 and sleeps == [cs.JOB_POLL_INTERVAL_S] * 2


def test_a_job_that_still_reads_as_running_is_waited_for_until_it_completes(upload):
    data, _ = upload
    running = dict(data[3], status='in_progress', conclusion=None)
    _, request, reads = _lagging(upload, [[running], [running], [data[3]]])
    sleeps = []
    assert select(request, sleeps.append) == (77, 1)
    assert len(reads) == 3 and len(sleeps) == 2


def test_the_wait_is_bounded_and_ends_in_the_same_missing_producer_error(upload):
    _, request, reads = _lagging(upload, [[]])
    sleeps = []
    with pytest.raises(ValueError, match='missing/ambiguous Windows producer'):
        select(request, sleeps.append)
    assert len(reads) == cs.JOB_POLL_ATTEMPTS == 40 and len(sleeps) == cs.JOB_POLL_ATTEMPTS - 1
    assert set(sleeps) == {cs.JOB_POLL_INTERVAL_S} and cs.JOB_POLL_INTERVAL_S == 3  # 40 reads, 3 s apart, about two minutes


def test_a_completed_failure_is_denied_at_once_without_waiting(upload):
    data, _ = upload
    failed = dict(data[3], conclusion='failure')
    _, request, reads = _lagging(upload, [[failed], [data[3]]])
    sleeps = []
    with pytest.raises(ValueError, match='failed/incomplete effective Windows producer'):
        select(request, sleeps.append)
    assert len(reads) == 1 and sleeps == []  # a later success never rescues a completed failure


def test_two_producers_are_denied_at_once_without_waiting(upload):
    data, _ = upload
    _, request, reads = _lagging(upload, [[data[3], dict(data[3], id=2)]])
    sleeps = []
    with pytest.raises(ValueError, match='missing/ambiguous Windows producer'):
        select(request, sleeps.append)
    assert len(reads) == 1 and sleeps == []
