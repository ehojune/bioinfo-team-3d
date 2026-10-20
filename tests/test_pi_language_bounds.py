"""Synthetic text only: preserve detection and bound long non-path scans (#810)."""
from time import perf_counter

import pytest

from labhq.pi_language import code_or_path, technical


@pytest.mark.parametrize('text', [
    '결과/표.tsv', '결과/중간/표.tsv', 'résultats/表.tsv', 'a/b', '_/1',
    './표.tsv', '../표.tsv', '../../결과/표.tsv', '/tmp/결과.tsv',
    '~/결과/표.tsv', 'a-b/c.d', '결과: (out/result.tsv)',
    'C:/결과.tsv', 'D:\\결과.tsv',
    '```python\npass\n```', '~~~text\n결과\n~~~', '`표`',
    '$표 = 1', 'import os', 'from pathlib import Path', 'def 함수():',
    'print(1)', 'New-Item 결과', 'Remove-Item 결과',
    '일반 설명\n  import os',
])
def test_code_and_path_detection_is_preserved(text):
    assert code_or_path(text) is True


@pytest.mark.parametrize('text', [
    '', '결과를 확인합니다.', '답' * 100, '/', '표/', '표 / 결과',
    '표//결과', '따옴표 ` 하나', '일반 설명 import 결과',
])
def test_plain_text_is_not_code_or_path(text):
    assert code_or_path(text) is False


@pytest.mark.parametrize('length', [25_000, 100_000])
@pytest.mark.parametrize('suffix,expected', [
    ('', False), ('/', False), (' 결과/표.tsv', True),
    ('/표.tsv', True), ('\n  import os', True),
])
def test_long_prefix_keeps_detection_bounded(length, suffix, expected):
    text = '답' * length + suffix
    start = perf_counter()
    assert code_or_path(text) is expected
    assert technical(text) is expected
    elapsed = perf_counter() - start
    # These pure scans take milliseconds; allow ample headroom on shared CI.
    assert elapsed < 0.5, f'{length}-character prefix took {elapsed:.3f}s'
