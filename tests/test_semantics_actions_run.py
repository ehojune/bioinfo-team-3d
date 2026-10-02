"""Action layer A2 (#149 결정 16): the PI's CLI runs request.followup, nothing else, through the existing REST path.

The three preconditions both independent reviews required, each with a test that fails without its fix:
(a) the PI's client token never reaches staff, and no staff surface can start the action;
(b) no automatic resend: the execution id is on disk before the POST, a lost answer is unknown, and a new run is
    refused until the PI closes it, across a restart;
(c) a precondition the live records cannot show is unknown, and unknown is never offered.
Also: off changes nothing, and HPC is always refused.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from labhq.research import semantics_actions as acts
from labhq.research import semantics_actions_run as a2
from labhq.settings import Settings

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "zz-pi-client-token-7c41"
CONFIRM = {"mode": "shadow", "actions": "confirm"}
CANARY = "zz-a2-canary 왜 이 세포유형인가"


def _settings(tmp_path, semantics=CONFIRM):
    s = Settings()
    s.gateway.state_dir = str(tmp_path / "state")
    s.gateway.client_token = TOKEN
    s.semantics = semantics
    return s


def _ledger_root(tmp_path) -> Path:
    return tmp_path / "state" / "semantics" / "actions"


def _ledger_text(tmp_path) -> str:
    root = _ledger_root(tmp_path)
    return "".join(p.read_text(encoding="utf-8") for p in sorted(root.rglob("*")) if p.is_file()) if root.exists() else ""


OK_REQ = {"id": "req_f1", "status": "done", "mode": "orchestrate", "followups": []}
OK_AGENTS = [{"id": "cso", "engine": "claude_code"}]


class Fake:
    """A gateway for the CLI: a GET answers its row (None: 404, an exception: raised); POST answers or raises."""

    def __init__(self, req=OK_REQ, agents=OK_AGENTS, post=(200, {"request_id": "req_f1", "followup_id": "fu_abc123"})):
        self.req, self.agents, self.post = req, agents, post
        self.calls: list[tuple[str, str]] = []
        self.on_post = None

    def __call__(self, method, path, body=None):
        self.calls.append((method, path))
        if method == "GET":
            value = self.req if path.startswith("/api/requests/") else self.agents
            if isinstance(value, Exception):
                raise value
            return (200, value) if value is not None else (404, None)
        if self.on_post:
            self.on_post()
        if isinstance(self.post, BaseException):
            raise self.post
        return self.post

    @property
    def posts(self):
        return [c for c in self.calls if c[0] == "POST"]


def _run(s, http, *, action="request.followup", rid="req_f1", text=CANARY, answer="y", env=None, tty=True,
         reload=None):
    said, asked = [], []

    def ask(prompt):
        asked.append(prompt)
        return answer

    code = a2.run(s, action, rid, text, env=env or {}, tty=tty, ask=ask, out=said.append, http=http, reload=reload)
    return code, said, asked


def _records(tmp_path):
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted((_ledger_root(tmp_path) / "runs").glob("*.json"))]


# ---------------------------------------------------------------- (a) the token never reaches staff

def _runner_with_config(tmp_path, monkeypatch):
    from labhq.models import AgentSpec, Engine, TaskResult
    from labhq.runner.daemon import Runner

    config = tmp_path / "config" / "labhq.yaml"
    config.parent.mkdir()
    config.write_text(yaml.safe_dump({
        "gateway": {"client_token": TOKEN, "runner_token": "zz-pi-runner-token-2b9e",
                    "state_dir": str(tmp_path / "gw")},
        "runner": {"state_dir": "rstate", "workspace_root": "runs", "agents_dir": "agents", "talent_dir": "talent"},
        "policy": {"approvals": {"timeout_s": 77}},
        "semantics": CONFIRM}), encoding="utf-8")
    settings = Settings.load(str(config))
    runner = Runner(settings)
    agent = AgentSpec(id="worker", name="Worker", role="test", engine=Engine.claude_code, builtin_mcp=["approval"])
    monkeypatch.setattr(runner, "_resolve_agent", lambda _task: agent)
    seen = {}

    class Adapter:
        async def run(self, ctx):
            seen["ctx"] = ctx
            return TaskResult(task_id=ctx.task.id, agent_id=agent.id, ok=True, text="done")

    monkeypatch.setattr("labhq.runner.daemon.get_adapter", lambda *_args: Adapter())
    return settings, runner, seen


async def test_staff_get_a_config_copy_without_the_gateway_tokens(tmp_path, monkeypatch):
    """Before: LABHQ_CONFIG named the PI's own file, client token included, readable by every staff process."""
    from labhq.models import Task

    settings, runner, seen = _runner_with_config(tmp_path, monkeypatch)
    result = await runner.run_task(Task(id="task-a2", request_id="req_x", agent_id="worker", prompt="p",
                                        meta={"kind": "direct"}))
    assert result.ok
    ctx = seen["ctx"]
    surfaces = [*ctx.env.values(), *(json.dumps(m.model_dump()) for m in ctx.mcp_servers)]
    assert ctx.mcp_servers and all(TOKEN not in v and "zz-pi-runner" not in v for v in surfaces)
    copy = Path(ctx.env["LABHQ_CONFIG"])
    assert copy.resolve() != Path(settings.config_path) and TOKEN not in copy.read_text(encoding="utf-8")
    staff = Settings.load(str(copy))  # what the MCP tools load
    assert staff.gateway.client_token == "" and staff.gateway.runner_token == ""
    assert staff.policy == settings.policy and staff.path("runs") == settings.path("runs")
    with TestClient(__import__("labhq.gateway.server", fromlist=["create_app"]).create_app(settings)) as client:
        assert client.get("/api/agents", headers={"Authorization": "Bearer "}).status_code == 401
        assert client.get("/api/agents", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 200


async def test_a_task_is_refused_rather_than_handed_the_original_config(tmp_path, monkeypatch):
    from labhq.models import Task
    from labhq.runner import daemon

    _, runner, seen = _runner_with_config(tmp_path, monkeypatch)
    monkeypatch.setattr(daemon, "write_staff_config", lambda *a: (_ for _ in ()).throw(OSError("disk full")))
    result = await runner.run_task(Task(id="task-a2b", request_id="req_x", agent_id="worker", prompt="p",
                                        meta={"kind": "direct"}))
    assert not result.ok and "OSError" in result.error and "ctx" not in seen


@pytest.mark.parametrize("marker", a2.STAFF_ENV)
def test_a_staff_task_environment_cannot_run_the_action(tmp_path, marker):
    http = Fake()
    code, said, asked = _run(_settings(tmp_path), http, env={marker: "x"})
    assert code == 1 and said[0].startswith("refused_env") and http.calls == [] and asked == []
    assert not _ledger_root(tmp_path).exists()


def test_the_staff_config_copy_cannot_run_the_action_even_with_a_scrubbed_environment(tmp_path):
    """A staff shell that drops the runner's variables and fakes a terminal still holds only the blank copy."""
    from labhq.settings import write_staff_config

    config = tmp_path / "labhq.yaml"
    config.write_text(yaml.safe_dump({"gateway": {"client_token": TOKEN, "state_dir": str(tmp_path / "state")},
                                      "semantics": CONFIRM}), encoding="utf-8")
    staff = Settings.load(write_staff_config(Settings.load(str(config)), tmp_path / "staff"))
    for s in (staff, staff.model_copy(update={"config_base": None})):  # blank token alone is enough
        http = Fake()
        code, said, _ = _run(s, http)
        assert code == 1 and said[0].split(":")[0] in ("refused_env", "refused_config") and http.calls == []


STAFF_SIDE = ("labhq/runner", "labhq/tools", "labhq/adapters")


def test_only_the_pi_cli_reaches_the_action_runner():
    """No staff-side module, MCP tool, gateway or orchestrator imports the runner of A2, and staff-side code names no
    REST path and no client token. The CLI imports it inside its own command only."""
    importers = []
    for path in sorted((ROOT / "labhq").rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        names |= {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        if any("semantics_actions_run" in name for name in names):
            importers.append(rel)
        if rel.startswith(STAFF_SIDE):
            text = path.read_text(encoding="utf-8")
            assert "/api/" not in text and "client_token" not in text and "semantics_actions" not in text, rel
    assert importers == ["labhq/cli.py"]
    tools = [n.name for p in (ROOT / "labhq" / "tools").glob("*_mcp.py")
             for n in ast.walk(ast.parse(p.read_text(encoding="utf-8"))) if isinstance(n, ast.AsyncFunctionDef)]
    assert tools and not [t for t in tools if "followup" in t or "action" in t]


FORBIDDEN = ("labhq.gateway", "labhq.orchestrator", "labhq.runner", "labhq.store", "labhq.tools", "..gateway",
             "..orchestrator", "..runner", "..store", "..tools", "subprocess", "socket", "sqlite3")


def test_the_action_runner_changes_nothing_directly():
    tree = ast.parse((ROOT / "labhq" / "research" / "semantics_actions_run.py").read_text(encoding="utf-8"))
    names = [("." * n.level) + (n.module or "") for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
    names += [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
    assert not [n for n in names if n.startswith(FORBIDDEN)]


# ---------------------------------------------------------------- the one path: existing REST, PI y/N

def _gateway(tmp_path):
    from labhq.gateway.server import create_app
    s = _settings(tmp_path)
    app = create_app(s)
    hub = app.state.hub
    hub.agents = {"cso": {"id": "cso", "engine": "claude_code"}}
    hub.requests["req_f1"] = {"id": "req_f1", "text": "t", "mode": "orchestrate", "status": "done",
                              "finished_at": 1.0, "followups": []}
    client = TestClient(app)

    def http(method, path, body=None):
        response = client.request(method, path, json=body, headers={"Authorization": f"Bearer {TOKEN}"})
        return response.status_code, response.json()
    return s, hub, client, http


def test_a_confirmed_followup_goes_through_the_existing_gateway_path(tmp_path):
    import time
    s, hub, client, http = _gateway(tmp_path)
    with client:
        code, said, asked = _run(s, http)
        assert code == 0 and said[0].startswith("accepted")
        (entry,) = hub.requests["req_f1"]["followups"]
        end = time.time() + 10
        while entry["status"] == "running":
            assert time.time() < end
            time.sleep(0.02)
        (rec,) = _records(tmp_path)
        assert (rec["decision"], rec["outcome"], rec["http"], rec["completion"]) == ("confirmed", "accepted", "2xx", None)
        assert rec["followup_id"] == entry["id"] and entry["text"] == CANARY
        out = []
        assert a2.check(s, rec["exec_id"], env={}, tty=True, ask=lambda p: "n", out=out.append, http=http) == 0
    (rec,) = _records(tmp_path)
    assert rec["outcome"] == "accepted" and rec["completion"] == "failed"  # accepted is not completed
    assert "CSO" in asked[0] or "cso" in asked[0]
    assert "zz-a2-canary" in asked[0] and "zz-a2-canary" not in "".join(said) + "".join(out)
    assert "zz-a2-canary" not in _ledger_text(tmp_path) and TOKEN not in _ledger_text(tmp_path)
    assert set(rec) == {"v", "exec_id", "action", "request_id", "ts", "decision", "conditions", "text_chars",
                        "outcome", "http", "status_code", "error_kind", "followup_id", "sent_at", "ms", "completion",
                        "checked_at"}


def test_the_pi_declining_sends_nothing(tmp_path):
    http = Fake()
    code, said, _ = _run(_settings(tmp_path), http, answer="")
    assert code == 1 and said == ["declined: 보내지 않았습니다."] and http.posts == []
    assert [r["decision"] for r in _records(tmp_path)] == ["declined"]


def test_turning_actions_off_while_the_pi_reads_sends_nothing(tmp_path):
    http = Fake()
    off = _settings(tmp_path, {"mode": "shadow", "actions": "shadow"})
    code, said, _ = _run(_settings(tmp_path), http, reload=lambda: off)
    assert code == 1 and said[0].startswith("refused_config") and http.posts == []
    code, said, _ = _run(_settings(tmp_path), http, reload=lambda: 1 / 0)
    assert code == 1 and http.posts == []


# ---------------------------------------------------------------- (b) no automatic resend, restart-safe

def test_the_execution_id_is_on_disk_before_the_post(tmp_path):
    http = Fake()
    seen = []
    http.on_post = lambda: seen.append([(r["outcome"], r["exec_id"]) for r in _records(tmp_path)])
    code, _, _ = _run(_settings(tmp_path), http)
    assert code == 0 and len(seen) == 1 and seen[0][0][0] == "intent"
    assert seen[0][0][1] == _records(tmp_path)[0]["exec_id"]


@pytest.mark.parametrize("post", [TimeoutError("read timed out"), ConnectionResetError(), (500, None),
                                  (200, {"request_id": "req_f1"}), (200, None)],
                         ids=["timeout", "reset", "5xx", "no-id", "no-body"])
def test_a_lost_or_unclear_answer_is_unknown_and_never_resent(tmp_path, post):
    s = _settings(tmp_path)
    http = Fake(post=post)
    code, said, _ = _run(s, http)
    assert code == 1 and said[0].startswith("unknown") and len(http.posts) == 1
    (rec,) = _records(tmp_path)
    assert rec["outcome"] == "unknown" and rec["followup_id"] is None
    http.post = (200, {"followup_id": "fu_second"})
    code, said, asked = _run(s, http, text="다른 질문")
    assert code == 1 and said[0].startswith("refused_model") and "previous_settled 모름" in said[0]
    assert len(http.posts) == 1 and asked == []


class Crash(BaseException):
    """The CLI process dies mid-send (power, kill): nothing after the intent is written."""


def test_a_crash_after_the_intent_reads_back_as_unknown_after_a_restart(tmp_path):
    s = _settings(tmp_path)
    http = Fake(post=Crash())
    with pytest.raises(Crash):
        _run(s, http)
    (rec,) = _records(tmp_path)
    assert rec["outcome"] == "intent"
    http.post = (200, {"followup_id": "fu_after"})
    restarted = _settings(tmp_path)  # a new process: only the files remain
    code, said, _ = _run(restarted, http)
    assert code == 1 and said[0].startswith("refused_model") and len(http.posts) == 1
    listed = []
    a2.check(restarted, None, env={}, tty=True, ask=input, out=listed.append)
    assert any(rec["exec_id"] in line and "intent" in line for line in listed)
    out = []
    assert a2.check(restarted, rec["exec_id"], env={}, tty=True, ask=lambda p: "n", out=out.append) == 1
    assert _run(restarted, http)[0] == 1 and len(http.posts) == 1  # still refused: the PI said no
    assert a2.check(restarted, rec["exec_id"], env={}, tty=True, ask=lambda p: "y", out=out.append) == 0
    closed = {r["exec_id"]: r for r in _records(tmp_path)}[rec["exec_id"]]
    assert out[-1].endswith("closed_by_pi") and closed["outcome"] == "closed_by_pi"
    code, said, _ = _run(restarted, http)  # a new PI decision, never an automatic resend
    assert code == 0 and len(http.posts) == 2


def test_closing_an_unknown_run_needs_the_pi_at_a_terminal(tmp_path):
    s = _settings(tmp_path)
    _run(s, Fake(post=TimeoutError()))
    (rec,) = _records(tmp_path)
    for env, tty in (({"LABHQ_TASK_ID": "t"}, True), ({}, False)):
        out = []
        assert a2.check(s, rec["exec_id"], env=env, tty=tty, ask=lambda p: "y", out=out.append) == 1
    assert _records(tmp_path)[0]["outcome"] == "unknown"


def test_a_server_refusal_is_recorded_and_not_retried(tmp_path):
    s = _settings(tmp_path)
    http = Fake(post=(409, {"detail": "a follow-up for this request is still running"}))
    code, said, _ = _run(s, http)
    assert code == 1 and said[0].startswith("refused") and len(http.posts) == 1
    (rec,) = _records(tmp_path)
    assert (rec["outcome"], rec["http"], rec["status_code"]) == ("refused", "4xx", 409)
    assert "still running" not in _ledger_text(tmp_path)
    assert not list((_ledger_root(tmp_path) / "open").iterdir())  # nothing was created: a new PI run may go


def test_a_second_process_cannot_take_the_same_request(tmp_path):
    s = _settings(tmp_path)
    http = Fake()
    other = a2.Ledger(s)
    original = a2.Ledger.acquire

    def race(self, action, rid, exec_id):  # another CLI took the lock between the check and the take
        original(other, action, rid, "0" * 16)
        return original(self, action, rid, exec_id)

    import unittest.mock
    with unittest.mock.patch.object(a2.Ledger, "acquire", race):
        code, said, _ = _run(s, http)
    assert code == 1 and said[0].startswith("refused_ledger") and http.posts == []


# ---------------------------------------------------------------- (c) unknown is never allowed

@pytest.mark.parametrize("req, agents", [
    (None, OK_AGENTS),                                                                # GET request: 404
    (OSError("down"), OK_AGENTS),                                                     # GET request: lost
    ({"id": "req_f1", "mode": "orchestrate", "followups": []}, OK_AGENTS),            # no status
    ({**OK_REQ, "followups": "?"}, OK_AGENTS),                                        # followups unreadable
    ({**OK_REQ, "followups": [3]}, OK_AGENTS),
    ({"id": "req_f1", "status": "done", "followups": []}, OK_AGENTS),                 # mode unknown
    (OK_REQ, None),                                                                   # GET agents: 404
    (OK_REQ, OSError("down")),                                                        # GET agents: lost
    (OK_REQ, [{"id": "cso"}]),                                                        # engine unknown
], ids=["request-404", "request-lost", "status", "followups", "followup-row", "mode", "agents-404", "agents-lost",
        "engine"])
def test_a_precondition_the_records_cannot_show_is_unknown_and_refused(tmp_path, req, agents):
    http = Fake(req=req, agents=agents)
    code, said, asked = _run(_settings(tmp_path), http)
    assert code == 1 and said[0].startswith("refused_model") and "모름" in said[0]
    assert http.posts == [] and asked == []
    (rec,) = _records(tmp_path)
    assert rec["decision"] == "refused_model" and None in rec["conditions"].values()
    assert acts._all(rec["conditions"].values()) is None


def test_a_blocked_precondition_is_refused_too(tmp_path):
    http = Fake(req={"id": "req_f1", "status": "running", "mode": "orchestrate", "followups": []})
    code, said, _ = _run(_settings(tmp_path), http)
    assert code == 1 and "request_terminal 거짓" in said[0] and http.posts == []


# ---------------------------------------------------------------- off changes nothing

@pytest.mark.parametrize("semantics", [None, "off", "shadow", {"mode": "shadow"}, {"mode": "shadow", "actions": "off"},
                                       {"mode": "shadow", "actions": "shadow"}, {"mode": "shadow", "actions": "Confirm"},
                                       {"mode": "off", "actions": "confirm"}])
def test_off_reads_nothing_writes_nothing_and_sends_nothing(tmp_path, semantics):
    http = Fake()
    code, said, asked = _run(_settings(tmp_path, semantics), http)
    assert code == 1 and said[0].startswith("refused_config") and http.calls == [] and asked == []
    assert not (tmp_path / "state").exists()


def test_a_latched_off_shadow_turns_the_action_off(tmp_path):
    from labhq.research import semantics_shadow as shadow
    s = _settings(tmp_path)
    shadow.write_disabled(shadow.ShadowPaths(shadow.shadow_root(s)), "info_boundary", 1)
    http = Fake()
    code, said, _ = _run(s, http)
    assert code == 1 and said[0].startswith("refused_config") and http.calls == []


@pytest.mark.parametrize("url", ["ws://10.0.0.5:8787", "wss://lab.example.net"])
def test_a_remote_gateway_or_a_default_token_is_refused(tmp_path, url):
    s = _settings(tmp_path)
    s.gateway.url = url
    default = _settings(tmp_path)
    default.gateway.client_token = "change-me-client"
    for settings in (s, default):
        http = Fake()
        assert _run(settings, http)[0] == 1 and http.calls == []


# ---------------------------------------------------------------- HPC and every other action

@pytest.mark.parametrize("action", ["hpc.submit", "hpc.cancel", "hpc.anything", "approval.decide", "ask.answer",
                                    "task.cancel", "recruit.start", "contract.update", "request.cancel", ""])
def test_hpc_is_always_refused_and_nothing_else_runs(tmp_path, action):
    http = Fake()
    code, said, asked = _run(_settings(tmp_path), http, action=action)
    expected = "refused_p3" if action.startswith("hpc.") else "refused_action"
    assert code == 1 and said[0].startswith(expected) and http.calls == [] and asked == []
    assert a2.gate(action, _settings(tmp_path, None), {}, False) == expected  # HPC first, whatever else holds
    assert a2.CLI_EXECUTABLE == frozenset({"request.followup"}) and acts.EXECUTABLE == frozenset()
