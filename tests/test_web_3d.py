"""Shared office state and bounded static routes; no new runtime dependencies."""
import ast
import asyncio
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

import pytest
import uvicorn
import websockets
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from labhq.gateway.server import WEB, create_app
from labhq.settings import Settings
from labhq.util import free_port
from tests.support.node_binary import node_binary, node_env


@pytest.fixture
def client(tmp_path):
    settings = Settings()
    settings.gateway.state_dir = str(tmp_path / 'state')
    return TestClient(create_app(settings)), settings


@pytest.mark.parametrize('path,mime', [
    ('/3d', 'text/html'), ('/3d/', 'text/html'),
    ('/state.js', 'text/javascript'), ('/3d/src/main.js', 'text/javascript'),
    ('/3d/src/live.js', 'text/javascript'),
    ('/vendor/three/build/three.module.js', 'text/javascript'),
    ('/vendor/three/build/three.core.js', 'text/javascript'),
    ('/vendor/three/examples/jsm/loaders/GLTFLoader.js', 'text/javascript'),
    ('/3d/assets/placeholder.gltf', 'model/gltf+json'),
])
def test_static_routes(client, path, mime):
    http, _ = client
    response = http.get(path)
    assert response.status_code == 200
    assert response.headers['content-type'].startswith(mime)
    if mime == 'text/html':
        assert 'window.LABHQ_BOOT={"mode":"live"}' in response.text
        assert '<!--LABHQ_BOOT-->' not in response.text


def test_same_shell_and_data_auth_as_2d(client):
    http, settings = client
    for path in ('/', '/3d'):
        assert http.get(path).status_code == 200  # public login shell, no data
    redirect = http.get('/3d?token=test-client&demo=1', follow_redirects=False)
    assert redirect.headers['location'] == '/3d/?token=test-client&demo=1'
    for query in ('', '?token=wrong'):
        with pytest.raises(WebSocketDisconnect) as error:
            with http.websocket_connect('/ws/client' + query) as ws:
                ws.receive_text()
        assert error.value.code == 1008
    with http.websocket_connect('/ws/client?token=' + settings.gateway.client_token) as ws:
        assert json.loads(ws.receive_text())['type'] == 'snapshot'
    assert http.get('/api/approvals').status_code == 401


@pytest.mark.asyncio
async def test_wrong_client_token_reaches_a_real_websocket_as_1008(tmp_path):
    settings = Settings()
    settings.gateway.state_dir = str(tmp_path / 'state')
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(create_app(settings), host='127.0.0.1', port=port,
                                           log_level='warning'))
    serving = asyncio.create_task(server.serve())
    try:
        for _ in range(200):
            if server.started:
                break
            await asyncio.sleep(0.01)
        assert server.started
        async with websockets.connect(f'ws://127.0.0.1:{port}/ws/client?token=wrong') as ws:
            with pytest.raises(websockets.exceptions.ConnectionClosedError) as error:
                await ws.recv()
        assert error.value.rcvd.code == 1008
        assert error.value.rcvd.reason == 'token'
    finally:
        server.should_exit = True
        await serving


@pytest.mark.parametrize('path', [
    '/3d/src/%2e%2e/%2e%2e/index.html',
    '/3d/assets/%2e%2e/%2e%2e/%2e%2e/gateway/server.py',
    '/3d/src/..%5c..%5cindex.html', '/3d/src/C:%5cWindows%5cwin.ini',
    '/vendor/three/%2e%2e/%2e%2e/index.html',
    '/vendor/three/%2fetc/passwd', '/vendor/three/..%5c..%5cindex.html',
    '/3d/assets/generate_placeholder.py', '/3d/README.md', '/3d/src/missing.js',
])
def test_static_paths_cannot_escape_or_expose_sources(client, path):
    assert client[0].get(path).status_code in (403, 404)


def test_symlink_escape(client, tmp_path, monkeypatch):
    from labhq.gateway import server
    web = tmp_path / 'web'
    assets = web / 'lab3d' / 'assets'
    assets.mkdir(parents=True)
    outside = tmp_path / 'outside.js'
    outside.write_text('private', encoding='utf-8')
    try:
        (assets / 'link.js').symlink_to(outside)
    except OSError:
        pytest.skip('symlink creation unavailable')
    monkeypatch.setattr(server, 'WEB', web)
    assert client[0].get('/3d/assets/link.js').status_code == 404


def test_both_pages_load_same_reducer_and_keep_dom_approvals():
    for path in (WEB / 'index.html', WEB / 'lab3d/index.html'):
        html = path.read_text(encoding='utf-8')
        scripts = re.findall(r'<script src="([^"]*state\.js)"></script>', html)
        assert len(scripts) == 1
        assert (path.parent / scripts[0]).resolve() == (WEB / 'state.js').resolve()
        assert 'id="approvals"' in html
    assert 'href="/3d/"' in (WEB / 'index.html').read_text(encoding='utf-8')
    assert 'href="/"' in (WEB / 'lab3d/index.html').read_text(encoding='utf-8')
    source = (WEB / 'state.js').read_text(encoding='utf-8')
    for forbidden in ('document.', 'window.', 'setTimeout(', 'WebSocket(', 'fetch('):
        assert forbidden not in source


def test_3d_uses_shared_visual_state_for_pose_and_marker():
    main = (WEB / 'lab3d/src/main.js').read_text(encoding='utf-8')
    assert 'state:pose(visual(a))' in main
    assert 'c.state=pose(visual(a))' in main
    assert 'c.skin.setState(c.state)' in main
    assert 'c.status.update(c.state' in main
    live = (WEB / 'lab3d/src/live.js').read_text(encoding='utf-8')
    assert 'syncDecisionCards' in live and 'prompt(' not in live


def test_package_data_includes_office_assets():
    package = WEB.parent
    config = (package.parent / 'pyproject.toml').read_text(encoding='utf-8')
    patterns = ast.literal_eval(re.search(r'labhq = (\[[\s\S]*?\])', config)[1])
    included = {p for pattern in patterns for p in package.glob(pattern) if p.is_file()}
    needed = {WEB / 'state.js', WEB / 'lab3d/index.html', *(WEB / 'ui').glob('*.js'), *(WEB / 'ui').glob('*.css')}
    for folder in (WEB / 'lab3d/src', WEB / 'lab3d/assets', WEB / 'vendor/three'):
        needed.update(p for p in folder.rglob('*') if p.suffix in {'.js', '.gltf', '.glb', '.bin'})
    assert needed <= included


# Legacy scripts print their own success message; node:test uses fail 0.
NODE_WEB_MARKERS = {
    'web_disk_preflight.cjs': 'disk preflight web state: passed',
    'web_recruit_before_plan.cjs': 'pre-plan recruitment card passed',
    'web_recruiter_capybara.cjs': 'recruiter capybara identity, unique roster and artwork: OK',
    'web_solo_lion.cjs': 'solo lion default, unique roster and artwork: OK',
    'web_state.cjs': 'isolation and bounds: OK',
    'web_live.cjs': 'reconnect and replay gap: OK',
    'web_command_center.cjs': 'keyed decisions, staff strip and tabs: OK',
    'web_issue126.cjs': 'full answer on demand in 2.5D and 3D: OK',
    'web_issue75.cjs': 'mobile staff collapse and bounded grouped decision history: OK',
    'web_ask.cjs': 'web_ask: ask/answer feed, refusal, targets and replay passed',
    'web_issue106.cjs': 'snapshot task priority, cancellation and unknown/HPC wait labels: OK',
    'web_issue87.cjs': 'snapshot restores an active step after a 200-event replay gap: OK',
    'web_issue58.cjs': 'web issue58 tests passed',
    'web_assumptions.cjs': 'assumptions web tests passed',
    'web_agent_trace.cjs': 'agent trace web tests passed',
    'web_agent_capabilities.cjs': 'agent capability web tests passed',
    'web_office_props.cjs': 'office prop tab entrances: OK',
    'web_cost_by_agent.cjs': 'cost by agent web tests passed',
    'web_solo_route.cjs': 'solo route web tests passed',
    'web_work_kind.cjs': 'work kind web tests passed',
    'web_request_bundle.cjs': 'request bundle web tests passed',
    'web_display_followups.cjs': 'display follow-up web tests passed',
    'web_environment.cjs': 'environment failure web tests passed',
    'web_checklist_skip.cjs': 'checklist skip web tests passed',
    'web_request_lifecycle.cjs': 'request lifecycle web tests passed',
    'web_staff_control.cjs': 'staff control web tests passed',
    'web_staff_control_tasks.cjs': 'staff control task rows web tests passed',
    'web_staff_control_steps.cjs': 'staff control step web tests passed',
    'web_staff_control_scope.cjs': 'staff control scope web tests passed',
    'web_trial_ready.cjs': 'trial readiness web tests passed',
}
NODE_WEB_TESTS = [
    (path.name, NODE_WEB_MARKERS.get(path.name, 'fail 0'))
    for path in sorted(Path(__file__).parent.glob('web_*.cjs'))
]


@pytest.mark.parametrize('filename,marker', NODE_WEB_TESTS)
def test_office_in_node(filename, marker):
    node = node_binary()
    if not node:
        pytest.skip('Node is optional')
    script = Path(__file__).with_name(filename)
    # The optional installed-browser checks wait for Chrome's start and the demo viewports (node:test budgets of 150 s and 240 s).
    # #833: the required resizable case now has separate cold-start/UI budgets inside one 60 s hard cap.
    # Other Node cases keep their existing limits.
    timeout = {'web_office_browser.cjs': 170, 'web_visual_demo.cjs': 260,
               'web_visual_tooling.cjs': 170, 'web_resizable_panels.cjs': 60}.get(filename, 30)
    # On Windows the demo driver starts Chrome inside the owned KILL_ON_CLOSE Job wrapper with this exact
    # interpreter (#842); it never falls back to a PATH or Store python.
    base_env = node_env(node, {**os.environ, 'LABHQ_TEST_PYTHON': sys.executable})
    # #833: the resizable script runs once per viewport, in this order, each under its own watchdog and with that
    # watchdog's absolute end (epoch ms), so its browser work stops early enough to close what it owns before the kill
    # below would skip that close. Every invocation must pass; none may be skipped when the browser is required.
    resizable = {'desktop': 'desktop splitters drag, respond to arrows, persist and reset',
                 'phone': 'phone layout hides the splitters and keeps the office full width'}
    for viewport in (list(resizable) if filename == 'web_resizable_panels.cjs' else [None]):
        env = dict(base_env)
        launch = None
        if viewport:
            env['LABHQ_RESIZABLE_VIEWPORT'] = viewport
            # One wall-clock reading sets the deadline; a monotonic one measures how long this invocation took.
            launch = (time.time(), time.monotonic())
            env['LABHQ_TEST_OUTER_DEADLINE_MS'] = str(int((launch[0] + timeout) * 1000))
        label = f'{filename} ({viewport})' if viewport else filename

        def timing():
            # #833: fixed numbers only. `*_epoch_ms` is the wall clock that the script's own timing rows also use;
            # `returned_after_mono_ms` is this process's monotonic clock from just before the launch to now.
            if launch is None:
                return ''
            return '\nresizable timing (python): ' + json.dumps({
                'viewport': viewport, 'launch_epoch_ms': int(launch[0] * 1000),
                'deadline_epoch_ms': int((launch[0] + timeout) * 1000),
                'returned_after_mono_ms': round((time.monotonic() - launch[1]) * 1000)})
        try:
            result = subprocess.run([node, str(script)], capture_output=True, text=True, encoding='utf-8',
                                    timeout=timeout, env=env)
        except subprocess.TimeoutExpired as exc:
            # TimeoutExpired may contain bytes even with text=True. Keep partial TAP
            # and browser phase diagnostics; a timeout remains a failure.
            def captured(value):
                return value.decode('utf-8', errors='replace') if isinstance(value, bytes) else (value or '')
            raise AssertionError(f'{label} exceeded {timeout}s{timing()}\nstdout:\n{captured(exc.output)}'
                                 f'\nstderr:\n{captured(exc.stderr)}') from exc
        output = f'{label}{timing()}\n{result.stdout}{result.stderr}'
        assert result.returncode == 0, output
        assert marker in result.stdout, output
        if viewport and os.environ.get('LABHQ_REQUIRE_RESIZABLE_BROWSER') == '1':
            # A Python PASS alone must not certify an optional inner Node skip, or another viewport's test.
            assert re.search(r'^# pass 3\s*$', result.stdout, re.MULTILINE), output
            assert re.search(r'^# skipped 0\s*$', result.stdout, re.MULTILINE), output
            assert re.search(r'^ok 3 - ' + re.escape(resizable[viewport]) + r'\s*$', result.stdout, re.MULTILINE), output
        if (filename in ('web_office_focus_layout.cjs', 'web_visual_tooling.cjs') and os.name == 'nt'
                and os.environ.get('GITHUB_ACTIONS') == 'true'):
            # #842: on hosted Windows the real-Chrome cleanup regressions (forced path included) must run; an inner
            # node:test skip would otherwise still print "fail 0" and pass this parent. Chrome stays optional elsewhere.
            assert re.search(r'^# skipped 0\s*$', result.stdout, re.MULTILINE), result.stdout + result.stderr


def test_every_node_web_test_runs_in_ci():
    collected = dict(test_office_in_node.pytestmark[0].args[1])
    on_disk = {path.name for path in Path(__file__).parent.glob('web_*.cjs')}
    assert set(collected) == on_disk
    assert NODE_WEB_MARKERS.keys() <= on_disk
    assert all(collected[name] == NODE_WEB_MARKERS.get(name, 'fail 0') for name in on_disk)
    assert all(isinstance(marker, str) and marker for marker in collected.values())


def test_browser_failure_profile_diagnostic_is_bounded_without_browser():
    """#833: a fake directory stream proves the failure receipt is bounded and closes its handle."""
    node = node_binary()
    if not node:
        pytest.skip('Node is optional')
    driver = Path(__file__).parent / 'support' / 'demo_driver.cjs'
    code = r"""
const assert = require('node:assert/strict');
const {profileActivity} = require(process.argv[1]);
const file = name => ({name, isFile: () => true, isDirectory: () => false});
let reads = 0, closes = 0;
const many = () => ({readSync: () => { reads++; return file('private-' + reads); }, closeSync: () => { closes++; }});
assert.deepEqual(profileActivity('mock', many),
  {entries: 96, files: 96, directories: 0, truncated: true, unreadable: false});
assert.equal(reads, 96);
assert.equal(closes, 1);
const missing = () => { throw Error('private path'); };
assert.deepEqual(profileActivity('mock', missing),
  {entries: 0, files: 0, directories: 0, truncated: false, unreadable: true});
const broken = () => ({readSync: () => { throw Error('private path'); }, closeSync: () => { closes++; }});
assert.deepEqual(profileActivity('mock', broken),
  {entries: 0, files: 0, directories: 0, truncated: false, unreadable: true});
assert.equal(closes, 2);
console.log('bounded profile diagnostic: OK');
"""
    result = subprocess.run([node, '-e', code, str(driver)], capture_output=True, text=True, timeout=5,
                            env=node_env(node))
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == 'bounded profile diagnostic: OK'
