import base64
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx

from labhq.models import TaskResult
from labhq.orchestrator.cso import Orchestrator
from labhq.result_storage import ResultStorage
from labhq.settings import DataZone, Settings


class Hub:
    def __init__(self, tmp_path):
        self.s = Settings()
        self.s.runner.workspace_root = str(tmp_path / "runs")
        self.requests = {
            "req_progress": {
                "id": "req_progress",
                "text": "Compare public studies",
                "status": "running",
                "intake": {"work_kind": "research"},
                "plan": {"steps": [
                    {"id": "s1", "instruction": "prepare", "depends_on": []},
                    {"id": "s2", "instruction": "analyze", "depends_on": ["s1"]},
                    {"id": "s3", "instruction": "summarize", "depends_on": ["s2"]},
                ]},
                "results": {},
                "result_storage": {
                    "mode": "existing", "repo": "owner/study", "visibility": "private",
                    "status": "selected", "progress_push": True,
                    "progress_branch": "labhq/progress",
                },
            }
        }
        self.events = []

    def save_request(self, _rid):
        pass

    async def publish(self, event):
        self.events.append(event)


def github_transport(calls, *, deny=False, fail_first_put=False):
    files = {}
    progress_exists = False
    put_failed = False

    def handle(request):
        nonlocal progress_exists, put_failed
        body = json.loads(request.content) if request.content else {}
        calls.append((request.method, request.url.path, body))
        if deny and request.url.path == "/repos/owner/study":
            return httpx.Response(403, json={"message": "forbidden"})
        if request.url.path == "/user":
            return httpx.Response(200, json={"login": "owner"})
        if request.url.path == "/repos/owner/study":
            return httpx.Response(200, json={"private": True, "default_branch": "main",
                                             "permissions": {"push": True}})
        if request.url.path.endswith("/git/ref/heads/labhq/progress"):
            return httpx.Response(200 if progress_exists else 404,
                                  json={"object": {"sha": "progress-sha"}} if progress_exists else {})
        if request.url.path.endswith("/git/ref/heads/main"):
            return httpx.Response(200, json={"object": {"sha": "main-sha"}})
        if request.url.path.endswith("/git/refs"):
            progress_exists = True
            return httpx.Response(201, json={"ref": "refs/heads/labhq/progress"})
        marker = "/contents/"
        if marker in request.url.path:
            name = request.url.path.split(marker, 1)[1]
            if request.method == "GET":
                if name not in files:
                    return httpx.Response(404, json={})
                return httpx.Response(200, json={
                    "sha": files[name][0], "encoding": "base64", "content": files[name][1],
                    "html_url": f"https://github.com/owner/study/blob/labhq/progress/{name}",
                })
            if fail_first_put and not put_failed:
                put_failed = True
                return httpx.Response(500, json={"message": "temporary failure"})
            encoded = body["content"]
            files[name] = (f"sha-{len(files) + 1}", encoded)
            return httpx.Response(201, json={"content": {"html_url": "https://example.invalid/file"}})
        return httpx.Response(404, json={})

    return httpx.MockTransport(handle)


def write_bundle(hub, rows):
    bundle = Path(hub.s.runner.workspace_root) / "requests" / "req_progress"
    manifest = ["relative_path\tstatus"]
    for name, body in rows.items():
        path = bundle / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body if isinstance(body, bytes) else body.encode("utf-8"))
        manifest.append(f"{name}\tcopied")
    (bundle / "MANIFEST.tsv").write_text("\n".join(manifest) + "\n", encoding="utf-8")


async def test_initial_storage_card_offers_progress_opt_in_off_by_default(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token-for-tests")
    hub = Hub(tmp_path)
    hub.requests["req_progress"].pop("result_storage")
    service = ResultStorage(hub, github_transport([]))

    card = await service.card("req_progress")
    assert card["progress_question"] == "진행 과정도 단계마다 올릴까요?"
    assert card["progress_default"] is False and card["progress_available"] is True
    await service.select("req_progress", {
        "mode": "existing", "repo": "owner/study", "visibility": "private", "progress_push": True,
    })
    saved = hub.requests["req_progress"]["result_storage"]
    assert saved["progress_push"] is True and saved["progress_branch"] == "labhq/progress"
    await service.close()


async def test_enabled_request_puts_each_completed_step_on_progress_branch(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token-for-tests")
    hub = Hub(tmp_path)
    write_bundle(hub, {
        "steps/s1/outputs/prepare.py": "print('ready')\n",
        "steps/s2/outputs/analysis.md": "# Analysis\n\nPublic result.\n",
        "steps/s3/outputs/summary.svg": b"<svg xmlns='http://www.w3.org/2000/svg'></svg>",
    })
    calls = []
    service = ResultStorage(hub, github_transport(calls))

    for sid in ("s1", "s2", "s3"):
        result = TaskResult(task_id=f"task-{sid}", agent_id="worker", ok=True,
                            text=f"{sid} done", outputs=[f"outputs/{sid}.md"])
        hub.requests["req_progress"]["results"][sid] = result.model_dump(mode="json")
        await service.progress_step("req_progress", sid, result)

    puts = [(path, body) for method, path, body in calls if method == "PUT"]
    readme_puts = [body for path, body in puts if path.endswith("/contents/progress/README.md")]
    assert len(readme_puts) == 3
    assert all(body["branch"] == "labhq/progress" for _path, body in puts)
    uploaded = {path for path, _body in puts}
    assert any(path.endswith("progress/steps/s1/prepare.py") for path in uploaded)
    assert any(path.endswith("progress/steps/s2/analysis.md") for path in uploaded)
    assert any(path.endswith("progress/steps/s3/summary.svg") for path in uploaded)
    final_readme = base64.b64decode(readme_puts[-1]["content"]).decode("utf-8")
    assert all(f"| {sid} | 완료 |" in final_readme for sid in ("s1", "s2", "s3"))
    await service.close()


async def test_internal_and_publish_check_failures_are_omitted(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token-for-tests")
    hub = Hub(tmp_path)
    internal = tmp_path / "internal"
    internal.mkdir()
    private_input = internal / "cohort.tsv"
    hub.s.policy.data_zones = [DataZone(path=str(internal), level="internal")]
    req = hub.requests["req_progress"]
    req["text"] = f"Use {private_input} and public GEO data."
    req["plan"]["steps"] = [
        {"id": "private", "instruction": f"Read {private_input}", "depends_on": []},
        {"id": "public", "instruction": "Summarize public GEO data", "depends_on": []},
    ]
    write_bundle(hub, {
        "steps/private/outputs/private.py": "print('internal')\n",
        "steps/public/outputs/unsafe.md": "password=do-not-publish\n",
        "steps/public/outputs/safe.svg": b"<svg xmlns='http://www.w3.org/2000/svg'></svg>",
    })
    for sid in ("private", "public"):
        req["results"][sid] = TaskResult(task_id=sid, agent_id="worker", ok=True,
                                          text="done").model_dump(mode="json")
    calls = []
    service = ResultStorage(hub, github_transport(calls))

    await service.progress_step("req_progress", "private")
    await service.progress_step("req_progress", "public")

    puts = [(path, body) for method, path, body in calls if method == "PUT"]
    names = {path for path, _body in puts}
    assert not any(path.endswith("private.py") or path.endswith("unsafe.md") for path in names)
    assert any(path.endswith("safe.svg") for path in names)
    readme = base64.b64decode([body for path, body in puts if path.endswith("progress/README.md")][-1]["content"]).decode()
    assert "| private | 내부 단계 · 올리지 않음 |" in readme
    assert "검사로 뺌" in readme and "unsafe.md" in readme
    await service.close()


async def test_custom_workspace_is_omitted_before_fake_progress_upload(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token-for-tests")
    hub = Hub(tmp_path)
    monkeypatch.chdir(tmp_path)
    hub.s.runner.workspace_root = "arbitrary-folder"
    private_literal = "arbitrary-folder/task/input.tsv"
    req = hub.requests["req_progress"]
    write_bundle(hub, {"steps/s1/outputs/unsafe.md": private_literal})
    req["results"]["s1"] = TaskResult(task_id="s1", agent_id="worker", ok=True,
                                       text=private_literal).model_dump(mode="json")
    calls = []
    service = ResultStorage(hub, github_transport(calls))
    await service.progress_step("req_progress", "s1")
    puts = [(path, body) for method, path, body in calls if method == "PUT"]
    assert puts and not any(path.endswith("unsafe.md") for path, _ in puts)
    bodies = [base64.b64decode(body["content"]).decode() for _path, body in puts]
    assert all(private_literal not in body for body in bodies)
    assert any("검사로 뺌" in body for body in bodies)
    await service.close()


async def test_progress_failure_warns_without_raising_and_permission_denial_disables(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token-for-tests")
    hub = Hub(tmp_path)
    calls = []
    service = ResultStorage(hub, github_transport(calls, deny=True))

    await service.progress_step("req_progress", "s1")
    saved = hub.requests["req_progress"]["result_storage"]
    assert saved["progress_push"] is False
    assert saved["progress_status"] == "disabled"
    assert "권한" in saved["progress_warning"]
    first_count = len(calls)
    await service.progress_step("req_progress", "s2")
    assert len(calls) == first_count
    assert len([event for event in hub.events if event["type"] == "request.progress_push"]) == 1
    await service.close()


async def test_transient_progress_failure_retries_completed_outputs_on_next_step(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token-for-tests")
    hub = Hub(tmp_path)
    write_bundle(hub, {
        "steps/s1/outputs/first.md": "first\n",
        "steps/s2/outputs/second.md": "second\n",
    })
    calls = []
    service = ResultStorage(hub, github_transport(calls, fail_first_put=True))
    for sid in ("s1", "s2"):
        result = TaskResult(task_id=sid, agent_id="worker", ok=True, text="done")
        hub.requests["req_progress"]["results"][sid] = result.model_dump(mode="json")
        await service.progress_step("req_progress", sid, result)

    saved = hub.requests["req_progress"]["result_storage"]
    assert saved["progress_push"] is True and saved["progress_status"] == "pushed"
    put_paths = [path for method, path, _body in calls if method == "PUT"]
    assert sum(path.endswith("progress/steps/s1/first.md") for path in put_paths) == 2
    assert any(path.endswith("progress/steps/s2/second.md") for path in put_paths)
    warnings = [event for event in hub.events if not event["data"]["ok"]]
    assert len(warnings) == 1 and warnings[0]["data"]["disabled"] is False
    await service.close()


async def test_progress_is_off_by_default_and_controlled_requests_cannot_enable_it(tmp_path):
    hub = Hub(tmp_path)
    saved = hub.requests["req_progress"]["result_storage"]
    saved.pop("progress_push")
    service = ResultStorage(hub)
    await service.progress_step("req_progress", "s1")
    assert service._client is None

    controlled = tmp_path / "controlled"
    controlled.mkdir()
    hub.s.policy.data_zones = [DataZone(path=str(controlled), level="restricted")]
    hub.requests["req_progress"]["text"] = f"Analyze {controlled / 'cohort.tsv'}"
    await service.select("req_progress", {
        "mode": "existing", "repo": "owner/study", "progress_push": True,
    })
    assert hub.requests["req_progress"]["result_storage"]["progress_push"] is False
    assert "통제 데이터" in hub.requests["req_progress"]["result_storage"]["progress_warning"]


async def test_run_dag_keeps_step_success_when_progress_callback_fails():
    class CsoHub:
        def __init__(self):
            self.s = Settings()
            self.s.orchestrator.step_retry_backoff_s = 0
            self.requests = {"r": {"id": "r", "text": "question", "results": {},
                                    "result_storage": {"progress_push": True}}}
            self.agents = {"worker": {"id": "worker", "engine": "mock"}}
            self.events = []
            self.result_storage = SimpleNamespace(progress_step=AsyncMock(side_effect=RuntimeError("offline")))

        async def dispatch(self, task):
            return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, text="done")

        async def publish(self, event):
            self.events.append(event)

        def save_request(self, _rid):
            pass

        def supports_resume(self, _agent_id):
            return False

    hub = CsoHub()
    outcomes = {}
    await Orchestrator(hub).run_dag(
        "r", "question", [{"id": "A", "agent_id": "worker", "instruction": "work",
                            "depends_on": []}], outcomes)
    assert outcomes["A"].ok
    assert hub.requests["r"]["result_storage"]["progress_warning"]
    assert any(event["type"] == "request.step_done" for event in hub.events)
