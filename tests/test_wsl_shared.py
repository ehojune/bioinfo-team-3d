"""Second-review regressions: all processes, WSL and network access are mocked."""
import asyncio
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from labhq import cli, wsl_bootstrap, wsl_setup
from labhq.gateway.server import Hub
from labhq.models import AgentSpec
from labhq.policy import evaluate_tool
from labhq.settings import DataZone, RunnerSettings, Settings
from test_wsl_setup import settings, restore_bootstrap_environment, READY


@pytest.mark.parametrize("connected", [False, True])
def test_only_connected_wsl_claims_bioinfo(settings, connected):
    settings.runner.wsl.enabled = True
    hub = Hub(settings)
    staff = [AgentSpec(id="bioinfo-agent", name="Bio", role="analysis", engine="mock").summary()]
    hub.runners["windows"] = object()
    if connected:
        hub.runners["lab-wsl"] = object()
    hub.set_roster("lab-wsl", staff, {"runtime_env": "wsl", "wsl_readiness": READY})
    hub.set_roster("windows", staff, {"runtime_env": "windows"})
    assert hub.agent_runner["bioinfo-agent"] == ("lab-wsl" if connected else "windows")
    if connected:
        hub.unregister_runner("lab-wsl", hub.runners["lab-wsl"])
        assert hub.agent_runner["bioinfo-agent"] == "windows"


def test_failed_connect_does_not_enable_or_rewrite(settings, monkeypatch):
    monkeypatch.setattr(wsl_setup, "_shared_settings", lambda s: {"workspace_root": "/mnt/c/Shared/Runs",
                                                               "policy": s.policy.model_dump()}, raising=False)
    monkeypatch.setattr(wsl_setup, "distributions", lambda: [settings.runner.wsl.distro])
    monkeypatch.setattr(wsl_setup, "_remote", lambda *a, **kw: {"ok": True, **READY, "url": "ws://host"})
    monkeypatch.setattr(wsl_setup, "control", lambda *a: (_ for _ in ()).throw(wsl_setup.WslError("offline")))
    before = Path(settings.config_path).read_bytes()
    with pytest.raises(wsl_setup.WslError):
        wsl_setup.setup(settings)
    assert Path(settings.config_path).read_bytes() == before
    assert not settings.runner.wsl.enabled


def test_duplicate_ids_validation():
    with pytest.raises(ValueError, match="runner.id"):
        RunnerSettings(id="same", wsl={"enabled": True, "id": "same"})


@pytest.mark.parametrize("entry", ["control", "up", "runner", "background"])
def test_duplicate_ids_refused_before_start(settings, monkeypatch, entry):
    settings.runner.wsl.id = settings.runner.id
    settings.runner.wsl.enabled = True
    monkeypatch.setattr(wsl_setup, "_remote", lambda *a, **kw: pytest.fail("remote must not start"))
    monkeypatch.setattr(cli.instance_guard, "check", lambda *a: pytest.fail("up must reject first"))
    from labhq.runner.daemon import Runner
    with pytest.raises((ValueError, wsl_setup.WslError), match="runner.id"):
        if entry == "control":
            wsl_setup.control(settings, "start")
        elif entry == "up":
            cli._up_unlocked(settings)
        elif entry == "background":
            cli._start_background(settings, "runner", popen=lambda *a, **kw: pytest.fail("must not spawn"))
        else:
            Runner(settings)


@pytest.mark.parametrize("scheme,port", [("ws", 80), ("wss", 443)])
def test_nat_default_port(monkeypatch, scheme, port):
    identity = {"config_id": "test"}
    monkeypatch.setattr(wsl_bootstrap, "run", lambda *a: SimpleNamespace(stdout=b"default via 192.0.2.1 dev eth0"))
    calls = []
    def fetch(url, **kw):
        calls.append(url)
        if "localhost" in url:
            raise OSError("not forwarded")
        return io.StringIO(json.dumps({"service": "labhq gateway", "identity": identity}))
    monkeypatch.setattr(wsl_bootstrap, "build_opener", lambda *a: SimpleNamespace(open=fetch))
    assert wsl_bootstrap.gateway_url({"gateway_url": f"{scheme}://localhost", "identity": identity}) == f"{scheme}://192.0.2.1:{port}"


def test_shared_workspace_and_policy_refresh(settings, monkeypatch):
    settings.runner.workspace_root = "C:/Shared/Runs"
    settings.policy.private_paths = ["C:/Private"]
    settings.policy.data_zones = [DataZone(path="D:/Restricted")]
    settings.policy.approvals.auto_allow_tools = []
    settings.policy.approvals.bash_ask_patterns = ["sensitive_command"]
    settings.policy.budget.per_task_usd = 0.25
    monkeypatch.setattr(wsl_setup, "distributions", lambda: [settings.runner.wsl.distro])
    monkeypatch.setattr(wsl_setup, "control", lambda *a: None)
    monkeypatch.setattr(wsl_setup, "check", lambda *a: [])
    payloads = []
    def remote(s, action, **kw):
        if action == "configure":
            payloads.append(kw)
        return {"ok": True, **READY, "url": "ws://host"}
    monkeypatch.setattr(wsl_setup, "_remote", remote)
    wsl_setup.setup(settings)
    config = payloads[-1]
    assert config["workspace_root"] == "/mnt/c/Shared/Runs"
    policy = type(settings.policy).model_validate(config["policy"])
    assert policy.private_paths == ["/mnt/c/Private"]
    assert policy.data_zones[0].path == "/mnt/d/Restricted"
    assert policy.budget == settings.policy.budget
    for tool, args in [("WebFetch", {"url": "https://example.org"}), ("Bash", {"command": "sensitive_command"})]:
        assert evaluate_tool(tool, args, policy).action == evaluate_tool(tool, args, settings.policy).action
        assert evaluate_tool(tool, args, policy).action != "allow"
    assert evaluate_tool("Read", {"file_path": "D:/Restricted/data.txt"}, settings.policy, windows=True).action == "deny"
    assert evaluate_tool("Read", {"file_path": "/mnt/d/Restricted/data.txt"}, policy, windows=False).action == "deny"
    settings.policy.budget.per_task_usd = 0.1
    wsl_setup.setup(settings)
    assert payloads[-1]["policy"]["budget"]["per_task_usd"] == 0.1


@pytest.mark.parametrize("path", [r"\\server\private", "/home/private"])
def test_unmappable_policy_refuses_setup(settings, monkeypatch, path):
    settings.policy.private_paths = [path]
    settings.runner.workspace_root = "C:/Shared/Runs"
    monkeypatch.setattr(wsl_setup, "distributions", lambda: [settings.runner.wsl.distro])
    monkeypatch.setattr(wsl_setup, "_remote", lambda *a, **kw: {"ok": True, "nextflow": True, "url": "ws://host"})
    monkeypatch.setattr(wsl_setup, "control", lambda *a: None)
    monkeypatch.setattr(wsl_setup, "check", lambda *a: [])
    with pytest.raises(wsl_setup.WslError):
        wsl_setup.setup(settings)


@pytest.mark.parametrize("raw", [r"c:\Shared\Runs\Step", "C:/Shared/Runs/Step", "/mnt/C/Shared/Runs/Step"])
def test_shared_path_roundtrip(raw):
    from labhq.wsl_paths import shared_path
    assert shared_path(raw, "wsl") == "/mnt/c/Shared/Runs/Step"
    assert shared_path(raw, "windows") == "C:/Shared/Runs/Step"


@pytest.mark.asyncio
async def test_gateway_translates_task_and_results(settings):
    hub = Hub(settings)
    messages = []
    async def send(text):
        messages.append(json.loads(text))
    hub.runners["lab-wsl"] = SimpleNamespace(send_text=send)
    hub.runner_locks["lab-wsl"] = asyncio.Lock()
    hub.runner_capabilities["lab-wsl"] = {"runtime_env": "wsl"}
    await hub.send_runner("lab-wsl", {"type": "task", "task": {"meta": {
        "workdir": "C:/Runs/new", "upstream_dirs": [r"c:\Runs\old"],
        "upstream_steps": {"first": "C:/Runs/old"}}}})
    assert messages[0]["task"]["meta"] == {"workdir": "/mnt/c/Runs/new", "upstream_dirs": ["/mnt/c/Runs/old"],
                                          "upstream_steps": {"first": "/mnt/c/Runs/old"}}
    from labhq.wsl_paths import message_paths
    result = {"type": "task.result", "data": {"workdir": "/mnt/c/Runs/new", "outputs": ["outputs/a.txt"],
                                                "output_sha256": {"outputs/a.txt": "hash"}}}
    assert message_paths(result, "windows")["data"] == {
        "workdir": "C:/Runs/new", "outputs": ["outputs/a.txt"], "output_sha256": {"outputs/a.txt": "hash"}}
    future = asyncio.get_running_loop().create_future()
    hub.futures["shared-result"] = future
    await hub.on_runner_message("lab-wsl", {"type": "task.result", "task_id": "shared-result", "data": {
        "task_id": "shared-result", "agent_id": "bioinfo-agent", "ok": True, "workdir": "/mnt/c/Runs/new"}})
    assert future.result().workdir == "C:/Runs/new"


def test_wsl_linux_only_workspace_refused():
    with pytest.raises(ValueError, match="shared"):
        RunnerSettings(environment="wsl", workspace_root="/home/runner/runs")


@pytest.mark.parametrize("target", ["wsl", "windows"])
def test_mixed_plan_links_upstream_files(tmp_path, target):
    from labhq.models import Task
    from labhq.runner.workspace import TaskWorkspace
    from labhq.wsl_paths import message_paths
    upstream = tmp_path / "first"
    (upstream / "outputs").mkdir(parents=True)
    (upstream / "outputs/data.txt").write_text("shared result", encoding="utf-8")
    raw = "C:/Shared/first" if target == "wsl" else "/mnt/c/Shared/first"
    meta = message_paths({"upstream_dirs": [raw], "upstream_steps": {"first": raw}}, target)
    expected = "/mnt/c/Shared/first" if target == "wsl" else "C:/Shared/first"
    assert meta["upstream_steps"]["first"] == meta["upstream_dirs"][0] == expected
    # Fake the two OS mount views; linking and reading use real temporary files.
    mounts = {expected: upstream}
    agent = AgentSpec(id="analyst", name="Analyst", role="analysis", engine="mock")
    ws = TaskWorkspace(tmp_path, Task(agent_id="analyst", prompt="read upstream"), agent)
    assert ws.link_inputs({k: mounts[v] / "outputs" for k, v in meta["upstream_steps"].items()}) == {}
    assert (ws.dir / "inputs/first/data.txt").read_text(encoding="utf-8") == "shared result"


def test_shared_wsl_bundle_collects_different_hostname(tmp_path, monkeypatch):
    from test_request_bundle import pending_bundle_fixture
    from labhq.request_bundle import build_request_bundle
    from labhq import wsl_paths
    s, req, task = pending_bundle_fixture(tmp_path, "shared-wsl")
    result = req["results"]["direct"]
    workdir = Path(result["workdir"])
    manifest = {"host": "other-wsl-hostname", "runs": {result["task_id"]: {
        "input_files": [{"path": "/mnt/c/Shared/input.tsv", "sha256": "input-hash"}]}}}
    (workdir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    wire_root = "/mnt/c/Shared/Runs"
    wire_workdir = wire_root + "/" + workdir.relative_to(s.runner.workspace_root).as_posix()
    result["workdir"] = wire_workdir
    task["runner_id"] = "lab-wsl"
    original = wsl_paths.shared_path
    def mounted(raw, target):
        if raw == wire_root:
            return s.runner.workspace_root
        if raw == wire_workdir:
            return str(workdir)
        if raw == "/mnt/c/Shared/input.tsv":
            return str(tmp_path / "input.tsv")
        return original(raw, target)
    monkeypatch.setattr(wsl_paths, "shared_path", mounted)
    bundle = build_request_bundle(req, s, {result["task_id"]: task}, {
        "lab-wsl": {"runtime_env": "wsl", "workspace_root": wire_root}})
    destination = Path(s.runner.workspace_root) / "requests/shared-wsl/steps/direct/outputs/answer.txt"
    assert destination.read_text(encoding="utf-8") == "answer\n"
    assert bundle["status"] != "incomplete"
    inputs = destination.parents[3] / "INPUTS.tsv"
    assert str(tmp_path / "input.tsv") in inputs.read_text(encoding="utf-8")
    assert json.loads((workdir / "manifest.json").read_text(encoding="utf-8")) == manifest


def test_nonshared_wsl_bundle_keeps_remote_host_refusal(tmp_path):
    from test_request_bundle import pending_bundle_fixture
    from labhq.request_bundle import build_request_bundle, RemoteRunnerBundle
    s, req, task = pending_bundle_fixture(tmp_path, "remote-wsl")
    result = req["results"]["direct"]
    (Path(result["workdir"]) / "manifest.json").write_text(json.dumps({"host": "remote-host"}), encoding="utf-8")
    task["runner_id"] = "remote"
    with pytest.raises(RemoteRunnerBundle):
        build_request_bundle(req, s, {result["task_id"]: task}, {
            "remote": {"runtime_env": "wsl", "workspace_root": "/home/remote/runs"}})


def test_bootstrap_persists_policy_without_defaults(settings, tmp_path, monkeypatch):
    root = tmp_path / "isolated-wsl"
    source = Path(__file__).resolve().parents[1]
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(wsl_bootstrap.sys, "prefix", str(root / "venv"))
    policy = settings.policy.model_dump()
    policy["approvals"]["auto_allow_tools"] = []
    policy["budget"]["per_task_usd"] = 0.2
    policy["private_paths"] = ["/mnt/c/Private"]
    data = {"action": "configure", "root": str(root), "source": str(source), "id": "lab-wsl",
            "url": "ws://192.0.2.1:43123", "token": "test-only-token", "bioinfo_agent": "/opt/bioinfo-agent",
            "supervisor": {"restart": False}, "workspace_root": "/mnt/c/Shared/Runs", "policy": policy,
            "reference_roots": ["/mnt/d/Refs", "/mnt/c/Shared/project"]}
    assert wsl_bootstrap.main(data)["ok"]
    actual = Settings.load(str(root / "labhq.yaml"))
    assert actual.policy.model_dump() == policy
    assert actual.runner.workspace_root == "/mnt/c/Shared/Runs"
    assert actual.runner.reference_roots == data["reference_roots"]
    data["private_defaults"] = True
    monkeypatch.setattr(cli, "_supervised", lambda *a: False)
    monkeypatch.setattr(cli, "_process_record", lambda *a: ("missing", None, None))
    assert wsl_bootstrap.main(data)["ok"]
    first = (root / "labhq.yaml").read_bytes()
    assert wsl_bootstrap.main(data)["ok"]
    assert (root / "labhq.yaml").read_bytes() == first
    actual = Settings.load(str(root / "labhq.yaml"))
    assert "/mnt/c/Private" in actual.policy.private_paths
    assert str(root / "labhq.yaml") in actual.policy.private_paths
    assert str(root / "codex-staff") in actual.policy.private_paths


def test_default_windows_private_paths_are_expanded_before_transfer(settings, monkeypatch):
    from labhq import private_paths
    settings.runner.workspace_root = "C:/Shared/Runs"
    monkeypatch.setattr(private_paths, "configured_private_paths", lambda *a: [
        ("C:/Users/operator/.ssh", "ssh"), ("C:/Private/config.yaml", "config")])
    data = wsl_setup._shared_settings(settings)
    assert data["private_defaults"] is True
    assert data["policy"]["private_paths"] == ["/mnt/c/Users/operator/.ssh", "/mnt/c/Private/config.yaml"]
