"""Chrome warm-up step: a cold start is timed in its own step, and the browser test's 30 s start limit stays (#1107)."""
import re
from pathlib import Path

import pytest
import yaml

from scripts import ci_chrome_warmup as w

ROOT = Path(__file__).resolve().parents[1]


class FakeProcess:
    """A Chrome that writes its DevToolsActivePort file after `port_after` polls and exits when told to or at `dies_at`."""

    def __init__(self, profile, port_after=2, dies_at=None):
        self.profile, self.port_after, self.dies_at = profile, port_after, dies_at
        self.polls, self.stopped, self.pid = 0, False, 4242

    def poll(self):
        self.polls += 1
        if self.polls >= self.port_after:
            (Path(self.profile) / 'DevToolsActivePort').write_text('9222\n/devtools/browser/x', encoding='utf-8')
        return 7 if self.dies_at is not None and self.polls >= self.dies_at else None


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def run(tmp_path, *, port_after=2, dies_at=None, page=True, limit_s=90, environ=None):
    clock, made, process = Clock(), [], []

    def make_profile(prefix):
        path = tmp_path / f'{prefix}{len(made)}'
        path.mkdir()
        made.append(path)
        return str(path)

    def popen(args, **kwargs):
        process.append(FakeProcess(made[0], port_after, dies_at))
        process[0].args = args
        return process[0]

    stops = []
    kwargs = dict(limit_s=limit_s, environ=environ or {}, popen=popen, clock=clock, sleep=clock.sleep,
                  targets=lambda port: [{'type': 'page'}] if page else [{'type': 'service_worker'}],
                  stop=stops.append, make_profile=make_profile)
    return w.warm('chrome.exe', **kwargs), process, stops, made


def test_a_started_chrome_reports_when_its_port_and_first_page_appeared_then_is_stopped(tmp_path):
    result, process, stops, made = run(tmp_path)
    assert set(result) == {'port_s', 'target_s'} and 0 < result['port_s'] <= result['target_s'] < 1
    assert stops == process and not made[0].exists()  # Chrome is stopped and its profile removed
    assert '--headless=new' in process[0].args and f'--user-data-dir={made[0]}' in process[0].args  # the test's own flags
    assert process[0].args[-1] == 'about:blank' and '--no-sandbox' not in process[0].args


def test_ci_adds_no_sandbox_like_the_browser_driver(tmp_path):
    _, process, _, _ = run(tmp_path, environ={'CI': 'true'})
    assert '--no-sandbox' in process[0].args


def test_a_missing_port_ends_in_a_named_error_after_the_limit_and_still_cleans_up(tmp_path):
    with pytest.raises(w.WarmupError, match=r'no DevToolsActivePort within 5 s'):
        run(tmp_path, port_after=10 ** 9, limit_s=5)
    assert not list(tmp_path.iterdir())  # the profile is gone


def test_a_page_that_never_appears_is_a_different_named_error(tmp_path):
    with pytest.raises(w.WarmupError, match=r'no page target within 3 s \(DevToolsActivePort took'):
        run(tmp_path, page=False, limit_s=3)


def test_a_page_target_that_arrives_after_the_limit_is_a_failure_not_a_pass(tmp_path):
    # The /json answer came back late (inside its own 5 s read) but after the 60 s limit: the start was still too slow.
    clock = Clock()

    def slow_targets(port):
        clock.now += 61
        return [{'type': 'page'}]

    made = []

    def make_profile(prefix):
        path = tmp_path / 'p'
        path.mkdir()
        made.append(path)
        return str(path)

    def popen(args, **kwargs):
        return FakeProcess(made[0], port_after=1)

    with pytest.raises(w.WarmupError, match=r'first page target after 61\.\d s, over the 60 s limit'):
        w.warm('chrome.exe', limit_s=60, environ={}, popen=popen, clock=clock, sleep=clock.sleep,
               targets=slow_targets, stop=lambda process: None, make_profile=make_profile)


def test_a_chrome_that_exits_early_names_its_exit_code(tmp_path):
    with pytest.raises(w.WarmupError, match='Chrome exited with code 7 before it opened its DevTools port'):
        run(tmp_path, port_after=10 ** 9, dies_at=3)


def test_find_chrome_prefers_the_override_and_says_none_when_there_is_none():
    exists = lambda path: path in {'/override/chrome', w.CANDIDATES[0]}
    assert w.find_chrome({'LABHQ_TEST_CHROME': '/override/chrome'}, exists) == '/override/chrome'
    assert w.find_chrome({}, exists) == w.CANDIDATES[0]
    assert w.find_chrome({'LABHQ_TEST_CHROME': '/gone'}, lambda path: False) is None


def test_main_does_not_fail_where_there_is_no_chrome_and_fails_loudly_on_a_failed_start(monkeypatch, capsys):
    monkeypatch.setattr(w, 'find_chrome', lambda: None)
    assert w.main() == 0 and 'no Chrome found' in capsys.readouterr().out
    monkeypatch.setattr(w, 'find_chrome', lambda: 'chrome.exe')
    monkeypatch.setattr(w, 'warm', lambda chrome: (_ for _ in ()).throw(w.WarmupError('no DevToolsActivePort within 90 s')))
    assert w.main() == 1
    assert capsys.readouterr().out.startswith('::error title=Chrome warm-up failed::no DevToolsActivePort within 90 s')
    monkeypatch.setattr(w, 'warm', lambda chrome: {'port_s': 11.2, 'target_s': 31.0})
    assert w.main() == 0
    assert re.search(r'DevToolsActivePort 11.2 s, first page target 31.0 s', capsys.readouterr().out)


def test_the_cold_start_limit_is_the_drivers_default_start_budget_and_names_itself_when_missed():
    driver = (ROOT / 'tests/support/demo_driver.cjs').read_text(encoding='utf-8')
    assert f'const START_MS = {w.START_LIMIT_S * 1000};' in driver  # one number for "Chrome must start within"
    assert w.START_LIMIT_S == 60 and w.START_LIMIT_S > 30  # longer than the warmed test's 30 s, never shorter


def test_the_policy_document_says_where_the_cold_start_is_checked():
    text = (ROOT / 'docs/reference/ci-reuse.md').read_text(encoding='utf-8')
    assert 'Warm Chrome' in text and 'cold start 검증은 이 step이 맡고' in text and '데워진 Chrome의' in text
    assert '사전 기동 없이' not in text and '재시도·사전 기동' not in text  # the old no-pre-start rule is gone


def test_the_windows_job_warms_chrome_before_the_critical_browser_test_without_touching_its_limits():
    steps = yaml.safe_load((ROOT / '.github/workflows/test.yml').read_text(encoding='utf-8'))['jobs']['pytest-windows']['steps']
    warm_step = next(step for step in steps if step.get('run') == 'python scripts/ci_chrome_warmup.py')
    critical = next(step for step in steps if step.get('run', '').startswith('pytest ') and '--ci-phase critical' in step['run'])
    assert steps.index(warm_step) < steps.index(critical)
    assert warm_step['timeout-minutes'] == 2 and 'continue-on-error' not in warm_step
    assert critical['timeout-minutes'] == 3  # the browser test is not given more time
    browser = (ROOT / 'tests/web_resizable_panels.cjs').read_text(encoding='utf-8')
    assert 'STARTUP_BUDGET_MS = 30000' in browser  # the start limit keeps its meaning
