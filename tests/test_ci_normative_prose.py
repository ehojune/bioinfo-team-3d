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
