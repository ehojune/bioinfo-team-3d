"""#1285: a changed test helper selects the nodes that use it, not every test file that imports it.

Pure AST/data contracts: no subprocess, socket or worker launch.
"""
import json
from types import SimpleNamespace

import pytest

from scripts import ci_selection as s, ci_suite as suite

HELPER = 'tests/support/lab.py'
OS_HELPER = 'import os\nos.fsync(1)\ndef run_lab(): return 1\n'
PLAIN_HELPER = 'VALUE = 1\ndef run_lab(): return 1\n'
CHANGED = frozenset({HELPER})


def policy_for(path='tests/test_math.py'):
    return {'version': 6, 'profile': s.WINDOWS_BOUNDARY, 'default': 'boundary', 'known_tests': [path],
            'boundary_cohorts': {'tests/test_windows*.py': 'native OS boundary'},
            'mandatory_nodes': [], 'mixed_nodes': {}}


def decide(tmp_path, source, node, *, helper=OS_HELPER, changed=CHANGED):
    helper_path = tmp_path / HELPER
    helper_path.parent.mkdir(parents=True, exist_ok=True)
    helper_path.write_text(helper, encoding='utf-8')
    return s.include_windows('tests/test_math.py', source, policy_for(), root=tmp_path, changed=changed, node=node)


SOURCE = ('import pytest\n'
          'from tests.support.lab import run_lab, lab_fixture\n'
          'def test_uses(): run_lab()\n'
          'def test_unrelated(): assert 1\n'
          'def test_fixture_argument(lab_fixture): assert 1\n'
          '@pytest.mark.usefixtures("lab_fixture")\n'
          'def test_marked(): assert 1\n'
          'def local(): return run_lab()\n'
          'def test_through_a_local_helper(): local()\n'
          'class TestGroup:\n'
          '    def test_inside(self): run_lab()\n'
          '    def test_also_inside(self): assert 1\n')


@pytest.mark.parametrize('node,expected', [
    ('test_uses', True), ('test_unrelated', False), ('test_fixture_argument', True), ('test_marked', True),
    ('test_through_a_local_helper', True),
    ('TestGroup::test_inside', True), ('TestGroup::test_also_inside', True),  # a class stays whole
    ('test_uses[param]', True),
])
def test_only_nodes_that_use_the_changed_helper_are_selected(tmp_path, node, expected):
    included, reason = decide(tmp_path, SOURCE, node)
    assert included is expected
    assert reason == ('directly imported changed OS test helper' if expected else
                      'ordinary logic/schema/text; full Linux and nightly/manual Windows')


def test_a_changed_helper_without_an_os_api_selects_nothing(tmp_path):
    assert not decide(tmp_path, SOURCE, 'test_uses', helper=PLAIN_HELPER)[0]


def test_an_unchanged_helper_selects_nothing(tmp_path):
    assert not decide(tmp_path, SOURCE, 'test_uses', changed=frozenset())[0]


def test_a_star_import_cannot_be_narrowed(tmp_path):
    source = 'from tests.support.lab import *\ndef test_unrelated(): assert 1\n'
    assert decide(tmp_path, source, 'test_unrelated')[0]


def test_import_time_use_selects_every_node_of_the_file(tmp_path):
    source = 'from tests.support.lab import run_lab\nVALUE = run_lab()\ndef test_unrelated(): assert 1\n'
    assert decide(tmp_path, source, 'test_unrelated')[0]


def test_a_module_import_is_used_through_its_name_or_alias(tmp_path):
    source = ('import tests.support.lab as lab\n'
              'def test_uses(): lab.run_lab()\n'
              'def test_unrelated(): assert 1\n')
    assert decide(tmp_path, source, 'test_uses')[0]
    assert not decide(tmp_path, source, 'test_unrelated')[0]


def test_an_imported_autouse_fixture_applies_to_every_node_without_being_named(tmp_path):
    helper = OS_HELPER + 'import pytest\n@pytest.fixture(autouse=True)\ndef always(): yield\n'
    source = 'from tests.support.lab import always\ndef test_unrelated(): assert 1\n'
    assert decide(tmp_path, source, 'test_unrelated', helper=helper)[0]
    plain = OS_HELPER + 'import pytest\n@pytest.fixture\ndef sometimes(): yield\n'
    source = 'from tests.support.lab import sometimes\ndef test_unrelated(): assert 1\n'
    assert not decide(tmp_path, source, 'test_unrelated', helper=plain)[0]


def test_a_file_level_decision_still_selects_the_file(tmp_path):
    assert decide(tmp_path, SOURCE, None)[0]


def test_select_records_why_each_file_was_kept(tmp_path):
    helper = tmp_path / HELPER
    helper.parent.mkdir(parents=True)
    helper.write_text(OS_HELPER, encoding='utf-8')
    test = tmp_path / 'tests/test_math.py'
    test.write_text(SOURCE, encoding='utf-8')
    (tmp_path / s.PROFILE).parent.mkdir(parents=True, exist_ok=True)
    data = policy_for()
    (tmp_path / s.PROFILE).write_text(json.dumps(data), encoding='utf-8')
    names = ['test_uses', 'test_unrelated', 'test_marked', 'test_through_a_local_helper']
    items = [SimpleNamespace(path=test, nodeid=f'tests/test_math.py::{n}') for n in names]
    chosen, omitted, reasons = suite.select(items, s.WINDOWS_BOUNDARY, tmp_path, changed=set(CHANGED))
    assert [i.nodeid.split('::')[1] for i in chosen] == ['test_uses', 'test_marked', 'test_through_a_local_helper']
    assert [i.nodeid.split('::')[1] for i in omitted] == ['test_unrelated']
    summary = suite.inclusion_summary()
    assert summary == {'directly imported changed OS test helper': {'nodes': 3, 'files': {'tests/test_math.py': 3}}}
    suite.select(items, s.WINDOWS_BOUNDARY, tmp_path, changed=set())
    assert suite.inclusion_summary() == {}  # reset on every call, never carried over


def test_the_summary_is_bounded_and_largest_first():
    counts = {f'tests/test_{n:02d}.py': n for n in range(1, 40)}
    summary = suite.inclusion_summary({'a reason': counts}, files=3)
    assert list(summary['a reason']['files']) == ['tests/test_39.py', 'tests/test_38.py', 'tests/test_37.py']
    assert summary['a reason']['nodes'] == sum(counts.values())
