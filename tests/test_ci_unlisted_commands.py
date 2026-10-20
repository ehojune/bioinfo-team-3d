"""Unknown executable examples must not opt out of CI; git is always fake."""
import pytest

from scripts import ci_skip as cs


@pytest.mark.parametrize('prefix', [
    'Run ', 'Please execute ', 'To reproduce, invoke ', 'For example, use ',
    'Try ', '다음을 실행하세요 ',
])
@pytest.mark.parametrize('old,new', [
    ('java Old.java', 'java New.java'), ('perl old.pl', 'perl new.pl'),
    ('ruby old.rb', 'ruby new.rb'), ('foobar old', 'foobar new'),
    ('quux argument', 'quux replacement'), ('clang old.c', 'clang new.c'),
])
def test_unlisted_cli_instruction_changes_additions_and_deletions_run_full(prefix, old, new, monkeypatch):
    before, after = prefix + old + '.', prefix + new + '.'
    assert not cs.document_change('README.md', before, after)
    assert not cs.document_change('README.md', 'Old prose.', after)
    assert not cs.document_change('README.md', before, 'New prose.')

    def git(*args):
        if args[0] == 'diff':
            return ':100644 100644 old new M\0README.md\0'
        assert args[0] == 'show'
        return {'base:README.md': before, 'head:README.md': after}[args[1]]
    monkeypatch.setattr(cs, 'git', git)
    assert not cs.can_skip('head', 'base')


@pytest.mark.parametrize('before,after', [
    ('java Old.java.', 'java New.java.'),
    ('foobar --old.', 'foobar --new.'),
    ('foobar -n old.', 'foobar -n new.'),
    ('foobar inputs/old.', 'foobar inputs/new.'),
    (r'foobar inputs\old.', r'foobar inputs\new.'),
    ("foobar 'old'.", "foobar 'new'."),
])
def test_unlisted_argv_without_instruction_prefix_also_runs_full(before, after):
    assert not cs.document_change('README.md', before, after)


@pytest.mark.parametrize('before,after', [
    ('The documentation is clear.', 'The documentation is more concise.'),
    ('A runner description.', 'A runner explanation.'),
    ('# labhq\n\nRun foobar old.\n\nOld prose.\n\nUse `java Stable.java`.\n\n```sh\nquux stable\n```',
     '# labhq\n\nRun foobar old.\n\nNew prose.\n\nUse `java Stable.java`.\n\n```sh\nquux stable\n```'),
])
def test_ordinary_prose_and_unchanged_unknown_cli_examples_still_reduce(before, after):
    assert cs.document_change('README.md', before, after)
