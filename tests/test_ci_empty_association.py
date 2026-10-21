"""Empty REST PR associations: authenticated API/ZIP fakes only, no git/network."""
import copy
import hashlib
from datetime import datetime, timezone

import pytest

from scripts import ci_skip as cs
from tests.test_ci_skip_policy import API, archive, evidence, reuse, sha


class EmptyAssociation(API):
    def __init__(self, evidence):
        super().__init__(evidence)
        self.run['pull_requests'] = []
        self.authoritative = copy.deepcopy(self.pr)
        self.authoritative['merged'] = True

    def __call__(self, path, binary=False):
        if path == 'pulls/788':
            self.calls.append(path)
            return self.authoritative
        return super().__call__(path, binary)

    def seal(self):
        self.zip = archive(self.manifest)
        self.artifact.update(size_in_bytes=len(self.zip),
            digest='sha256:' + hashlib.sha256(self.zip).hexdigest())


def test_empty_association_requires_authoritative_merged_pr_and_all_existing_proofs(evidence):
    api = EmptyAssociation(evidence)
    assert reuse(api)
    assert api.calls.count('pulls/788') == 1
    assert 'actions/artifacts/7/zip' in api.calls
    assert 'git/commits/' + api.manifest['checkout_sha'] in api.calls
    assert any('?ref=' + sha('c') in path for path in api.calls)  # pre-existing trusted policy
    assert api.manifest['version'] == cs.VERSION and cs.VERSION == 8


@pytest.mark.parametrize('case', [
    'number', 'head', 'base', 'repository', 'fork', 'base-ref', 'not-merged',
    'merged-time', 'merge-sha', 'missing-number', 'missing-merged', 'missing-repo',
])
def test_authoritative_pr_must_bind_the_listing_snapshot_and_exact_main_merge(evidence, case):
    api = EmptyAssociation(evidence); pr = api.authoritative
    if case == 'number': pr['number'] += 1
    elif case in {'head', 'base'}: pr[case]['sha'] = sha('f')
    elif case == 'repository': pr['base']['repo']['id'] += 1
    elif case == 'fork': pr['head']['repo'] = {'id':999,'full_name':'elsewhere/fork'}
    elif case == 'base-ref': pr['base']['ref'] = 'feature'
    elif case == 'not-merged': pr['merged'] = False
    elif case == 'merged-time': pr['merged_at'] = '2026-10-19T00:00:00Z'
    elif case == 'merge-sha': pr['merge_commit_sha'] = sha('f')
    elif case == 'missing-number': del pr['number']
    elif case == 'missing-merged': del pr['merged']
    else: del pr['base']['repo']
    assert not reuse(api)
    assert 'actions/artifacts/7/zip' not in api.calls


@pytest.mark.parametrize('value', [None, {}, '', False, [{'number':999}],
    [{'number':788,'head':{'sha':sha('f')}}]])
def test_only_an_explicit_empty_list_gets_the_narrow_fallback(evidence, value):
    api = EmptyAssociation(evidence); api.run['pull_requests'] = value
    assert not reuse(api)
    assert 'pulls/788' not in api.calls


def test_missing_association_field_is_full_not_equivalent_to_empty(evidence):
    api = EmptyAssociation(evidence); del api.run['pull_requests']
    assert not reuse(api)
    assert 'pulls/788' not in api.calls


def test_nonempty_association_still_requires_the_run_repository_id(evidence):
    api = API(evidence)
    for side in ('base', 'head'):
        api.run['pull_requests'][0][side]['repo']['id'] = 999
    assert not reuse(api)


@pytest.mark.parametrize('error', [PermissionError, TimeoutError, KeyError])
def test_unavailable_authoritative_pr_never_falls_back_to_manifest_assertions(evidence, error):
    api = EmptyAssociation(evidence)
    def request(path, binary=False):
        if path == 'pulls/788':
            raise error('synthetic unavailable authoritative proof')
        return api(path, binary)
    assert not cs.reuse_main(api.identity, request, now=datetime(2026,10,20,1,tzinfo=timezone.utc))
    assert 'actions/artifacts/7/zip' not in api.calls


@pytest.mark.parametrize('case', [
    'run-id','attempt','workflow-id','workflow-path','event','failed','cancelled','running',
    'stale','future','branch-head','run-fork','run-repository','tree','policy','checkout-base',
    'checkout-head','checkout-shape','commit-sha','commit-tree','parent-base','parent-head',
    'parent-order','parent-count','workflow-sha','workflow-ref','workflow-blob',
    'manifest-run','manifest-attempt','manifest-repository','manifest-pr','manifest-base',
    'missing-gate','failed-gate','gate-tree','partial-jobs','extra-job','job-failed',
    'job-attempt','job-run','job-head','expired','digest','preview-artifact','preview-profile',
    'preview-marker','old-v3','base-range',
])
def test_empty_association_never_relaxes_identity_coverage_or_authenticated_evidence(evidence,case):
    api=EmptyAssociation(evidence)
    if case=='run-id': api.run['id']+=1
    elif case=='attempt': api.run['run_attempt']+=1
    elif case=='workflow-id': api.run['workflow_id']+=1
    elif case=='workflow-path': api.run['path']='.github/workflows/other.yml'
    elif case=='event': api.run['event']='push'
    elif case in {'failed','cancelled'}: api.run['conclusion']='failure' if case=='failed' else 'cancelled'
    elif case=='running': api.run['status']='in_progress'
    elif case in {'stale','future'}: api.run['updated_at']='2020-01-01T00:00:00Z' if case=='stale' else '2099-01-01T00:00:00Z'
    elif case=='branch-head': api.run['head_sha']=sha('f')
    elif case in {'run-fork','run-repository'}: api.run['head_repository' if case=='run-fork' else 'repository']={'id':999,'full_name':'elsewhere/fork'}
    elif case=='tree': api.manifest['tree']=sha('f')
    elif case=='policy': api.manifest['policy']={**api.manifest['policy'],cs.POLICY_FILES[1]:sha('f')}
    elif case.startswith('checkout-'): api.manifest['checkout_sha']={'checkout-base':sha('c'),'checkout-head':sha('d'),'checkout-shape':'invalid'}[case]
    elif case=='commit-sha': api.commit['sha']=sha('f')
    elif case=='commit-tree': api.commit['tree']['sha']=sha('f')
    elif case in {'parent-base','parent-head'}: api.commit['parents'][0 if case=='parent-base' else 1]['sha']=sha('f')
    elif case=='parent-order': api.commit['parents'].reverse()
    elif case=='parent-count': api.commit['parents'].pop()
    elif case=='workflow-sha': api.manifest['workflow_sha']=sha('f')
    elif case=='workflow-ref': api.manifest['workflow_ref']=f'{cs.REPOSITORY}/{cs.WORKFLOW}@refs/heads/main'
    elif case=='workflow-blob': api.manifest['workflow_blob']=sha('f')
    elif case=='manifest-run': api.manifest['run_id']+=1
    elif case=='manifest-attempt': api.manifest['attempt']+=1
    elif case=='manifest-repository': api.manifest['repository']='elsewhere/fork'
    elif case=='manifest-pr': api.manifest['pr']['number']+=1
    elif case=='manifest-base': api.manifest['pr']['base_sha']=sha('f')
    elif case=='missing-gate': del api.manifest['gates']['pytest-windows']
    elif case=='failed-gate': api.manifest['gates']['pytest-windows']['result']='failure'
    elif case=='gate-tree': api.manifest['gates']['pytest-windows']['tree']=sha('f')
    elif case=='partial-jobs': api.jobs.pop()
    elif case=='extra-job': api.jobs.append(copy.deepcopy(api.jobs[0]))
    elif case=='job-failed': api.jobs[0]['conclusion']='cancelled'
    elif case=='job-attempt': api.jobs[0]['run_attempt']+=1
    elif case=='job-run': api.jobs[0]['run_id']+=1
    elif case=='job-head': api.jobs[0]['head_sha']=sha('f')
    elif case=='expired': api.artifact['expired']=True
    elif case=='preview-artifact': api.artifact['name']='boundary-preview-42-1'
    elif case=='preview-profile': api.manifest['coverage']['pytest-windows']='windows-boundary-preview-v4'
    elif case=='preview-marker': api.manifest.update(mode='preview',reusable=False)
    elif case=='old-v3': api.manifest.update(version=3); api.manifest['coverage']['pytest-windows']='windows-boundary-v1'
    elif case=='base-range': api.identity['selection_base']=sha('f')
    api.seal()
    if case=='digest': api.artifact['digest']='sha256:'+'0'*64
    assert not reuse(api)


@pytest.mark.parametrize('where',['base','checkout'])
@pytest.mark.parametrize('policy_path',cs.POLICY_FILES)
def test_empty_association_still_requires_every_policy_blob_on_trusted_base_and_checkout(evidence,where,policy_path):
    api=EmptyAssociation(evidence)
    def request(path,binary=False):
        ref=sha('c') if where=='base' else sha('e')
        if path==f'contents/{policy_path}?ref={ref}':return {'type':'file','sha':sha('f')}
        return api(path,binary)
    assert not cs.reuse_main(api.identity,request,now=datetime(2026,10,20,1,tzinfo=timezone.utc))


def test_newer_empty_association_failure_does_not_fall_back_to_older_success(evidence):
    api=EmptyAssociation(evidence); latest=copy.deepcopy(api.run)
    latest.update(id=99,conclusion='failure',updated_at='2026-10-20T00:01:00Z')
    def request(path,binary=False):
        if path.startswith('actions/workflows/9/runs?'):return {'workflow_runs':[api.run,latest]}
        return api(path,binary)
    assert not cs.reuse_main(api.identity,request,now=datetime(2026,10,20,1,tzinfo=timezone.utc))
    assert 'actions/artifacts/7/zip' not in api.calls
