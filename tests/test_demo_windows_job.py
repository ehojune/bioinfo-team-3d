"""#842: the test-only Windows demo-browser launcher. Pure: fake Job and fake start, no process or Job is created."""
import importlib.util
import io
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('demo_windows_job_browser', ROOT / 'tests' / 'support' / 'windows_job_browser.py')
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


class FakeJob:
    def __init__(self, record, *, peak=0, fail=None):
        self.record, self.peak, self.fail = record, peak, fail

    def __call__(self, pid, limit):
        self.record.append(('job', pid, limit))
        if self.fail:
            raise self.fail
        return self

    def peak_bytes(self):
        return self.peak


def streams():
    return SimpleNamespace(stdin=io.StringIO(), stdout=io.StringIO(), stderr=io.StringIO())


def test_the_child_starts_inside_the_job_with_exactly_this_process_standard_handles():
    record, io_ = [], streams()

    def start(command, **options):
        record.append(('start', command, options))
        return SimpleNamespace(returncode=3)

    code = launcher.run(['--windows-job', '1024', '--', 'chrome.exe', '--headless=new', 'about:blank'],
                        job_factory=FakeJob(record), start=start, **vars(io_))
    assert code == 3  # the browser's own status, as the product entry returns it
    assert record[0][0] == 'job' and record[0][2] == 1024  # the Job exists before the child starts
    assert record[1] == ('start', ['chrome.exe', '--headless=new', 'about:blank'],
                         dict(check=False, stdin=io_.stdin, stdout=io_.stdout, stderr=io_.stderr))
    assert 'close_fds' not in record[1][2], 'only the standard handles are passed; no broad handle inheritance'


def test_default_handles_are_this_process_streams(monkeypatch):
    seen = {}
    io_ = streams()
    for name in ('stdin', 'stdout', 'stderr'):
        monkeypatch.setattr(launcher.sys, name, getattr(io_, name))
    launcher.run(['--windows-job', '1', '--', 'chrome.exe'], job_factory=FakeJob([]),
                 start=lambda command, **options: seen.update(options) or SimpleNamespace(returncode=0))
    assert (seen['stdin'], seen['stdout'], seen['stderr']) == (io_.stdin, io_.stdout, io_.stderr)


def test_job_setup_failure_starts_no_child_and_reports_the_product_setup_line():
    record, io_ = [], streams()
    code = launcher.run(['--windows-job', '1024', '--', 'chrome.exe'], job_factory=FakeJob(record, fail=OSError('denied')),
                        start=lambda *a, **k: record.append('start'), **vars(io_))
    assert code == 126 and 'start' not in record
    assert io_.stderr.getvalue() == 'LABHQ_MEMORY_LIMIT_SETUP: OSError: denied\n'


@pytest.mark.parametrize('argv', [[], ['--windows-job', '1', 'chrome.exe'], ['--exec', '1', '--', 'chrome.exe'],
                                  ['--windows-job', '0', '--', 'chrome.exe'], ['--windows-job', 'x', '--', 'chrome.exe'],
                                  ['--windows-job', '1', '--']])
def test_invalid_arguments_create_neither_job_nor_child(argv):
    record, io_ = [], streams()
    code = launcher.run(argv, job_factory=FakeJob(record), start=lambda *a, **k: record.append('start'), **vars(io_))
    assert code == 126 and record == []
    assert io_.stderr.getvalue().startswith('LABHQ_MEMORY_LIMIT_SETUP:')


def test_missing_browser_and_memory_limit_keep_the_product_codes_and_lines():
    io_ = streams()

    def missing(command, **options):
        raise FileNotFoundError(command[0])
    assert launcher.run(['--windows-job', '100', '--', 'nochrome.exe'], job_factory=FakeJob([]), start=missing,
                        **vars(io_)) == 127
    assert io_.stderr.getvalue() == 'LABHQ_EXEC_NOT_FOUND: nochrome.exe\n'
    io_ = streams()
    assert launcher.run(['--windows-job', '100', '--', 'chrome.exe'], job_factory=FakeJob([], peak=99),
                        start=lambda c, **o: SimpleNamespace(returncode=1), **vars(io_)) == 1
    assert io_.stderr.getvalue() == 'out of memory: Windows Job object limit reached\n'


def test_the_default_job_is_the_product_kill_on_close_job_loaded_from_its_source_file():
    job = launcher.product_job_class()
    assert job.__name__ == '_WindowsJob'
    assert launcher.PRODUCT_MODULE == ROOT / 'labhq' / 'runner' / 'memory_limit.py'
    assert job._KILL_ON_CLOSE == 0x00002000 and job._JOB_MEMORY == 0x00000200
    with pytest.raises(OSError):
        launcher.product_job_class(ROOT / 'tests' / 'support' / 'missing_memory_limit.py')
