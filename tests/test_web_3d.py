"""Shared office state and bounded static routes; no new runtime dependencies."""
import ast
import asyncio
import json
from pathlib import Path
import re
import shutil
import subprocess

import pytest
import uvicorn
import websockets
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from labhq.gateway.server import WEB, create_app
from labhq.settings import Settings
from labhq.util import free_port


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
    needed = {WEB / 'state.js', WEB / 'lab3d/index.html', *(WEB / 'ui').glob('*.js')}
    for folder in (WEB / 'lab3d/src', WEB / 'lab3d/assets', WEB / 'vendor/three'):
        needed.update(p for p in folder.rglob('*') if p.suffix in {'.js', '.gltf', '.glb', '.bin'})
    assert needed <= included


@pytest.mark.parametrize('filename,marker', [
    ('web_state.cjs', 'isolation and bounds: OK'),
    ('web_live.cjs', 'reconnect and replay gap: OK'),
    ('web_command_center.cjs', 'keyed decisions, staff strip and tabs: OK'),
    ('web_decision_detail.cjs', 'fail 0'),
    ('web_issue126.cjs', 'full answer on demand in 2.5D and 3D: OK'),
    ('web_issue184.cjs', 'fail 0'),
    ('web_decision_focus.cjs', 'fail 0'),
    ('web_decision_summary.cjs', 'fail 0'),
    ('web_scope_card.cjs', 'fail 0'),
    ('web_clarify_options.cjs', 'fail 0'),
    ('web_cp2_evidence.cjs', 'fail 0'),
    ('web_issue75.cjs', 'mobile staff collapse and bounded grouped decision history: OK'),
    ('web_followup.cjs', 'fail 0'),
    ('web_refs.cjs', 'fail 0'),
    ('web_ask.cjs', 'web_ask: ask/answer feed, refusal, targets and replay passed'),
    ('web_issue106.cjs', 'snapshot task priority, cancellation and hibernating label: OK'),
    ('web_issue87.cjs', 'snapshot restores an active step after a 200-event replay gap: OK'),
    # Node files written after this list existed ran only by hand; CI runs node through this test alone.
    ('web_issue58.cjs', 'web issue58 tests passed'),
    ('web_assumptions.cjs', 'assumptions web tests passed'),
    ('web_agent_trace.cjs', 'agent trace web tests passed'),
    ('web_agent_capabilities.cjs', 'agent capability web tests passed'),
    ('web_office_props.cjs', 'office prop tab entrances: OK'),
    ('web_cost_by_agent.cjs', 'cost by agent web tests passed'),
    ('web_solo_route.cjs', 'solo route web tests passed'),
    ('web_work_kind.cjs', 'work kind web tests passed'),
    ('web_request_bundle.cjs', 'request bundle web tests passed'),
    ('web_display_followups.cjs', 'display follow-up web tests passed'),
    ('web_environment.cjs', 'environment failure web tests passed'),
    ('web_checklist_skip.cjs', 'checklist skip web tests passed'),
    ('web_cards_status.cjs', 'fail 0'),
    ('web_request_lifecycle.cjs', 'request lifecycle web tests passed'),
])
def test_office_in_node(filename, marker):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is optional')
    script = Path(__file__).with_name(filename)
    result = subprocess.run([node, str(script)], capture_output=True, text=True, encoding='utf-8', timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert marker in result.stdout, result.stdout + result.stderr


def test_every_node_web_test_runs_in_ci():
    # A .cjs file outside the list above never runs in CI (five did not, found 2026-10-04).
    listed = {name for name, _marker in test_office_in_node.pytestmark[0].args[1]}
    on_disk = {path.name for path in Path(__file__).parent.glob('web_*.cjs')}
    assert on_disk <= listed, sorted(on_disk - listed)
