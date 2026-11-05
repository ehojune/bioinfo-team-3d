"""A relative `.../data/...` path is not a local path, and a check that stops the edition names itself (#713)."""

import pytest

from labhq.portfolio import scan_publish_files
from tests.test_publish_retry_607 import NEW, fake_github, run_finish


@pytest.mark.parametrize(("text", "flagged"), [
    ('MATRIX = "inputs/om_fetch/data/methylation_matrix.tsv.gz"\n', False),  # the 10-17 analysis scripts
    ('ZHOU = "outputs/reference/tmp/mask.tsv"\n', False),
    ('A = "./data/x.tsv"\nB = "../data/y.tsv"\n', False),  # PR #717 review
    ('MATRIX = "/data/methylation_matrix.tsv.gz"\n', True),
    ("see file:///home/me/run.log\n", True),
    (r"interpreter=C:\Users\me\.labhq\runs\x\python.exe" + "\n", True),
])
def test_root_folders_count_only_where_a_path_starts(text, flagged):
    issues = scan_publish_files({"steps/s1/outputs/scripts/run.py": text})
    assert any(issue["kind"] == "local_path" for issue in issues) is flagged


async def test_a_stopped_edition_says_which_check_and_files(tmp_path, monkeypatch):
    monkeypatch.setattr("labhq.result_storage.scan_publish_files",
                        lambda files, internal=False, settings=None:
                        [{"kind": "local_path", "path": "steps/s1/outputs/run.py"}])
    hub, saved = await run_finish(tmp_path, monkeypatch, NEW, fake_github([]))
    assert saved["status"] == "failed" and saved["error_type"] == "EditionCheckError"
    assert saved["warning"] == "게시 전 검사에 실패했습니다: local_path — steps/s1/outputs/run.py"
    assert "토큰" not in saved["warning"]


def test_cleaning_keeps_relative_paths_and_masks_absolute_ones():
    from labhq.portfolio import _clean
    from labhq.result_storage import clean_document
    from labhq.settings import Settings
    text = 'A = "./data/x.tsv"\nB = "../data/y.tsv"\nC = "/home/me/z.tsv"\n'
    for cleaned in (_clean(text, Settings(), {}), clean_document(text, Settings(), {})):
        assert './data/x.tsv' in cleaned and '../data/y.tsv' in cleaned
        assert "/home/me" not in cleaned
