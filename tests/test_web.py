"""The gateway serves the web office and keeps phone sockets alive on stale approvals."""

import json

from fastapi.testclient import TestClient

from labhq.gateway.server import create_app
from labhq.settings import Settings


def test_office_page_manifest_and_stale_approval():
    s = Settings()
    client = TestClient(create_app(s))
    page = client.get("/")
    assert page.status_code == 200 and 'window.LABHQ_BOOT={"mode":"live"}' in page.text
    assert "<!--LABHQ_BOOT-->" not in page.text and 'id="zones"' in page.text
    assert client.get("/manifest.webmanifest").json()["short_name"] == "labhq"
    assert client.get("/icon.svg").headers["content-type"].startswith("image/svg+xml")
    assert client.get("/api/health").json()["service"] == "labhq gateway"
    with client.websocket_connect(f"/ws/client?token={s.gateway.client_token}") as ws:
        assert json.loads(ws.receive_text())["type"] == "snapshot"
        ws.send_text(json.dumps({"type": "approval.resolve", "id": "appr_gone", "approved": True}))
        assert json.loads(ws.receive_text())["type"] == "approval.stale"  # still connected


def test_demo_page_without_gateway_boot_marker():
    from labhq.gateway import server
    html = (server.WEB / "index.html").read_text(encoding="utf-8")
    assert "<!--LABHQ_BOOT-->" in html  # published/static copies fall back to demo mode
