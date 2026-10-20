"""Real Markdown parsing and fake git; no commands, processes or network."""
import pytest
from markdown_it import MarkdownIt

from scripts import ci_skip as cs


PATHS = ('README.md', 'docs/reference/overview.md', 'docs/status/progress.md')


@pytest.fixture(autouse=True)
def complete_markdown_dependency():
    # A missing parser must fail the test, not masquerade as fail-closed proof.
    assert MarkdownIt('commonmark').parse('Ordinary prose.')[1].content == 'Ordinary prose.'
    assert cs.markdown_parts('Ordinary prose.')[2] == [('Ordinary prose.', False)]


@pytest.mark.parametrize('path', PATHS)
@pytest.mark.parametrize('before,after', [
    pytest.param('Authentication remains mandatory.', 'Authentication is now optional.',
                 id='4321974288-authentication'),
    ('Widget access is prohibited.', 'Widget access is permitted.'),
    ('장치 검토를 거쳐야 합니다.', '장치 검토를 건너뛰어도 됩니다.'),
    ('검토는 필수입니다.', '검토는 생략해도 됩니다.'),
    ('Trinkets must remain sealed.', 'Trinkets can remain open.'),
    ('An inspection is required.', 'An inspection is unnecessary.'),
    ('Widgets shall remain closed.', 'Widgets may remain open.'),
    ('A widget needs inspection.', 'A widget no longer needs inspection.'),
    ('An observer ought to attend.', 'An observer need not attend.'),
    ('Never expose the widget.', 'Expose the widget.'),
    ('Do not open the widget.', 'Open the widget.'),
    ('기록을 남겨야 합니다.', '기록을 남기지 않아도 됩니다.'),
    ('이 경로로 가면 안 됩니다.', '이 경로로 가도 됩니다.'),
    ('문은 열지 마세요.', '문은 열어도 괜찮습니다.'),
    ('장치 출입은 금지됩니다.', '장치 출입은 가능합니다.'),
])
def test_requirement_permission_and_prohibition_changes_run_full_in_both_directions(path, before, after):
    assert not cs.document_change(path, before, after)
    assert not cs.document_change(path, after, before)


@pytest.mark.parametrize('path', PATHS)
@pytest.mark.parametrize('normative', [
    'Authentication remains mandatory.',
    'Widget access is prohibited.',
    '장치 검토를 거쳐야 합니다.',
])
def test_added_or_removed_requirement_is_checked_on_either_side(path, normative):
    plain = 'The overview explains the diagram.'
    assert not cs.document_change(path, normative, plain)
    assert not cs.document_change(path, plain, normative)


@pytest.mark.parametrize('path', PATHS)
@pytest.mark.parametrize('before,after', [
    ('The overview is clearer.', 'The overview is easier to read.'),
    ('설명이 더 명확해졌습니다.', '설명이 더 짧아졌습니다.'),
    ('The appendix contains more examples.', 'The appendix contains clearer diagrams.'),
])
def test_ordinary_prose_still_reduces_checks(path, before, after):
    assert cs.document_change(path, before, after)


@pytest.mark.parametrize('path', PATHS)
def test_unchanged_requirement_does_not_block_an_independent_prose_edit(path):
    before = 'Authentication remains mandatory.\n\nThe overview is clearer.'
    after = before.replace('The overview is clearer.', 'The overview is easier to read.')
    assert cs.document_change(path, before, after)


def test_prose_in_the_same_normative_paragraph_remains_conservatively_full():
    before = 'Authentication remains mandatory. The overview is clearer.'
    after = before.replace('The overview is clearer.', 'The overview is easier to read.')
    assert not cs.document_change('README.md', before, after)


@pytest.mark.parametrize('path', (*PATHS, 'AGENTS.md', 'HANDOFF.md', 'docs/manual.md'))
@pytest.mark.parametrize('before,after', [
    ('Authentication remains mandatory. Widgets stay closed.',
     'Authentication remains mandatory.\nWidgets stay closed.'),
    ('Authentication remains mandatory.', 'Authentication remains\nmandatory.'),
    ('장치 검토를 거쳐야 합니다.', '장치 검토를\n거쳐야 합니다.'),
])
def test_exact_markdown_meaning_is_preserved_for_requirement_reflow(path, before, after):
    assert cs.document_change(path, before, after)
    assert cs.document_change(path, after, before)


def test_emphasis_and_sentence_structure_changes_are_not_reflow():
    assert not cs.document_change('README.md', 'Authentication is **mandatory**.',
                                  'Authentication is mandatory.')
    assert not cs.document_change('README.md', 'Authentication remains mandatory.',
                                  'Authentication remains\n\nmandatory.')


def test_normative_container_changes_are_not_ordinary_prose_or_reflow():
    assert not cs.document_change('README.md', '- Authentication remains mandatory.',
                                  'Authentication remains mandatory.')
    assert not cs.document_change('README.md', '> Authentication remains mandatory.',
                                  'Authentication remains mandatory.')


def test_entire_diff_with_a_requirement_change_runs_full_using_fake_git(monkeypatch):
    def git(*args):
        if args[0] == 'diff':
            return ':100644 100644 old new M\0docs/reference/overview.md\0'
        assert args[0] == 'show'
        return {'base:docs/reference/overview.md': 'Authentication remains mandatory.',
                'head:docs/reference/overview.md': 'Authentication is now optional.'}[args[1]]
    monkeypatch.setattr(cs, 'git', git)
    assert not cs.can_skip('head', 'base')


def test_4321519440_exact_gradle_change_is_already_full_with_real_parser():
    before, after = 'Build with gradle assemble.', 'Build with gradle publish.'
    assert cs.markdown_parts(before)[2] and cs.markdown_parts(after)[2]
    assert not cs.prose_change(before, after)
    assert not cs.document_change('README.md', before, after)


COMMAND_EDITS = (
    ('Build with gradle assemble.', 'Build with gradle clean.'),
    ('Build with gradle assemble.', 'Build with gradle bundle.'),
    ('Frobnicate with zorb weave.', 'Frobnicate with zorb unweave.'),
    ('Construct via zerbit plonk.', 'Construct via zerbit sprock.'),
    ('Mogrify zephyr weave.', 'Mogrify zephyr unweave.'),
    ('zephyr weave.', 'zephyr unweave.'),
    ('Using zorb, weave.', 'Using zorb, sprock.'),
    ('After quux prepare, zorb weave.', 'After quux prepare, zorb sprock.'),
    ('Please frobnicate zephyr weave.', 'Please frobnicate zephyr unweave.'),
    ('The next step is zorb weave.', 'The next step is zorb unweave.'),
    ('The diagram is clearer. Mogrify zephyr weave.',
     'The diagram is clearer. Mogrify zephyr unweave.'),
    ('The overview is clearer, then frobnicate zephyr weave.',
     'The overview is shorter, then frobnicate zephyr weave.'),
    ('## Frobnicate zephyr weave', '## Frobnicate zephyr unweave'),
    ('- Mogrify zephyr weave.', '- Mogrify zephyr unweave.'),
    ('프롭으로 위젯을 조립하세요.', '프롭으로 위젯을 분해하세요.'),
    ('프롭을 조립하십시오.', '프롭을 분해하십시오.'),
    ('프롭으로 위젯을 조립합니다.', '프롭으로 위젯을 분해합니다.'),
    ('zorb로 위젯을 조립합니다.', 'zorb로 위젯을 분해합니다.'),
)


@pytest.mark.parametrize('path', PATHS)
@pytest.mark.parametrize('before,after', COMMAND_EDITS)
def test_unclassified_instructions_are_protected_in_both_directions(path, before, after):
    assert not cs.document_change(path, before, after)
    assert not cs.document_change(path, after, before)
    plain = 'The overview is clearer.'
    assert not cs.document_change(path, before, plain)
    assert not cs.document_change(path, plain, before)


@pytest.mark.parametrize('before,after', COMMAND_EDITS[:6])
def test_unclassified_instruction_reaches_full_decision_with_only_fake_git(monkeypatch, before, after):
    def git(*args):
        if args[0] == 'diff':
            return ':100644 100644 old new M\0README.md\0'
        assert args[0] == 'show'
        return {'base:README.md': before, 'head:README.md': after}[args[1]]
    monkeypatch.setattr(cs, 'git', git)
    assert not cs.can_skip('head', 'base')


@pytest.mark.parametrize('before,after', [
    ('The diagram is clearer.', 'The diagram is shorter.'),
    ('This paragraph is easier to read.', 'This paragraph is more concise.'),
    ('The explanations are clearer.', 'The explanations are more concise.'),
    ('A runner description.', 'A runner explanation.'),
    ('More prose.', 'New prose.'),
])
def test_reviewed_descriptive_forms_remain_document_only_in_both_directions(before, after):
    assert cs.document_change('README.md', before, after)
    assert cs.document_change('README.md', after, before)


def test_unchanged_unclassified_instruction_is_protected_but_independent_prose_can_change():
    before = 'Build with gradle assemble.\n\nThe diagram is clearer.'
    after = before.replace('The diagram is clearer.', 'The diagram is shorter.')
    assert cs.document_change('README.md', before, after)
    assert cs.document_change('README.md', after, before)
    assert cs.document_change('README.md', 'Build with gradle assemble.', 'Build with\ngradle assemble.')


def test_missing_real_parser_is_a_full_fallback_not_green_classification_evidence(monkeypatch):
    import builtins
    original = builtins.__import__

    def missing_parser(name, *args, **kwargs):
        if name == 'markdown_it':
            raise ModuleNotFoundError('synthetic missing markdown_it')
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, '__import__', missing_parser)
    with pytest.raises(ModuleNotFoundError, match='synthetic missing markdown_it'):
        cs.markdown_parts('The overview is clearer.')
    assert not cs.document_change('README.md', 'The overview is clearer.', 'The overview is shorter.')


@pytest.mark.parametrize('text', [
    'The overview sparkles brightly.',  # possibly harmless, outside the small grammar
    'Description inconnue et ambiguë.',
    '위젯을 조립합니다.',
])
def test_unreviewed_sentence_forms_conservatively_run_full(text):
    assert not cs.document_change('README.md', 'The overview is clearer.', text)
    assert not cs.document_change('README.md', text, 'The overview is clearer.')


KOREAN_ACTION_EDITS = (
    ('프롭으로 위젯을 조립하고 결과를 확인합니다.',
     '프롭으로 위젯을 분해하고 결과를 확인합니다.'),
    ('젤룩으로 프라븐을 돌리고 내용을 설명합니다.',
     '젤룩으로 프라븐을 묶고 내용을 설명합니다.'),
    ('브룬을 플룩하고 조각을 담습니다.', '브룬을 글룩하고 조각을 담습니다.'),
    ('트룹을 조립하고 기록을 남깁니다.', '트룹을 분해하고 기록을 남깁니다.'),
    ('위젯을 확인합니다.', '부품을 확인합니다.'),
    ('위젯을 설명합니다.', '부품을 설명합니다.'),
    ('위젯을 담습니다.', '부품을 담습니다.'),
    ('위젯을 남깁니다.', '부품을 남깁니다.'),
    ('프롭으로위젯을조립하고결과를확인합니다.',
     '프롭으로위젯을분해하고결과를확인합니다.'),
    ('설명이조립하고결과를확인합니다.', '설명이분해하고결과를확인합니다.'),
    ('설명이 더 명확해졌습니다. 프롭으로 위젯을 조립하고 결과를 확인합니다.',
     '설명이 더 명확해졌습니다. 프롭으로 위젯을 분해하고 결과를 확인합니다.'),
    ('설명이 더 명확해졌습니다.프롭으로 위젯을 조립하고 결과를 확인합니다.',
     '설명이 더 명확해졌습니다.프롭으로 위젯을 분해하고 결과를 확인합니다.'),
    ('프롭으로 위젯을 조립하고 결과를 확인합니다. 설명이 더 명확해졌습니다.',
     '프롭으로 위젯을 분해하고 결과를 확인합니다. 설명이 더 명확해졌습니다.'),
    ('## 프롭으로위젯을조립하고결과를확인합니다',
     '## 프롭으로위젯을분해하고결과를확인합니다'),
    ('- 프롭으로 위젯을 조립하고 결과를 확인합니다.',
     '- 프롭으로 위젯을 분해하고 결과를 확인합니다.'),
)


@pytest.mark.parametrize('path', PATHS)
@pytest.mark.parametrize('before,after', KOREAN_ACTION_EDITS)
def test_korean_action_chains_cannot_be_descriptions_by_their_report_tail(path, before, after):
    assert not cs.document_change(path, before, after)
    assert not cs.document_change(path, after, before)
    plain = '설명이 더 명확해졌습니다.'
    assert not cs.document_change(path, plain, after)
    assert not cs.document_change(path, before, plain)


def test_korean_action_chain_reaches_full_diff_decision_with_fake_git(monkeypatch):
    before, after = KOREAN_ACTION_EDITS[0]
    def git(*args):
        if args[0] == 'diff':
            return ':100644 100644 old new M\0README.md\0'
        assert args[0] == 'show'
        return {'base:README.md': before, 'head:README.md': after}[args[1]]
    monkeypatch.setattr(cs, 'git', git)
    assert not cs.can_skip('head', 'base')


@pytest.mark.parametrize('before,after', [
    ('설명이 더 명확해졌습니다.', '설명이 더 짧아졌습니다.'),
    ('문서는 간결합니다.', '문서는 짧습니다.'),
])
def test_small_korean_adjective_grammar_remains_document_only(before, after):
    assert cs.document_change('README.md', before, after)
    assert cs.document_change('README.md', after, before)


def test_small_korean_unspaced_adjectives_retain_the_existing_fragment_fallback():
    # Do not loosen the existing single-token/full fallback to admit new prose.
    before, after = '설명이더명확해졌습니다.', '설명이더짧아졌습니다.'
    assert not cs.document_change('README.md', before, after)
    assert not cs.document_change('README.md', after, before)


def test_small_korean_grammar_does_not_add_a_previously_unsupported_adjective_form():
    before, after = '문서는 간결합니다.', '문서는 간결해졌습니다.'
    assert not cs.document_change('README.md', before, after)
    assert not cs.document_change('README.md', after, before)


def test_korean_action_text_can_only_reflow_with_identical_markdown_semantics():
    before, changed = KOREAN_ACTION_EDITS[0]
    wrapped = before.replace('결과를 확인합니다.', '결과를\n확인합니다.')
    assert cs.document_change('README.md', before, wrapped)
    assert cs.document_change('README.md', wrapped, before)
    assert not cs.document_change('README.md', wrapped, changed)


def test_4320728990_unchanged_readme_heading_is_already_document_only_with_fake_git(monkeypatch):
    before, after = '# labhq\n', '# labhq\nMore prose.\n'
    assert cs.document_change('README.md', before, after)
    def git(*args):
        if args[0] == 'diff':
            return ':100644 100644 old new M\0README.md\0'
        assert args[0] == 'show'
        return {'base:README.md': before, 'head:README.md': after}[args[1]]
    monkeypatch.setattr(cs, 'git', git)
    assert cs.can_skip('head', 'base')
