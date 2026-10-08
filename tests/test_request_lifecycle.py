"""Request and approval lifecycle for a PI working alone (PI 점검 R2 R5 R13 R15)."""
import asyncio
import time

import pytest

from labhq.gateway.server import PI_DECISION_KINDS, Hub
from labhq.models import ApprovalRequest, Task, TaskResult
from labhq.orchestrator.cso import BudgetExceeded, Orchestrator
from labhq.settings import Settings


def _settings(tmp_path):
    s = Settings()
    s.gateway.state_dir = str(tmp_path / "state")
    return s


def _request(hub, rid, status="running", **extra):
    hub.requests[rid] = {"id": rid, "text": f"{rid} 요청 본문", "mode": "orchestrate", "status": status,
                         "created_at": time.time(), **extra}
    hub.save_request(rid)
    return hub.requests[rid]


def result(task, ok=True, **kwargs):
    return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=ok, **kwargs)


class FakeHub:
    """The orchestrator's view of the gateway, with the PI's answer scripted."""

    def __init__(self, dispatch, decision):
        self.s = Settings()
        self.s.orchestrator.chief_of_staff_agent = None
        self.s.orchestrator.reviewer_agent = None
        self.s.orchestrator.step_retry_backoff_s = 0
        self.requests = {"r": {"text": "question", "mode": "team", "budget_usd": 10}}
        self.agents = {name: {"id": name, "name": name, "role": "test", "engine": "mock"}
                       for name in ("cso", "worker")}
        self.events, self.calls, self.approvals = [], [], []
        self.reply, self.decision = dispatch, decision

    async def dispatch(self, task):
        self.calls.append(task)
        return await self.reply(task)

    async def publish(self, event):
        self.events.append(event)

    async def request_approval(self, **kwargs):
        self.approvals.append(kwargs)
        return dict(self.decision)

    def supports_resume(self, agent_id):
        return False

    def result_map(self, rid):
        return {}

    def save_request(self, rid):
        pass


TIMED_OUT = {"approved": False, "note": "timed out", "state": "timed_out"}
DENIED = {"approved": False, "note": ""}


# ----- R5: an unanswered PI decision parks the request; it never ends as a deny -----

@pytest.mark.asyncio
async def test_pi_decision_cards_outlive_the_gate_timeout_and_park_the_request(tmp_path):
    s = _settings(tmp_path)
    s.policy.approvals.timeout_s = 1  # the gate timeout; it used to end every card below after one second
    hub = Hub(s)
    kinds = sorted(PI_DECISION_KINDS - {"resume"})  # a resume card has no waiter; tested below
    for kind in kinds:
        _request(hub, f"r_{kind}")
    waits = {kind: asyncio.create_task(hub.request_approval(kind, f"{kind}?", f"r_{kind}")) for kind in kinds}
    await asyncio.sleep(1.4)
    assert not [kind for kind, wait in waits.items() if wait.done()]
    assert {kind: hub.requests[f"r_{kind}"]["status"] for kind in kinds} == {kind: "waiting_pi" for kind in kinds}
    cards = {entry["approval"]["request_id"]: aid for aid, entry in hub.approvals.items()}
    for kind in kinds:
        assert hub.approvals[cards[f"r_{kind}"]]["approval"]["timeout_s"] == s.policy.approvals.pi_decision_timeout_s
        await hub.resolve_approval(cards[f"r_{kind}"], True, "진행")
    decisions = {kind: await asyncio.wait_for(wait, 1) for kind, wait in waits.items()}
    assert all(decision["approved"] and "state" not in decision for decision in decisions.values())
    assert {hub.requests[f"r_{kind}"]["status"] for kind in kinds} == {"running"}
    assert not any(hub.requests[f"r_{kind}"].get("pi_waits") for kind in kinds)
    statuses = [e["data"]["status"] for e in hub.events if e["type"] == "request.status"]
    assert statuses.count("waiting_pi") == len(kinds) and statuses.count("running") == len(kinds)


@pytest.mark.asyncio
async def test_a_gate_card_keeps_the_gate_timeout_and_does_not_park(tmp_path):
    s = _settings(tmp_path)
    s.policy.approvals.timeout_s = 1
    hub = Hub(s)
    _request(hub, "r")
    decision = await hub.request_approval("download", "big file?", "r")
    assert decision["state"] == "timed_out" and hub.requests["r"]["status"] == "running"
    assert not [e for e in hub.events if e["type"] == "request.status"]


@pytest.mark.asyncio
async def test_the_pi_decision_bound_returns_timed_out_and_lifts_the_hold(tmp_path):
    s = _settings(tmp_path)
    s.policy.approvals.pi_decision_timeout_s = 1
    hub = Hub(s)
    _request(hub, "r")
    decision = await hub.request_approval("clarify", "which cohort?", "r")
    assert decision["state"] == "timed_out" and not decision["approved"]
    assert hub.requests["r"]["status"] == "running" and "pi_waits" not in hub.requests["r"]
    stored = hub.store.get("approval_decision", decision["approval_id"])
    assert stored["state"] == "timed_out"


@pytest.mark.asyncio
@pytest.mark.parametrize(("decision", "outcome", "report"), [
    (TIMED_OUT, "clarify_timed_out", "PI 질문 카드에 기한 안에 답이 없어"),
    (DENIED, None, "PI 확인 답변을 받지 못해 요청을 멈췄습니다."),
])
async def test_an_unanswered_clarify_card_is_not_a_denied_one(decision, outcome, report):
    async def dispatch(task):
        return result(task, structured={"steps": [{"id": "A", "agent_id": "worker", "instruction": "A",
                                                   "depends_on": []}],
                                        "clarifying_questions": ["Which cohort?"]})

    hub = FakeHub(dispatch, decision)
    await Orchestrator(hub).run_request("r")
    req = hub.requests["r"]
    assert req["status"] == "failed" and req.get("outcome") == outcome
    assert report in req["report"]
    assert [t.meta["kind"] for t in hub.calls] == ["plan"]


@pytest.mark.asyncio
@pytest.mark.parametrize(("decision", "timed_out"), [(TIMED_OUT, True), (DENIED, False)])
async def test_an_unanswered_budget_card_is_recorded_as_timed_out(decision, timed_out):
    async def dispatch(task):
        return result(task, text="done", cost_usd=0.8)

    hub = FakeHub(dispatch, decision)
    hub.requests["r"]["budget_usd"] = 0.5
    orch = Orchestrator(hub)
    assert (await orch.run_step(Task(agent_id="worker", request_id="r", prompt="work"))).ok
    with pytest.raises(BudgetExceeded) as stopped:
        await orch.run_step(Task(agent_id="worker", request_id="r", prompt="next"))
    assert bool(orch.budget_outcomes["r"][-1].get("timed_out")) is timed_out
    assert ("timed out" in str(stopped.value)) is timed_out and ("denied" in str(stopped.value)) is not timed_out
    assert hub.requests["r"].get("outcome") == ("budget_timed_out" if timed_out else None)


@pytest.mark.asyncio
async def test_a_resume_card_past_the_bound_ends_its_request_as_resume_timed_out(tmp_path):
    s = _settings(tmp_path)
    hub = Hub(s)
    _request(hub, "old", status="interrupted")
    _request(hub, "fresh", status="interrupted")
    old = hub.new_resume_approval("old")
    fresh = hub.new_resume_approval("fresh")
    assert old.timeout_s == s.policy.approvals.pi_decision_timeout_s
    stale = hub.approvals[old.id]["approval"]
    stale.update(created_at=time.time() - s.policy.approvals.pi_decision_timeout_s - 60, timeout_s=3600)
    hub.save_approval(old.id)
    await hub.expire_stale_resume_cards()
    assert old.id not in hub.approvals and fresh.id in hub.approvals
    assert hub.requests["old"]["status"] == "failed" and hub.requests["old"]["outcome"] == "resume_timed_out"
    assert hub.store.get("approval_decision", old.id)["state"] == "timed_out"
    assert hub.requests["fresh"]["status"] == "interrupted" and fresh.id in hub.resume_timers
    await hub.resolve_approval(fresh.id, False)  # a deny is still a deny
    assert hub.requests["fresh"]["error"] == "resume declined" and fresh.id not in hub.resume_timers


# ----- R13: request cancel -----

class CaptureSocket:
    def __init__(self):
        self.sent = []

    async def send_text(self, body):
        import json
        self.sent.append(json.loads(body))


ROSTER = [{"id": name, "name": name, "role": "test", "engine": "mock"} for name in ("cso", "worker")]


def _team_settings(tmp_path):
    s = _settings(tmp_path)
    s.orchestrator.chief_of_staff_agent = None
    s.orchestrator.precedent_agent = None
    return s


async def _until(predicate, timeout=3.0):
    end = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < end, "timed out"
        await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_cancel_stops_a_planning_request_and_its_live_task(tmp_path):
    from labhq.gateway.server import RequestIn

    hub = Hub(_team_settings(tmp_path))
    ws = CaptureSocket()
    hub.register_runner("local", ws, ROSTER, "inc")
    rid = hub.create_request(RequestIn(text="공개 데이터 QC 요약"))
    await _until(lambda: any(m.get("type") == "task.dispatch" for m in ws.sent))
    plan_task = next(m["task"]["id"] for m in ws.sent if m.get("type") == "task.dispatch")
    orchestration = hub.request_tasks[rid]

    cancelling = asyncio.create_task(hub.cancel_request(rid))
    await _until(lambda: {"type": "task.cancel", "task_id": plan_task} in ws.sent)
    # The cancelled turn reports back (with its cost) before the terminal record is written.
    await hub.on_runner_message("local", {"type": "task.result", "task_id": plan_task, "request_id": rid,
                                          "data": TaskResult(task_id=plan_task, agent_id="cso", ok=False,
                                                             error="cancelled", cost_usd=0.4).model_dump(mode="json")})
    out = await asyncio.wait_for(cancelling, 3)
    assert out == {"request_id": rid, "status": "cancelled", "already": False, "cancelled_tasks": [plan_task]}
    assert orchestration.done() and rid not in hub.request_tasks
    req = hub.requests[rid]
    assert req["status"] == "cancelled" and req["outcome"] == "cancelled" and "취소했습니다" in req["report"]
    final = [e for e in hub.events if e["type"] == "request.completed" and e["request_id"] == rid]
    assert len(final) == 1 and final[0]["data"]["status"] == "cancelled" and final[0]["data"]["ok"] is False
    assert final[0]["data"]["cost_usd"] == pytest.approx(0.4) and final[0]["data"]["cost_known"] is True

    # Nothing else is sent, and a second cancel answers the same.
    sent = len(ws.sent)
    await asyncio.sleep(0.05)
    assert not [m for m in ws.sent[sent:] if m.get("type") == "task.dispatch"]
    assert (await hub.cancel_request(rid)) == {"request_id": rid, "status": "cancelled", "already": True,
                                               "cancelled_tasks": [plan_task]}
    assert hub.requests[rid]["status"] == "cancelled"
    assert len([e for e in hub.events if e["type"] == "request.completed" and e["request_id"] == rid]) == 1


@pytest.mark.asyncio
async def test_a_cancelled_turn_that_never_reports_is_unaccounted_not_free(tmp_path):
    from labhq.gateway.server import RequestIn

    hub = Hub(_team_settings(tmp_path))
    hub.cancel_result_wait_s = 0.2
    ws = CaptureSocket()
    hub.register_runner("local", ws, ROSTER, "inc")
    rid = hub.create_request(RequestIn(text="공개 데이터 QC 요약"))
    await _until(lambda: any(m.get("type") == "task.dispatch" for m in ws.sent))
    await hub.cancel_request(rid)
    final = next(e for e in hub.events if e["type"] == "request.completed" and e["request_id"] == rid)
    assert final["data"]["cost_known"] is False and hub.requests[rid]["cost_known"] is False


@pytest.mark.asyncio
async def test_cancel_stops_parallel_steps_and_a_late_card_from_the_runner_is_closed(tmp_path):
    hub = Hub(_settings(tmp_path))
    _request(hub, "r")
    parked = asyncio.Event()

    async def step():
        parked.set()
        await asyncio.sleep(3600)  # a step parked on quota or an answer

    sibling = hub.orchestrator._step_task("r", step())
    await asyncio.wait_for(parked.wait(), 1)
    ws = CaptureSocket()
    hub.register_runner("local", ws, ROSTER, "inc")
    await hub.cancel_request("r")
    await asyncio.sleep(0)
    assert sibling.cancelled()

    late = ApprovalRequest(kind="tool_permission", summary="Bash: rm -rf tmp/", request_id="r", agent_id="worker",
                           task_id="t1").model_dump(mode="json")
    await hub.on_runner_message("local", {"type": "approval.requested", "data": late})
    assert late["id"] not in hub.approvals and hub.store.get("approval_decision", late["id"])["state"] == "expired"
    assert {"type": "approval.resolved", "id": late["id"], "approved": False, "note": "요청 취소로 닫힘"} in ws.sent
    assert not [e for e in hub.events if e["type"] == "approval.requested" and e["data"].get("id") == late["id"]]


@pytest.mark.asyncio
async def test_cancel_releases_a_quota_wait_and_closes_the_requests_cards(tmp_path):
    hub = Hub(_settings(tmp_path))
    _request(hub, "r", results={"s1": TaskResult(task_id="t1", agent_id="worker", ok=True, text="표 완성",
                                                 outputs=["outputs/qc.tsv"], workdir_id="w1").model_dump(mode="json")},
             plan={"steps": [{"id": "s1", "agent_id": "worker"}, {"id": "s2", "agent_id": "worker"}]})
    _request(hub, "other")
    now = time.time()
    held = hub.track_request("r", hub.wait_quota("r", "s2", "codex", resume_at=now + 3600, deadline_at=now + 7200,
                                                  reason="usage limit"))
    card = asyncio.create_task(hub.request_approval("budget", "계속할까요?", "r"))
    others = asyncio.create_task(hub.request_approval("clarify", "다른 요청", "other"))
    await _until(lambda: hub.requests["r"].get("quota_waits") and len(hub.approvals) == 2)

    await hub.cancel_request("r")
    assert held.cancelled() and hub.quota_hold("codex") is None
    decision = await asyncio.wait_for(card, 1)
    assert decision["state"] == "expired" and not decision["approved"]
    assert [entry["approval"]["request_id"] for entry in hub.approvals.values()] == ["other"]
    req = hub.requests["r"]
    assert req["status"] == "cancelled" and "quota_waits" not in req and "pi_waits" not in req
    assert "s1 (worker): 완료" in req["report"] and "w1/outputs/qc.tsv" in req["report"]
    assert "s2 (worker): 실행하지 않음" in req["report"] and "한도 대기 중이던 단계: s2" in req["report"]
    assert hub.requests["other"]["status"] == "waiting_pi"
    others.cancel()


@pytest.mark.asyncio
async def test_a_cancelled_interrupted_request_gets_no_resume_card_after_a_restart(tmp_path):
    s = _settings(tmp_path)
    hub = Hub(s)
    _request(hub, "r", status="interrupted")
    resume = hub.new_resume_approval("r")
    await hub.cancel_request("r")
    assert resume.id not in hub.approvals and hub.requests["r"]["status"] == "cancelled"
    assert hub.store.get("approval_decision", resume.id)["state"] == "expired"
    restarted = Hub(s)
    assert restarted.requests["r"]["status"] == "cancelled" and not restarted.approvals


def test_cancel_endpoint_and_cli(tmp_path, monkeypatch, capsys):
    from fastapi.testclient import TestClient

    from labhq import cli
    from labhq.gateway.server import create_app

    s = _settings(tmp_path)
    app = create_app(s)
    hub = app.state.hub
    for rid, status in (("r", "interrupted"), ("finished", "done")):
        hub.requests[rid] = {"id": rid, "text": rid, "mode": "orchestrate", "status": status, "created_at": 1.0}
    auth = {"Authorization": f"Bearer {s.gateway.client_token}"}
    with TestClient(app) as client:
        assert client.post("/api/requests/r/cancel").status_code == 401
        first = client.post("/api/requests/r/cancel", headers=auth)
        assert first.status_code == 200 and first.json()["status"] == "cancelled" and not first.json()["already"]
        again = client.post("/api/requests/r/cancel", headers=auth)
        assert again.status_code == 200 and again.json()["already"] is True
        assert client.post("/api/requests/finished/cancel", headers=auth).status_code == 409
        assert client.post("/api/requests/nope/cancel", headers=auth).status_code == 404

    calls = []

    def api(_s, method, path, **kw):
        calls.append((method, path))
        return {"request_id": "req_x", "status": "cancelled", "already": False, "cancelled_tasks": ["t1", "t2"]}

    monkeypatch.setattr(cli, "_api", api)
    monkeypatch.setattr(Settings, "load", classmethod(lambda cls, path=None: s))
    cli.main(["cancel", "req_x"])
    assert calls == [("POST", "/api/requests/req_x/cancel")]
    assert "req_x: 취소했습니다 · 멈춘 작업 2개" in capsys.readouterr().out


# ----- R15: no runner -----

@pytest.mark.asyncio
async def test_a_request_without_a_runner_waits_and_runs_when_one_registers(tmp_path):
    from labhq.gateway.server import RequestIn

    hub = Hub(_team_settings(tmp_path))
    rid = hub.create_request(RequestIn(text="공개 데이터 QC 요약"))
    await _until(lambda: hub.requests[rid]["status"] == "waiting_for_runner")
    assert list(hub.requests[rid]["runner_waits"].values())[0]["agent_id"] == "cso"
    assert not hub.store.all("task")  # nothing was sent, so nothing is uncertain or spent
    wait = next(e for e in hub.events if e["type"] == "request.runner_wait")
    assert wait["data"]["missing_agents"] == ["cso"] and "labhq runner" in wait["data"]["message"]
    assert any(e["type"] == "request.status" and e["data"]["status"] == "waiting_for_runner" for e in hub.events)

    ws = CaptureSocket()
    hub.register_runner("local", ws, ROSTER, "inc")
    await _until(lambda: any(m.get("type") == "task.dispatch" for m in ws.sent))
    assert hub.requests[rid]["status"] == "running" and "runner_waits" not in hub.requests[rid]
    assert [e["data"]["status"] for e in hub.events if e["type"] == "request.status"] == [
        "waiting_for_runner", "running"]  # an open page sees it move on
    # The plan sees the roster that arrived with the runner, so a plan naming its staff runs.
    plan = next(m["task"] for m in ws.sent if m.get("type") == "task.dispatch")
    assert '"worker"' in plan["prompt"] or "worker" in plan["prompt"]
    steps = [{"id": "s1", "agent_id": "worker", "instruction": "QC 표 만들기", "depends_on": []}]
    await hub.on_runner_message("local", {"type": "task.result", "task_id": plan["id"], "request_id": rid,
                                          "data": TaskResult(task_id=plan["id"], agent_id="cso", ok=True,
                                                             structured={"steps": steps}).model_dump(mode="json")})
    await _until(lambda: any(m.get("type") == "task.dispatch" and m["task"]["agent_id"] == "worker"
                             for m in ws.sent))
    hub.cancel_result_wait_s = 0.1
    await hub.cancel_request(rid)


@pytest.mark.asyncio
async def test_a_runner_wait_past_its_bound_fails_with_a_korean_next_step(tmp_path):
    from labhq.gateway.server import RequestIn

    s = _team_settings(tmp_path)
    s.gateway.runner_wait_s = 0.2
    hub = Hub(s)
    rid = hub.create_request(RequestIn(text="공개 데이터 QC 요약"))
    await _until(lambda: hub.requests[rid]["status"] == "failed")
    report = hub.requests[rid]["report"] + hub.requests[rid].get("report_appendix", "")
    assert "러너가 1분 안에 연결되지 않아 cso 작업을 보내지 못했습니다" in report and "labhq runner" in report


@pytest.mark.asyncio
async def test_an_agent_no_runner_ever_hosted_still_fails_at_once(tmp_path):
    hub = Hub(_settings(tmp_path))
    hub.register_runner("local", CaptureSocket(), ROSTER, "inc")
    _request(hub, "r")
    outcome = await asyncio.wait_for(hub.dispatch(Task(agent_id="ghost", request_id="r", prompt="x")), 1)
    assert not outcome.ok and "no runner hosts agent 'ghost'" in outcome.error and outcome.cost_usd == 0
    # The same when the connected runner used to host the agent and dropped it from its roster.
    hub.register_runner("local", CaptureSocket(), [*ROSTER, {"id": "dropped", "name": "d", "role": "t",
                                                            "engine": "mock"}], "inc")
    hub.register_runner("local", CaptureSocket(), ROSTER, "inc")
    outcome = await asyncio.wait_for(hub.dispatch(Task(agent_id="dropped", request_id="r", prompt="x")), 1)
    assert not outcome.ok and "no runner hosts agent 'dropped'" in outcome.error


def test_health_says_whether_a_runner_is_connected(tmp_path):
    from fastapi.testclient import TestClient

    from labhq.gateway.server import create_app

    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        health = client.get("/api/health").json()
    assert health["runners"] == [] and health["runner_online"] is False and health["waiting_for_runner"] == 0


# ----- R2: orphan cards and a readable resume card -----

def _runner_card(agent_id="worker", task_id="t1"):
    return ApprovalRequest(kind="tool_permission", summary="Bash: rm -rf tmp/", request_id="r", agent_id=agent_id,
                           task_id=task_id, timeout_s=90).model_dump(mode="json")


@pytest.mark.asyncio
async def test_a_runner_restart_expires_the_cards_its_old_process_asked(tmp_path):
    hub = Hub(_settings(tmp_path))
    _request(hub, "r")
    hub.register_runner("local", CaptureSocket(), ROSTER, "first")
    card = _runner_card()
    await hub.on_runner_message("local", {"type": "approval.requested", "data": card})
    assert hub.approvals[card["id"]]["origin_incarnation"] == "first"

    assert await hub.expire_orphan_approvals("local", "first") == []  # the same process reconnecting keeps it
    assert card["id"] in hub.approvals

    replacement = CaptureSocket()
    hub.register_runner("local", replacement, ROSTER, "second")
    assert await hub.expire_orphan_approvals("local", "second") == [card["id"]]
    assert card["id"] not in hub.approvals and not hub.store.get("approval", card["id"])
    assert hub.store.get("approval_decision", card["id"])["state"] == "expired"
    assert hub.store.get("decision", card["id"]) is None  # the new process never asked; nothing is sent to it
    await hub.flush_decisions("local")
    assert not [m for m in replacement.sent if m.get("type") == "approval.resolved"]
    assert any(e["type"] == "approval.expired" and e["data"]["id"] == card["id"] for e in hub.events)


@pytest.mark.asyncio
async def test_a_card_from_a_runner_that_no_longer_hosts_the_agent_expires(tmp_path):
    s = _settings(tmp_path)
    hub = Hub(s)
    gone, kept = _runner_card("worker", "t_old"), _runner_card("aligner", "t_hpc")
    for card, origin in ((gone, "local"), (kept, "hpc")):  # saved before cards carried the asking process
        hub.approvals[card["id"]] = {"approval": card, "origin": origin}
        hub.save_approval(card["id"])
    restarted = Hub(s)
    restarted.register_runner("lab-workstation", CaptureSocket(), ROSTER, "inc")
    assert await restarted.expire_orphan_approvals("lab-workstation", "inc") == [gone["id"]]
    assert "직원이 다른 러너(lab-workstation)로 옮겨" in restarted.store.get("approval_decision", gone["id"])["note"]
    assert list(restarted.approvals) == [kept["id"]]  # another runner's agent, still away: it may come back


def test_a_resume_card_names_the_request_its_date_and_the_steps_left(tmp_path):
    hub = Hub(_settings(tmp_path))
    created = time.mktime((2026, 9, 28, 10, 30, 0, 0, 0, -1))
    _request(hub, "r", status="interrupted", created_at=created,
             text="새로 받은 WGS 배치 표준 QC를 돌리고 결과를 요약해 주세요 " * 3,
             plan={"steps": [{"id": sid} for sid in ("s1", "s2", "s3")]},
             results={"s1": {"ok": True}})
    card = hub.new_resume_approval("r")
    assert card.summary.startswith('중단된 요청 "새로 받은 WGS 배치 표준 QC')
    assert "(09-28 10:30 접수)" in card.summary and card.summary.endswith("남은 단계: s2, s3")
    assert "[" not in card.summary
    assert card.detail == {"request_text": card.detail["request_text"], "created_at": created, "steps": ["s2", "s3"]}


# ----- what an open page needs without a reload (#494) -----

@pytest.mark.asyncio
async def test_restart_and_resume_status_changes_reach_open_pages_as_events(tmp_path):
    s = _settings(tmp_path)
    hub = Hub(s)
    _request(hub, "r")
    restarted = Hub(s)
    replay = [e for e in restarted.store.events_since(0) if e.get("request_id") == "r"]
    assert [(e["type"], (e["data"].get("status") or e["data"].get("kind"))) for e in replay] == [
        ("request.status", "interrupted"), ("approval.requested", "resume")]
    assert replay[0]["data"]["previous"] == "running"

    card = next(iter(restarted.approvals))
    await restarted.resolve_approval(card, True)
    await _until(lambda: any(e["type"] == "request.status" and e["data"]["status"] == "waiting_for_runner"
                             for e in restarted.events))
    await restarted.cancel_request("r")


def test_a_snapshot_keeps_the_quota_deadline_of_a_waiting_step(tmp_path):
    hub = Hub(_settings(tmp_path))
    _request(hub, "r", plan={"steps": [{"id": "s1", "agent_id": "worker"}]},
             quota_waits={"s1": {"engine": "codex", "resume_at": 100.0, "deadline_at": 900.0, "reason": "limit"}})
    detail = hub.request_step_details("r", hub.requests["r"])["s1"]
    assert detail["quota_resume_at"] == 100.0 and detail["quota_deadline_at"] == 900.0


# ----- PR #502 review: direct requests to known staff, resume countdown -----

@pytest.mark.asyncio
async def test_a_direct_request_to_known_staff_waits_for_the_first_runner(tmp_path):
    import httpx

    from labhq.gateway.server import create_app

    s = _settings(tmp_path)
    core = tmp_path / "agents" / "core"
    core.mkdir(parents=True)
    (core / "analyst.yaml").write_text("id: analyst\nname: Analyst\nrole: test\nengine: mock\n", encoding="utf-8")
    s.runner.agents_dir = str(tmp_path / "agents")
    Hub(s).set_roster("lab", [{"id": "curator", "name": "c", "role": "t", "engine": "mock"}])  # an earlier runner
    app = create_app(s)
    hub = app.state.hub
    hub.runners.clear()  # set_roster above left no live connection; the fresh gateway has no runner
    auth = {"Authorization": f"Bearer {s.gateway.client_token}"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gw") as client:
        ids = {}
        for agent in ("analyst", "curator"):
            response = await client.post("/api/requests", headers=auth,
                                         json={"text": "표 정리", "mode": "direct", "agent_id": agent})
            assert response.status_code == 200, response.text
            ids[agent] = response.json()["request_id"]
        unknown = await client.post("/api/requests", headers=auth,
                                    json={"text": "x", "mode": "direct", "agent_id": "ghost"})
        assert unknown.status_code == 404
    await _until(lambda: all(hub.requests[rid]["status"] == "waiting_for_runner" for rid in ids.values()))
    assert not hub.store.all("task")

    ws = CaptureSocket()
    roster = [{"id": name, "name": name, "role": "t", "engine": "mock"} for name in ("analyst", "curator")]
    hub.register_runner("lab", ws, roster, "inc")
    await _until(lambda: {m["task"]["agent_id"] for m in ws.sent if m.get("type") == "task.dispatch"}
                 == {"analyst", "curator"})
    hub.cancel_result_wait_s = 0.1
    for rid in ids.values():
        await hub.cancel_request(rid)


def test_a_resume_card_saved_with_the_old_hour_gets_the_decision_bound(tmp_path):
    s = _settings(tmp_path)
    hub = Hub(s)
    _request(hub, "r", status="interrupted")
    card = hub.new_resume_approval("r")
    hub.approvals[card.id]["approval"]["timeout_s"] = 3600  # what a card saved before PR #502 carries
    hub.save_approval(card.id)
    restarted = Hub(s)
    assert restarted.approvals[card.id]["approval"]["timeout_s"] == s.policy.approvals.pi_decision_timeout_s
    assert restarted.snapshot()["data"]["approvals"][0]["timeout_s"] == s.policy.approvals.pi_decision_timeout_s


def test_a_resume_card_after_every_step_finished_says_no_step_is_left(tmp_path):
    # Trial 2026-10-08: a research request restarted at its continue card read "남은 단계: 요청 전체" with 12/12 done.
    hub = Hub(_settings(tmp_path))
    _request(hub, "r", status="interrupted", plan={"steps": [{"id": "s1"}, {"id": "s2"}]},
             results={"s1": {"ok": True}, "s2": {"ok": True}})
    _request(hub, "unplanned", status="interrupted")
    assert hub.new_resume_approval("r").summary.endswith("남은 단계: 없음(단계는 모두 끝났고 그 뒤 검토·결정·보고서부터 이어 갑니다)")
    assert hub.new_resume_approval("unplanned").summary.endswith("남은 단계: 요청 전체")


@pytest.mark.asyncio
async def test_shutdown_readiness_names_follow_ups_and_recruitments_outside_active_requests(tmp_path):
    hub = Hub(_settings(tmp_path))
    _request(hub, "done", status="done", followups=[{"id": "fu_1", "text": "그림 다시", "status": "running"},
                                                    {"id": "fu_0", "text": "끝난 것", "status": "done"}])
    assert hub.shutdown_readiness() == {
        "requests": [], "recruits": 0, "ready": False,
        "followups": [{"request_id": "done", "followup_id": "fu_1", "text": "그림 다시"}]}
    hub.requests["done"]["followups"][0]["status"] = "done"
    assert hub.shutdown_readiness()["ready"] is True
    hub.recruits_running["lab"] = 1
    assert hub.shutdown_readiness()["ready"] is False and hub.shutdown_readiness()["recruits"] == 1
    await hub.on_runner_message("lab", {"type": "recruit.done", "ts": 1.0, "data": {"agent": {"id": "c_x"}}})
    assert hub.shutdown_readiness() == {"requests": [], "followups": [], "recruits": 0, "ready": True}
