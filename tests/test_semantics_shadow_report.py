"""`labhq semantics report | enable | mark` (#150 B1): local records only, no network."""
from __future__ import annotations

import json
import socket
import time
from datetime import date

import pytest

from labhq.cli import main
from labhq.research import semantics_shadow as shadow


def _line(i, *, ms=5.0, lane="general", candidates=0, unknown=0.5, prov="ok", objects="ok", epoch=1):
    return {"v": 1, "type": "request", "ts": time.time() - 3600 + i, "epoch": epoch, "request_id": f"req_r{i:04d}",
            "lane": lane, "status": "done", "ms": ms, "snapshot_ms": 1.0, "busy_skipped": 0,
            "provenance": {"status": prov, "ms": ms / 2, "candidates": candidates, "unknown_ratio": unknown,
                           "incomplete": False, "excluded": {"type_unknown": 2}, "lineage": {"gaps": 1}},
            "objects": {"status": objects, "ms": ms / 4, "objects": {"Task": 4, "Step": 2}, "link_total": 6,
                        "unresolved": 1},
            "hash": {"hashed": 1, "observed_new": 1, "verified": 0, "changed": 0, "workspaces": {"ok": 1}}}


@pytest.fixture
def config(tmp_path, monkeypatch):
    path = tmp_path / "labhq.yaml"
    path.write_text(f"gateway: {{state_dir: '{(tmp_path / 'state').as_posix()}'}}\nsemantics: shadow\n",
                    encoding="utf-8")

    def no_network(*args, **kwargs):
        raise AssertionError("report must not use the network")

    monkeypatch.setattr(socket, "socket", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)
    return path


def _write(tmp_path, lines):
    paths = shadow.ShadowPaths(tmp_path / "state" / "semantics")
    for line in lines:
        shadow.append_line(paths, line)
    return paths


def _cli(capsys, config, *args):
    with pytest.raises(SystemExit) as done:
        main(["--config", str(config), "semantics", *args])
    return done.value.code, capsys.readouterr().out


def test_the_report_tables_both_models_and_the_auto_off_history(tmp_path, config, capsys):
    paths = _write(tmp_path, [_line(1), _line(2, prov="timeout", objects="error"), _line(3, candidates=2, lane="research")])
    shadow.append_line(paths, {"v": 1, "type": "auto_off", "ts": time.time(), "epoch": 1, "reason": "worker_stuck"})
    code, out = _cli(capsys, config, "report", "--today", "2026-10-15")
    assert code == 0
    assert "요청 3건" in out and "| 출처 의미 모델 | 3 | 2 | 1 | 0 |" in out and "| 객체·링크 뷰 | 3 | 2 | 0 | 1 |" in out
    assert "후보 있는 요청 1" in out and "worker_stuck" in out and "제거 제안: 없음" in out
    code, out = _cli(capsys, config, "report", "--json", "--today", "2026-10-15")
    rep = json.loads(out)
    assert rep["requests"] == 3 and rep["research_with_candidates"] == 1
    assert rep["provenance"]["timeout"] == 1 and rep["objects"]["error"] == 1
    assert rep["objects"]["unresolved"] == 2 and rep["auto_off"][0]["reason"] == "worker_stuck"


def test_at_the_deadline_the_report_proposes_removal(tmp_path, config, capsys):
    _write(tmp_path, [_line(1, lane="research", candidates=1)])
    rep = shadow.build_report(shadow.ShadowPaths(tmp_path / "state" / "semantics"), date(2026, 12, 30))
    assert rep["deadline"] == "2026-12-30"
    assert any("판정 기한" in p for p in rep["propose_removal"]) and any("효용 미입증" in p for p in rep["propose_removal"])
    code, out = _cli(capsys, config, "report", "--today", "2026-12-30")
    assert code == 0 and "PROPOSE_REMOVAL" in out and "결정은 PI" in out


@pytest.mark.parametrize("kind", ["slow", "unknown", "wrong"])
def test_threshold_proposals_wait_for_enough_records(tmp_path, kind):
    lines = [_line(i, ms=1500 if kind == "slow" else 5, unknown=0.95 if kind == "unknown" else 0.5) for i in range(14)]
    paths = _write(tmp_path, lines)
    if kind == "wrong":
        for i, verdict in enumerate(["wrong_other", "ok", "ok", "ok"]):
            shadow.mark(paths, f"req_r{i:04d}", "sem:0a1b2c3d", verdict)
    assert shadow.build_report(paths, date(2026, 10, 15))["propose_removal"] == []
    shadow.append_line(paths, _line(99, ms=1500 if kind == "slow" else 5, unknown=0.95 if kind == "unknown" else 0.5))
    if kind == "wrong":
        shadow.mark(paths, "req_r0099", "sem:0a1b2c3d", "ok")
    proposals = shadow.build_report(paths, date(2026, 10, 15))["propose_removal"]
    assert len(proposals) == 1 and {"slow": "지연", "unknown": "기록 공백", "wrong": "오답"}[kind] in proposals[0]


def test_enable_shows_the_reason_and_opens_a_new_epoch(tmp_path, config, capsys):
    paths = shadow.ShadowPaths(tmp_path / "state" / "semantics")
    shadow.read_state(paths)
    shadow.write_disabled(paths, "consecutive_failures", 1)
    code, out = _cli(capsys, config, "enable")
    assert code == 0 and "consecutive_failures" in out and "epoch 2" in out
    assert not paths.disabled.exists() and shadow.read_state(paths)["epoch"] == 2
    code, out = _cli(capsys, config, "report")
    assert "상태: on · 설정 shadow · 자동 off - · epoch 2" in out


def test_mark_checks_its_arguments_and_wrong_identity_turns_semantics_off(tmp_path, config, capsys):
    code, out = _cli(capsys, config, "mark", "req_abc", "outputs/x.tsv", "ok")
    assert code == 1 and "sem:<8 hex>" in out
    code, out = _cli(capsys, config, "mark", "req_abc", "sem:0a1b2c3d", "wrong_identity")
    assert code == 0 and "wrong_identity" in out
    paths = shadow.ShadowPaths(tmp_path / "state" / "semantics")
    assert shadow.read_disabled(paths)["reason"] == "wrong_identity"
    rep = shadow.build_report(paths)
    assert rep["state"]["on"] is False and rep["marks"] == {"reviewed": 1, "wrong": 1}


@pytest.mark.parametrize("text,setting", [("", "off"), ("semantics: {mode: advisory}\n", "invalid")])
def test_the_report_says_off_when_the_setting_is_off(tmp_path, capsys, text, setting):
    path = tmp_path / "labhq.yaml"
    path.write_text(f"gateway: {{state_dir: '{(tmp_path / 'state').as_posix()}'}}\n" + text, encoding="utf-8")
    with pytest.raises(SystemExit):
        main(["--config", str(path), "semantics", "report", "--json"])
    state = json.loads(capsys.readouterr().out)["state"]
    assert state["on"] is False and state["setting"] == setting


@pytest.mark.parametrize("args", [["enable"], ["mark", "req_abc", "sem:0a1b2c3d", "wrong_identity"]],
                         ids=["enable", "mark"])
def test_the_cli_writes_nothing_inside_a_git_work_tree(tmp_path, capsys, args):
    (tmp_path / ".git").mkdir()
    path = tmp_path / "labhq.yaml"
    path.write_text(f"gateway: {{state_dir: '{(tmp_path / 'state').as_posix()}'}}\nsemantics: shadow\n",
                    encoding="utf-8")
    with pytest.raises(SystemExit) as done:
        main(["--config", str(path), "semantics", *args])
    assert done.value.code == 1 and "git work tree" in capsys.readouterr().out
    assert not (tmp_path / "state").exists()
