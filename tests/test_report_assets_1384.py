"""Request assets are isolated and need the same client token as reports."""
from fastapi.testclient import TestClient

from labhq.gateway.server import create_app
from labhq.settings import DataZone, Settings


def test_asset_access_is_authenticated_and_confined_to_request_bundle(tmp_path):
    settings = Settings()
    settings.gateway.state_dir = str(tmp_path / "state")
    settings.runner.workspace_root = str(tmp_path / "runs")
    bundle = tmp_path / "runs" / "requests" / "req_one"
    (bundle / "steps" / "s1" / "outputs").mkdir(parents=True)
    (bundle / "steps" / "s1" / "outputs" / "result.csv").write_bytes(b"x\n1\n")
    (tmp_path / "secret.txt").write_text("secret", encoding="utf-8")
    app = create_app(settings)
    app.state.hub.requests["req_one"] = {"id": "req_one", "status": "done", "report": "# Result"}
    client = TestClient(app)
    route = "/api/requests/req_one/report-asset"
    headers = {"Authorization": f"Bearer {settings.gateway.client_token}"}
    params = {"path": "steps/s1/outputs/result.csv"}
    assert client.get(route, params=params).status_code == 401
    result = client.get(route, params=params, headers=headers)
    assert result.status_code == 200 and result.text == "x\n1\n"
    assert result.headers["x-content-type-options"] == "nosniff"
    for path in ("../../secret.txt", "/secret.txt", "steps/../secret.txt", "C:/secret.txt"):
        assert client.get(route, params={"path": path}, headers=headers).status_code == 404
    assert client.get("/api/requests/unknown/report-asset", params=params, headers=headers).status_code == 404
    app.state.hub.requests["req_one"]["results"] = {"s1": {"workdir_id": "worker-dir"}}
    mapped = client.get(route, params={"path": "worker-dir/outputs/result.csv"}, headers=headers)
    assert mapped.status_code == 200 and mapped.content == result.content
    app.state.hub.requests["req_one"]["status"] = "running"
    assert client.get(route, params=params, headers=headers).status_code == 409
    app.state.hub.requests["req_one"]["status"] = "done"
    settings.policy.data_zones = [DataZone(path=str(tmp_path / "controlled"), level="restricted")]
    app.state.hub.requests["req_one"]["text"] = str(tmp_path / "controlled" / "data.tsv")
    assert client.get(route, params=params, headers=headers).status_code == 409
