"""Request comparison and the two-config launch failure behind #559."""
import json
from pathlib import Path

import pytest

from labhq import cli
from labhq.gateway.server import Hub
from labhq.research import semantics_shadow as shadow
from labhq.settings import Settings
from tests.test_semantics_shadow_report import _line


def config(tmp_path, mode="ab", name="labhq.yaml"):
    path = tmp_path / name
    path.write_text(
        f"gateway:\n  state_dir: '{tmp_path.as_posix()}/state'\n"
        "  client_token: unit-client\n"
        f"semantics: {{mode: '{mode}', actions: confirm}}\n", encoding="utf-8")
    return Settings.load(str(path))


def process_record(settings, other):
    path = cli._pid_file(settings, "gateway")
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {"pid": 1234, "role": "gateway", "created": "unit-generation", "config": other.config_path}
    path.write_text(json.dumps(record), encoding="utf-8")
    return path, record


@pytest.mark.parametrize("healthy", [False, True])
def test_up_does_not_claim_ab_is_running_with_an_off_config(tmp_path, monkeypatch, capsys, healthy):
    intended = config(tmp_path)
    running = config(tmp_path, "off", "other.yaml")
    path, record = process_record(intended, running)
    monkeypatch.setattr(cli, "_process_created", lambda pid: "unit-generation")
    monkeypatch.setattr(cli, "_health", lambda s: {"runners": [s.runner.id]} if healthy else None)
    monkeypatch.setattr(cli, "_start_background", lambda *a, **k: pytest.fail("must not start another process"))
    with pytest.raises(SystemExit) as stopped:
        cli._up_unlocked(intended)
    assert stopped.value.code == 2
    assert "다른 설정 파일" in capsys.readouterr().err
    assert json.loads(path.read_text(encoding="utf-8")) == record


@pytest.mark.parametrize("alive", [True, False])
def test_request_report_explains_config_mismatch_without_writing(tmp_path, monkeypatch, capsys, alive):
    settings = config(tmp_path)
    other = config(tmp_path, "off", "other.yaml")
    path, _ = process_record(settings, other)
    monkeypatch.setattr(cli, "_process_created", lambda pid: "unit-generation" if alive else None)
    from labhq.store import StateStore
    store = StateStore(tmp_path / "state/gateway.sqlite3")
    store.put("request", "req_missing", {"id": "req_missing", "mode": "orchestrate", "status": "done"})
    store.close()
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    with pytest.raises(SystemExit) as stopped:
        cli.main(["-c", settings.config_path, "semantics", "report", "--request", "req_missing"])
    assert stopped.value.code == 0
    out = capsys.readouterr().out
    assert "그림자 기록 없음" in out and "다른 설정 파일" in out and "실제 진행" in out
    if not alive:
        assert "종료된 gateway" in out
    assert {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before
    assert path.exists()


@pytest.mark.parametrize("resumed", [False, True])
async def test_general_ab_request_records_after_restart_and_compares(tmp_path, capsys, resumed):
    settings = config(tmp_path)
    hub = Hub(settings)
    rid = "req_general"
    hub.requests[rid] = {"id": rid, "status": "running", "mode": "orchestrate", "created_at": 1,
                         "plan": {"steps": [{"id": "s1", "agent_id": "analyst", "outputs": ["result.tsv"]}]},
                         "results": {"s1": {"ok": True, "outputs": ["result.tsv"]}}}
    hub.save_request(rid)
    if resumed:
        hub.store.close()
        hub = Hub(settings)
        assert hub.requests[rid]["status"] == "interrupted"
        # Exercise the real resume dispatcher, with only execution replaced by a local terminal checkpoint.
        hub.resume_agents = lambda request_id: set()

        async def finish(request_id, resume=False):
            assert resume
            hub.requests[request_id]["status"] = "done"
            hub.commit_terminal(request_id, "request.completed", {})

        hub.orchestrator.run_request = finish
        await hub.resume_when_ready(rid)
    else:
        hub.requests[rid]["status"] = "done"
        hub.commit_terminal(rid, "request.completed", {})
    assert hub.semantics_shadow.drain(10)
    lines, _ = shadow.read_lines(hub.semantics_shadow.paths)
    own = [line for line in lines if line.get("request_id") == rid and line["type"] == "request"]
    assert len(own) == 1 and "arm" not in own[0] and own[0]["epoch"] == 1
    with pytest.raises(SystemExit) as stopped:
        cli.main(["-c", settings.config_path, "semantics", "report", "--request", rid, "--json"])
    assert stopped.value.code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["request_id"] == rid and report["shadow"]["status"] == "done"
    assert report["actual"]["steps_done"] == 1 and report["actual"]["outputs_reported"] == 1
    hub.store.close()


def test_request_comparison_uses_only_valid_matching_lines_and_plain_text(tmp_path, capsys):
    settings = config(tmp_path)
    from labhq.store import StateStore
    store = StateStore(tmp_path / "state/gateway.sqlite3")
    store.put("request", "req_r0001", {"id": "req_r0001", "status": "done", "mode": "orchestrate",
              "plan": {"steps": [{"id": "s1"}]}, "results": {"s1": {"ok": True, "outputs": ["secret.tsv"]}}})
    store.put("approval_decision", "appr_one", {"approval": {"request_id": "req_r0001"}, "approved": True})
    store.close()
    paths = shadow.ShadowPaths(tmp_path / "state/semantics")
    shadow.append_line(paths, _line(1, candidates=2))
    shadow.append_line(paths, _line(2, candidates=99))
    broken = _line(1, candidates=90)
    broken["objects"]["objects"] = {"Task": "broken"}
    shadow.append_line(paths, broken)
    with pytest.raises(SystemExit) as stopped:
        cli.main(["-c", settings.config_path, "semantics", "report", "--request", "req_r0001"])
    assert stopped.value.code == 0
    out = capsys.readouterr().out
    for phrase in ("재사용 후보 2건", "완료 1", "승인 1", "손상", "입력", "산출", "행동 판단"):
        assert phrase in out
    assert "99" not in out and "secret.tsv" not in out and "**" not in out and "|---" not in out


def test_unknown_request_report_does_not_create_state(tmp_path, capsys):
    settings = config(tmp_path)
    with pytest.raises(SystemExit) as stopped:
        cli.main(["-c", settings.config_path, "semantics", "report", "--request", "req_absent"])
    assert stopped.value.code == 1
    assert "요청을 찾지 못했습니다" in capsys.readouterr().out
    assert not (tmp_path / "state").exists()
