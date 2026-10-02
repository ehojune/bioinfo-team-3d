"""`labhq semantics report | enable | mark` (#150 B1): local records only, no network."""
from __future__ import annotations

import json
import socket
import time
from datetime import date

import pytest

from labhq.cli import main
from labhq.research import semantics_shadow as shadow

REF = "sem:0a1b2c3d"


def _line(i, *, ms=5.0, lane="general", candidates=0, unknown=0.5, prov="ok", objects="ok", epoch=1):
    return {"v": 1, "type": "request", "ts": time.time() - 3600 + i, "epoch": epoch, "request_id": f"req_r{i:04d}",
            "lane": lane, "status": "done", "ms": ms, "snapshot_ms": 1.0, "busy_skipped": 0,
            "provenance": {"status": prov, "ms": ms / 2, "candidates": candidates, "unknown_ratio": unknown,
                           "incomplete": False, "excluded": {"type_unknown": 2}, "lineage": {"gaps": 1},
                           "candidate_refs": [REF]},
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


def test_ab_report_compares_requests_candidates_reference_failure_and_cost(tmp_path):
    advisory = {**_line(1, lane="research", candidates=1), "arm": "advisory", "offered": 1, "referenced": True,
                "cost_usd": 1.5, "cost_known": True}
    shadow_arm = {**_line(2, lane="research", candidates=1), "arm": "shadow", "offered": 1, "referenced": False,
                  "status": "failed", "cost_usd": 0, "cost_known": False}
    paths = _write(tmp_path, [advisory, shadow_arm])

    rep = shadow.build_report(paths, date(2026, 10, 10), "ab")

    assert rep["arms"]["advisory"] == {"requests": 1, "with_candidates": 1, "referenced": 1,
                                         "reference_rate": 1.0, "failed": 0, "failure_rate": 0.0,
                                         "cost_usd": 1.5, "cost_unknown": 0}
    assert rep["arms"]["shadow"]["failure_rate"] == 1.0 and rep["arms"]["shadow"]["cost_unknown"] == 1
    assert rep["ab_window"] == {"requests": 2, "target_requests": 10, "deadline": "2026-10-23",
                                 "reached": False}


def test_ab_report_counts_research_requests_only_so_others_cannot_close_the_window(tmp_path):
    research = {**_line(1, lane="research"), "arm": "advisory", "offered": 1, "referenced": True,
                "cost_usd": 1.0, "cost_known": True}
    others = [{**_line(i, lane=lane), "arm": "shadow", "offered": 0, "referenced": False, "status": "failed",
               "cost_usd": 2.0, "cost_known": True}
              for i, lane in enumerate(["general"] * 5 + ["direct"] * 5, start=2)]
    paths = _write(tmp_path, [research, *others])

    rep = shadow.build_report(paths, date(2026, 10, 10), "ab")

    assert rep["arms"]["advisory"]["requests"] == 1 and rep["arms"]["shadow"]["requests"] == 0
    assert rep["arms"]["shadow"]["cost_usd"] == 0 and rep["arms"]["shadow"]["failure_rate"] is None
    assert rep["ab_window"]["requests"] == 1 and rep["ab_window"]["reached"] is False


def test_ab_report_takes_candidates_from_the_plan_time_offer_not_the_end(tmp_path):
    def row(i, end, offered, referenced):
        return {**_line(i, lane="research", candidates=end), "arm": "advisory", "offered": offered,
                "referenced": referenced, "cost_usd": 0, "cost_known": True}

    # offered at plan time but gone at the end (used, then not), and a candidate that appeared only at the end
    paths = _write(tmp_path, [row(1, 0, 2, True), row(2, 0, 1, False), row(3, 3, 0, False)])

    rep = shadow.build_report(paths, date(2026, 10, 10), "ab")

    assert rep["arms"]["advisory"]["with_candidates"] == 2 and rep["arms"]["advisory"]["reference_rate"] == 0.5


def test_info_boundary_report_names_only_the_field_and_category(tmp_path, config, capsys):
    paths = shadow.ShadowPaths(tmp_path / "state" / "semantics")
    shadow.read_state(paths)
    detail = [{"field": "objects", "class": "path"}]
    shadow.write_disabled(paths, "info_boundary", 1, boundary=detail)
    shadow.append_line(paths, {"v": 1, "type": "auto_off", "ts": time.time(), "epoch": 1,
                               "reason": "info_boundary", "counts": {}, "boundary": detail})
    code, out = _cli(capsys, config, "report", "--today", "2026-10-15")
    assert code == 0 and "objects:path" in out and "info_boundary" in out
    code, out = _cli(capsys, config, "report", "--json", "--today", "2026-10-15")
    report = json.loads(out)
    assert report["state"]["boundary"] == detail and report["auto_off"][0]["boundary"] == detail


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
    _write(tmp_path, [{**_line(1), "request_id": "req_abc"}])
    code, out = _cli(capsys, config, "mark", "req_abc", "outputs/x.tsv", "ok")
    assert code == 1 and "sem:<8 hex>" in out
    code, out = _cli(capsys, config, "mark", "req_abc", "sem:0a1b2c3d", "wrong_identity")
    assert code == 0 and "wrong_identity" in out
    paths = shadow.ShadowPaths(tmp_path / "state" / "semantics")
    assert shadow.read_disabled(paths)["reason"] == "wrong_identity"
    rep = shadow.build_report(paths)
    assert rep["state"]["on"] is False and rep["marks"] == {"reviewed": 1, "wrong": 1}


@pytest.mark.parametrize("rid, ref", [("req_nope", REF), ("req_abc", "sem:deadbeef"), ("req_r0001", REF)],
                         ids=["unknown_request", "unknown_ref", "ref_of_another_request"])
def test_mark_refuses_a_candidate_the_shadow_never_recorded(tmp_path, config, capsys, rid, ref):
    """#175: a typo in the request id or ref is refused before anything is written, so it cannot turn the shadow
    off (wrong_identity) or skew the wrong ratio."""
    paths = _write(tmp_path, [{**_line(1), "request_id": "req_abc"},
                              {**_line(2), "request_id": "req_r0001",
                               "provenance": {**_line(2)["provenance"], "candidate_refs": ["sem:11112222"]}}])
    shadow.read_state(paths)
    before = {f.name: f.read_bytes() for f in paths.root.iterdir()}
    code, out = _cli(capsys, config, "mark", rid, ref, "wrong_identity")
    assert code == 1 and "no recorded candidate" in out and "nothing written" in out
    assert {f.name: f.read_bytes() for f in paths.root.iterdir()} == before
    assert shadow.read_disabled(paths) is None and shadow.build_report(paths)["marks"]["reviewed"] == 0


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


BAD_NESTED = {"provenance_list": {"provenance": ["ok"]}, "objects_text": {"objects": "ok"},
              "hash_number": {"hash": 3}, "excluded_list": {"provenance": {"status": "ok", "excluded": [1]}},
              "lineage_text": {"provenance": {"status": "ok", "lineage": "gaps"}},
              "object_counts_text": {"objects": {"status": "ok", "objects": {"Task": "four"}}},
              "workspaces_list": {"hash": {"hashed": 1, "workspaces": ["ok"]}},
              "candidates_text": {"provenance": {"status": "ok", "candidates": "two"}},
              "ms_infinite": {"ms": float("inf")},
              # the two shapes of #162, already counted as broken since dc01c50
              "objects_empty_list": {"objects": []},
              "excluded_text_value": {"provenance": {"status": "ok", "excluded": {"x": "a"}}}}


@pytest.mark.parametrize("bad", list(BAD_NESTED))
def test_a_request_row_with_a_malformed_payload_is_a_broken_line(tmp_path, config, capsys, bad):
    """Valid JSON in an older or torn shape is counted as broken; the rows around it still add up."""
    paths = _write(tmp_path, [_line(1), _line(3, candidates=2, lane="research")])
    with paths.log.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({**_line(2), **BAD_NESTED[bad]}) + "\n")
    rep = shadow.build_report(paths, date(2026, 10, 15))
    assert rep["requests"] == 2 and rep["broken_lines"] == 1 and rep["research_with_candidates"] == 1
    assert rep["provenance"]["n"] == 2 and rep["objects"]["unresolved"] == 2
    code, out = _cli(capsys, config, "report", "--json", "--today", "2026-10-15")
    assert code == 0 and json.loads(out)["broken_lines"] == 1


def test_the_report_cli_never_ends_in_a_traceback(tmp_path, config, capsys, monkeypatch):
    def broken(*args, **kwargs):
        raise AttributeError("'list' object has no attribute 'get'")

    monkeypatch.setattr(shadow, "build_report", broken)
    code, out = _cli(capsys, config, "report")
    assert code == 1 and "unexpected AttributeError" in out and "Traceback" not in out


def test_the_report_splits_by_model_and_vocabulary_version_and_sums_type_counts(tmp_path, config, capsys):
    """#221: a vocabulary change is its own version; declared and inferred are labelled as unverified."""
    typed = _line(2)
    typed["vocab_sha256"] = "b" * 64
    typed["provenance"].update(model_sha256="a" * 64, types={"data_type": {"local": {"declared": 2}},
                                                             "format": {"withheld": {"inferred": 1}}},
                               declarations={"outputs": 4, "data_declared": 2, "format_declared": 1,
                                             "issues": {"unknown_key": 1}})
    _write(tmp_path, [_line(1), typed])
    code, out = _cli(capsys, config, "report", "--json", "--today", "2026-10-15")
    rep = json.loads(out)
    assert code == 0 and rep["versions"] == {"-/-": 1, f"{'a' * 12}/{'b' * 12}": 1}
    assert rep["types"] == {"data_type": {"local": {"declared": 2}}, "format": {"withheld": {"inferred": 1}}}
    assert rep["declarations"] == {"outputs": 4, "data_declared": 2, "format_declared": 1, "issues": {"unknown_key": 1}}
    code, out = _cli(capsys, config, "report", "--today", "2026-10-15")
    assert f"{'a' * 12}/{'b' * 12} 1" in out and "계획 산출 4 · data 선언 2 · format 선언 1" in out
    assert "내용 검증이 아니다" in out and "withheld inferred 1" in out
