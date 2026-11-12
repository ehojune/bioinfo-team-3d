"""Literal-check tests adapted from OKF Loremaster and modified for labhq.

Original: https://github.com/PFreda-Lab/okf-loremaster
Commit: ead3819
File: tests/test_verification.py
License: Apache-2.0; see labhq/evidence/NOTICE.md and LICENSE-APACHE-2.0.txt.

Modified for labhq: removed schema-level verification cases and added Korean Unicode
normalization and Python 3.10 grammar checks.
"""

import ast
import hashlib
import inspect
import json
from pathlib import Path

import pytest

import labhq.evidence.literal_check as literal_check
from labhq.evidence import report_check
from labhq.orchestrator.cso import Orchestrator
from tests.test_research_cp2 import CP1, _research_hub, _settings


def test_evidence_literal_checks_use_read_text_and_preserve_missing_status():
    ledger = {"evidence": [{"id": "e1", "observation": 'Reported "hazard ratio was 1.84".',
                            "quantities": [{"value": "1.84"}]}]}
    assert literal_check.check_evidence(ledger, {})[0]["state"] == "unverified"
    bad = literal_check.check_evidence(ledger, {"e1": "The hazard ratio was 1.44."})[0]
    assert bad["state"] == "mismatch" and len(bad["problems"]) == 2
    good = literal_check.check_evidence(ledger, {"e1": "The hazard ratio was 1.84."})[0]
    assert good["state"] == "matched" and not good["problems"]


def test_evidence_literal_check_keeps_written_precision_and_ignores_plain_paraphrases():
    ledger = {"evidence": [{"id": "e1", "observation": "A paraphrase of the result.",
                            "quantities": [{"value": "1.840"}]}]}
    assert literal_check.check_evidence(ledger, {"e1": "The ratio was 1.84."})[0]["state"] == "matched"
    ledger["evidence"][0]["quantities"] = []
    assert literal_check.check_evidence(ledger, {}) == []
from labhq.evidence.literal_check import (
    EN_DASH,
    MAX_LOCATED_QUOTE_WORDS,
    MIDDLE_DOT,
    MINUS,
    Quantity,
    Source,
    normalize,
    quantities_in,
)


def test_journal_number_shapes_are_read_as_numbers() -> None:
    text = f"OR 1{MIDDLE_DOT}82, beta {MINUS}0.44, n = 12,405"
    assert [quantity.value for quantity in quantities_in(text)] == [1.82, -0.44, 12405]


def test_interval_dash_is_not_a_negative_sign() -> None:
    assert [q.value for q in quantities_in("CI 1.21-2.74")] == [1.21, 2.74]
    assert [q.value for q in quantities_in(f"CI 1.21{EN_DASH}2.74")] == [1.21, 2.74]
    assert [q.value for q in quantities_in("CI 1.21 - 2.74")] == [1.21, 2.74]
    assert [q.value for q in quantities_in("at 30-day follow-up, 12 events")] == [30, 12]


def test_claim_may_be_less_precise_than_source_but_not_more_precise() -> None:
    assert Source("the adjusted odds ratio was 1.84").holds(1.8)
    assert Source("the adjusted odds ratio was 1.84").holds(1.84)
    assert not Source("the adjusted odds ratio was 1.8").holds(1.84)
    assert not Source("the adjusted odds ratio was 1.84").holds(1.9)


def test_bare_integer_does_not_support_claimed_decimal() -> None:
    source = Source("Table 4 lists the 2019 cohort of 244 adults.")
    assert not source.holds(4.44)
    assert source.holds(244)


def test_precision_survives_tiny_effect() -> None:
    assert Quantity.of(0.00003).decimals == 5


def test_quote_matching_ignores_case_punctuation_and_reflowed_whitespace() -> None:
    source = Source("The association held\nafter adjustment (P = 0.03).")
    assert source.holds_quote("the association held after adjustment, p=0.03")
    assert not source.holds_quote("The association did not hold after adjustment")
    assert normalize("A--B  c!") == "a b c"


def test_normalize_preserves_korean_quote_text() -> None:
    source = Source("치료 효과는 유의했다 (P = 0.03).")
    assert normalize("한글—인용문!") == "한글 인용문"
    assert source.holds_quote("치료 효과는 유의했다")
    assert not source.holds_quote("대조군 효과는 유의했다")


def test_locator_returns_original_sentence_without_heading() -> None:
    sentence = "In adjusted models the association was 1.82 (95% CI 1.21-2.74)."
    source = Source(f"## RESULTS\n{sentence}\nTable 2 follows.")
    assert source.sentence_for("In adjusted models") == sentence
    assert source.sentence_for("In unadjusted models") == ""


def test_flattened_table_is_limited_to_word_window() -> None:
    table = "Characteristic " + " ".join(f"row {n} value {n}.0" for n in range(200))
    quote = Source(table).sentence_for("row 40 value")
    assert len(quote.split()) == MAX_LOCATED_QUOTE_WORDS
    assert quote.startswith("row 40 value 40.0 row 41")
    assert quote in table
    assert Source(quote).holds(40.0)
    assert not Source(quote).holds(180.0)


def test_long_prose_sentence_below_cap_is_left_whole() -> None:
    sentence = (
        "In models adjusted for "
        + ", ".join(f"covariate {n}" for n in range(20))
        + ", the association was 1.82 (95% CI 1.21-2.74)."
    )
    assert len(sentence.split()) < MAX_LOCATED_QUOTE_WORDS
    assert Source(sentence).sentence_for("In models adjusted") == sentence


def test_module_parses_with_python_3_10_grammar() -> None:
    source = inspect.getsource(literal_check)
    ast.parse(source, feature_version=(3, 10))


def saved_paper(tmp_path, *, body='The response was 1.84.', paper_id='doi:10.1000/example'):
    from labhq.tools.annot_literature import parse_paper_id
    relative = '.tmp/literature/' + parse_paper_id(paper_id).file_name()
    path = tmp_path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body.encode())
    receipt = {'paper_id': paper_id, 'text_file': str(path), 'access_level': 'abstract',
               'text_sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    Path(str(path) + '.receipt.json').write_text(json.dumps(receipt), encoding='utf-8')
    return path, receipt


def ledger(value='1.84'):
    return {'evidence': [{'id': 'e1', 'kind': 'literature_claim', 'status': 'observed',
        'observation': '"The response was 1.84."', 'quantities': [{'value': value}],
        'source': {'id_scheme': 'doi', 'id_value': '10.1000/example'}}],
        'claims': [{'id': 'c1', 'status': 'supported'}],
        'links': [{'claim_id': 'c1', 'evidence_id': 'e1', 'relation': 'supports'}]}


@pytest.mark.parametrize('problem', ['none', 'hash', 'identity', 'missing', 'hardlink'])
def test_saved_source_requires_matching_receipt_and_bytes(tmp_path, problem):
    path, receipt = saved_paper(tmp_path)
    if problem == 'hash':
        path.write_text('changed', encoding='utf-8')
    elif problem == 'identity':
        receipt['paper_id'] = 'doi:10.1000/other'
        Path(str(path) + '.receipt.json').write_text(json.dumps(receipt), encoding='utf-8')
    elif problem == 'missing':
        path.unlink()
    elif problem == 'hardlink':
        (tmp_path / 'alias.md').hardlink_to(path)
    texts = literal_check.source_texts_for(ledger(), {'workdir': str(tmp_path)})
    checked = literal_check.check_evidence(ledger(), texts)
    assert checked[0]['state'] == ('matched' if problem == 'none' else 'unverified')
    assert texts == ({'e1': 'The response was 1.84.'} if problem == 'none' else {})


def test_collected_artifact_uses_runner_hash_and_not_observation(tmp_path):
    (tmp_path / 'outputs').mkdir()
    text = 'The response was 1.84.'
    (tmp_path / 'outputs/result.txt').write_bytes(text.encode())
    rows = ledger('2.50')
    rows['artifact_refs'] = [{'artifact_id': 'a1', 'path': 'outputs/result.txt'}]
    rows['evidence'][0]['source'] = {'artifact_id': 'a1'}
    result = {'workdir': str(tmp_path), 'outputs': ['outputs/result.txt'],
              'output_sha256': {'outputs/result.txt': hashlib.sha256(text.encode()).hexdigest()}}
    texts = literal_check.source_texts_for(rows, result)
    checked = literal_check.check_evidence(rows, texts)
    assert checked[0]['state'] == 'mismatch'
    assert 'quantity not found' in checked[0]['problems'][0]
    checked[0]['step_id'] = 's1'
    gate = report_check.check_report('Result [[claim:s1/c1]]', {'s1': rows}, literal_checks=checked,
                                   artifact_sha256={'s1/a1': result['output_sha256']['outputs/result.txt']})
    assert any('literal' in item and 'e1' in item for item in gate['problems'])
    result['output_sha256']['outputs/result.txt'] = '0' * 64
    assert literal_check.source_texts_for(rows, result) == {}


@pytest.mark.parametrize('local', ['missing', 'metadata', 'identity', 'hash', 'malformed', 'directory'])
def test_precedent_source_is_read_only_from_staged_verified_copy(tmp_path, local):
    from labhq.research.precedent_texts import file_name
    body = 'The response was 1.84.'
    receipt = {'paper_id': 'doi:10.1000/example', 'access_level': 'abstract',
               'text_sha256': hashlib.sha256(body.encode()).hexdigest()}
    staged = tmp_path / 'inputs/precedent' / file_name(receipt, 1)
    staged.parent.mkdir(parents=True)
    staged.write_bytes(body.encode())
    if local != 'missing':
        path, local_receipt = saved_paper(tmp_path)
        if local == 'metadata':
            local_receipt['access_level'] = 'metadata'
        elif local == 'identity':
            local_receipt['paper_id'] = 'doi:10.1000/other'
        elif local == 'hash':
            path.write_bytes(b'changed local text')
        elif local == 'directory':
            path.unlink()
            path.mkdir()
        Path(str(path) + '.receipt.json').write_text(
            '{bad json' if local == 'malformed' else json.dumps(local_receipt), encoding='utf-8')
    texts = literal_check.source_texts_for(ledger(), {'workdir': str(tmp_path)}, precedents=[receipt])
    assert texts == {'e1': body}
    staged.write_text('changed', encoding='utf-8')
    assert literal_check.source_texts_for(ledger(), {'workdir': str(tmp_path)}, precedents=[receipt]) == {}


def test_ancestor_artifact_needs_the_collected_owner_path(tmp_path):
    (tmp_path / 'outputs').mkdir()
    text = 'The response was 1.84.'
    (tmp_path / 'outputs/result.txt').write_bytes(text.encode())
    rows = ledger()
    rows['artifact_refs'] = [{'artifact_id': 'a1', 'path': 'parent/outputs/result.txt'}]
    rows['evidence'][0]['source'] = {'artifact_id': 'a1'}
    ancestor = {'workdir': str(tmp_path), 'workdir_id': 'parent', 'outputs': ['outputs/result.txt'],
                'output_sha256': {'outputs/result.txt': hashlib.sha256(text.encode()).hexdigest()}}
    assert literal_check.source_texts_for(rows, {}, [ancestor]) == {'e1': text}
    assert literal_check.source_texts_for(rows, {}) == {}


@pytest.mark.parametrize('stored_id', ['pmid:123', 'pmcid:PMC123'])
def test_source_alias_requires_receipt_metadata_and_matching_bytes(tmp_path, stored_id):
    path, receipt = saved_paper(tmp_path, paper_id=stored_id)
    receipt['paper_ids'] = [stored_id, 'doi:10.1000/example']
    sidecar = Path(str(path) + '.receipt.json')
    sidecar.write_text(json.dumps(receipt), encoding='utf-8')
    assert literal_check.source_texts_for(ledger(), {'workdir': str(tmp_path)}) == {'e1': 'The response was 1.84.'}
    receipt['paper_ids'] = [stored_id, 'doi:10.1000/other']
    sidecar.write_text(json.dumps(receipt), encoding='utf-8')
    assert literal_check.source_texts_for(ledger(), {'workdir': str(tmp_path)}) == {}
    receipt['paper_ids'] = [stored_id, 'doi:10.1000/example']
    sidecar.write_text(json.dumps(receipt), encoding='utf-8')
    path.write_bytes(b'changed')
    assert literal_check.source_texts_for(ledger(), {'workdir': str(tmp_path)}) == {}


def test_fulltext_receipt_records_provider_identifier_aliases(tmp_path):
    from labhq.tools.annot_literature import fulltext_receipt
    path, prior = saved_paper(tmp_path, paper_id='pmid:123')
    receipt = fulltext_receipt('pmid:123', {'text_file': path.relative_to(tmp_path).as_posix(), 'text_sha256': prior['text_sha256'],
        'access_level': 'abstract', 'pmid': '123', 'pmcid': 'PMC123', 'doi': '10.1000/example'}, tmp_path)
    assert set(receipt['paper_ids']) == {'pmid:123', 'pmcid:PMC123', 'doi:10.1000/example'}


def test_precedent_alias_record_uses_staged_copy_and_never_original_path(tmp_path):
    from labhq.research.precedent_texts import file_name
    body = 'The response was 1.84.'
    receipt = {'paper_id': 'pmid:123', 'paper_ids': ['pmid:123', 'doi:10.1000/example'],
               'access_level': 'abstract', 'text_file': str(tmp_path / 'unavailable-original.md'),
               'text_sha256': hashlib.sha256(body.encode()).hexdigest()}
    staged = tmp_path / 'inputs/precedent' / file_name(receipt, 1)
    staged.parent.mkdir(parents=True)
    staged.write_bytes(body.encode())
    record = {'status': 'ok', 'papers': [{'fulltext_receipt': receipt}]}
    assert literal_check.source_texts_for(ledger(), {'workdir': str(tmp_path)}, precedents=record) == {'e1': body}
    staged.write_bytes(b'changed')
    assert literal_check.source_texts_for(ledger(), {'workdir': str(tmp_path)}, precedents=record) == {}
    receipt['text_sha256'] = []
    assert literal_check.source_texts_for(ledger(), {'workdir': str(tmp_path)}, precedents=record) == {}


def test_registry_uri_can_name_a_saved_source_without_duplicate_id_fields(tmp_path):
    saved_paper(tmp_path)
    rows = ledger()
    rows['evidence'][0]['source'] = {'uri': 'https://doi.org/10.1000/example'}
    assert literal_check.source_texts_for(rows, {'workdir': str(tmp_path)}) == {'e1': 'The response was 1.84.'}


def test_unverified_literal_is_not_a_source_refusal():
    checks = [{'step_id': 's1', 'evidence_id': 'e1', 'state': 'unverified',
               'problems': ['source text unavailable for literal check']}]
    assert not report_check.check_report('Result [[claim:s1/c1]]', {'s1': ledger()},
                                        literal_checks=checks)['problems']


@pytest.mark.usefixtures('pre_v1_defaults')
@pytest.mark.asyncio
async def test_default_cp2_checks_saved_source_without_live_lookup(tmp_path, monkeypatch):
    settings = _settings()
    assert settings.research.literal_source_check is True
    settings.research.live_source_check = False
    hub = _research_hub(settings, [CP1, {'approved': True, 'choice': 'approve', 'note': ''}])
    original = hub.reply
    saved_paper(tmp_path)
    async def reply(task):
        response = await original(task)
        if task.meta['kind'] == 'step':
            response.workdir = str(tmp_path)
            row = response.structured['evidence'][0]
            row['observation'] = '"The response was 9.99."'
            row['source'].update(id_scheme='doi', id_value='10.1000/example', accessed_at='2026-11-12')
            row['source'].pop('artifact_id', None)
        return response
    hub.reply = reply
    def forbidden(*args, **kwargs):
        pytest.fail('offline literal check must not start live lookup')
    monkeypatch.setattr('labhq.orchestrator.cso.LiveSourceResolver', forbidden)
    await Orchestrator(hub).run_request('r')
    cp2 = hub.approvals[1]['detail']
    assert cp2['literal_checks'][0]['state'] == 'mismatch'
    assert any('literal' in row['reason'] for row in cp2['refused_evidence'])
    assert hub.requests['r']['research_contract']['checkpoints']['cp2']['literal_checks'] == cp2['literal_checks']
