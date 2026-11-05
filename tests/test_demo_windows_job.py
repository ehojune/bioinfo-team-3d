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
    # #833: the start was attempted inside the Job, so the fixed marker line precedes the product line.
    assert io_.stderr.getvalue() == 'LABHQ_JOB_STARTING_BROWSER\nLABHQ_EXEC_NOT_FOUND: nochrome.exe\n'
    io_ = streams()
    assert launcher.run(['--windows-job', '100', '--', 'chrome.exe'], job_factory=FakeJob([], peak=99),
                        start=lambda c, **o: SimpleNamespace(returncode=1), **vars(io_)) == 1
    assert io_.stderr.getvalue() == 'LABHQ_JOB_STARTING_BROWSER\nout of memory: Windows Job object limit reached\n'


class RecordingStream:
    def __init__(self, record, *, broken=False):
        self.record, self.broken, self.text = record, broken, ''

    def write(self, text):
        if self.broken:
            raise OSError('closed')
        self.text += text
        if text.endswith('\n'):
            self.record.append(('stderr', self.text))
            self.text = ''
        return len(text)

    def flush(self):
        if self.broken:
            raise OSError('closed')


def test_the_fixed_marker_is_written_once_after_the_job_exists_and_before_the_browser_starts():
    """#833: it splits wrapper/Job setup from Chrome's own start. A fixed constant: no path, argument or value."""
    record, io_ = [], streams()
    stderr = RecordingStream(record)
    launcher.run(['--windows-job', '1024', '--', 'C:/secret/chrome.exe', '--user-data-dir=C:/private'],
                 job_factory=FakeJob(record), start=lambda c, **o: record.append('start') or SimpleNamespace(returncode=0),
                 stdin=io_.stdin, stdout=io_.stdout, stderr=stderr)
    assert [entry if isinstance(entry, str) else entry[0] for entry in record] == ['job', 'stderr', 'start']
    assert record[1] == ('stderr', launcher.JOB_STARTING_BROWSER + '\n') == ('stderr', 'LABHQ_JOB_STARTING_BROWSER\n')


def test_an_unwritable_stderr_does_not_stop_the_start_or_change_its_status():
    record, io_ = [], streams()
    code = launcher.run(['--windows-job', '1024', '--', 'chrome.exe'], job_factory=FakeJob(record),
                        start=lambda c, **o: record.append('start') or SimpleNamespace(returncode=5),
                        stdin=io_.stdin, stdout=io_.stdout, stderr=RecordingStream(record, broken=True))
    assert code == 5 and record[-1] == 'start'


def test_the_default_job_is_the_product_kill_on_close_job_loaded_from_its_source_file():
    job = launcher.product_job_class()
    assert job.__name__ == '_WindowsJob'
    assert launcher.PRODUCT_MODULE == ROOT / 'labhq' / 'runner' / 'memory_limit.py'
    assert job._KILL_ON_CLOSE == 0x00002000 and job._JOB_MEMORY == 0x00000200
    with pytest.raises(OSError):
        launcher.product_job_class(ROOT / 'tests' / 'support' / 'missing_memory_limit.py')


class FakeChild:
    """A Popen stand-in: records the order of creation, wait and kill; nothing is started."""
    def __init__(self, record, *, returncode=0, wait_error=None):
        self.record, self.returncode, self.wait_error = record, returncode, wait_error

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.record.append('exit')
        return False

    def wait(self):
        self.record.append('wait')
        if self.wait_error:
            raise self.wait_error
        return self.returncode

    def kill(self):
        self.record.append('kill')


def fake_popen(record, **child):
    def popen(command, **options):
        record.append(('popen', command, options))
        return FakeChild(record, **child)
    return popen


def test_the_created_marker_follows_the_actual_child_creation_and_precedes_the_wait():
    """#833: LABHQ_BROWSER_CREATED means Popen (CreateProcess) returned, which the Job line cannot show."""
    record, io_ = [], streams()
    stderr = RecordingStream(record)
    completed = launcher.start_and_mark(['chrome.exe', '--user-data-dir=C:/private'], check=False, stdin=io_.stdin,
                                        stdout=io_.stdout, stderr=stderr, popen=fake_popen(record, returncode=4))
    assert completed.returncode == 4 and completed.args == ['chrome.exe', '--user-data-dir=C:/private']
    assert record == [('popen', ['chrome.exe', '--user-data-dir=C:/private'],
                       dict(stdin=io_.stdin, stdout=io_.stdout, stderr=stderr)),
                      ('stderr', 'LABHQ_BROWSER_CREATED\n'), 'wait', 'exit']
    assert launcher.BROWSER_CREATED == 'LABHQ_BROWSER_CREATED'


def test_a_failed_creation_writes_no_created_marker_and_keeps_the_product_code():
    io_ = streams()

    def missing(command, **options):
        raise FileNotFoundError(command[0])
    start = lambda command, **options: launcher.start_and_mark(command, popen=missing, **options)
    assert launcher.run(['--windows-job', '100', '--', 'nochrome.exe'], job_factory=FakeJob([]), start=start,
                        **vars(io_)) == 127
    assert io_.stderr.getvalue() == 'LABHQ_JOB_STARTING_BROWSER\nLABHQ_EXEC_NOT_FOUND: nochrome.exe\n'


def test_the_default_start_writes_both_fixed_lines_in_order_and_returns_the_browser_status():
    record, io_ = [], streams()
    # The wrapper's default is this start, over the real Popen; here the same function runs over a fake Popen.
    assert launcher.run.__kwdefaults__['start'] is launcher.start_and_mark
    assert launcher.start_and_mark.__kwdefaults__['popen'] is launcher.subprocess.Popen
    start = lambda command, **options: launcher.start_and_mark(command, popen=fake_popen(record, returncode=6), **options)
    assert launcher.run(['--windows-job', '64', '--', 'chrome.exe'], job_factory=FakeJob(record), start=start,
                        **vars(io_)) == 6
    assert io_.stderr.getvalue() == 'LABHQ_JOB_STARTING_BROWSER\nLABHQ_BROWSER_CREATED\n'
    assert [entry[0] if isinstance(entry, tuple) else entry for entry in record] == ['job', 'popen', 'wait', 'exit']


def test_an_unwritable_stderr_still_waits_for_the_browser_and_keeps_its_status():
    record = []
    completed = launcher.start_and_mark(['chrome.exe'], stderr=RecordingStream(record, broken=True),
                                        popen=fake_popen(record, returncode=2))
    assert completed.returncode == 2 and record[-2:] == ['wait', 'exit']


def test_an_interrupted_wait_kills_the_owned_child_before_the_error_propagates():
    record = []
    with pytest.raises(KeyboardInterrupt):
        launcher.start_and_mark(['chrome.exe'], stderr=io.StringIO(),
                                popen=fake_popen(record, wait_error=KeyboardInterrupt()))
    assert record[1:] == ['wait', 'kill', 'exit']
