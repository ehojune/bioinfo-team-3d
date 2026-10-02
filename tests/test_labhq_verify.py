"""`labhq verify <request>` and the audit bundle (#58 ⑥): re-hash recorded outputs on the runner's PC."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import zipfile
from pathlib import Path

import httpx
import pytest

from labhq import cli
from labhq.evidence.audit import BUNDLE_FILES, NO_FILES_LINE
from labhq.models import AgentSpec, Engine, Task, TaskResult
from labhq.runner.daemon import Runner
from labhq.settings import Settings

BODY = b"gene\tlog2fc\nCD276\t2.5\n"


def _settings(tmp_path: Path) -> Settings:
    settings = Settings()
    for name in ("state_dir", "workspace_root", "agents_dir", "talent_dir"):
        setattr(settings.runner, name, str(tmp_path / name))
    return settings


async def _run(tmp_path: Path, monkeypatch, write, *, outputs: tuple[str, ...] = ("table.tsv",),
               hash_max_bytes: int | None = None) -> tuple[Settings, TaskResult]:
    """One step through the real runner with a fake CLI, so the record is what the daemon writes (#334)."""
    settings = _settings(tmp_path)
    if hash_max_bytes is not None:
        settings.runner.output_hash_max_bytes = hash_max_bytes
    runner = Runner(settings)
    agent = AgentSpec(id="analyst", name="Analyst", role="test", engine=Engine.claude_code, builtin_mcp=[])
    monkeypatch.setattr(runner, "_resolve_agent", lambda _task: agent)

    class FakeCli:
        async def run(self, ctx):
            if ctx.before_spawn:
                ctx.before_spawn()
            write(Path(ctx.workdir))
            return TaskResult(task_id=ctx.task.id, agent_id=agent.id, ok=True, text="done")

    monkeypatch.setattr("labhq.runner.daemon.get_adapter", lambda *_args: FakeCli())
    result = await runner.run_task(Task(id="task-v", request_id="req_v", agent_id="analyst", prompt="write",
                                        meta={"kind": "step", "step_id": "s1", "outputs": list(outputs)}))
    return settings, result


def _write(workdir: Path, relative: str, body: bytes) -> None:
    path = workdir / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)


def _verify(monkeypatch, capsys, settings: Settings, request: dict | Exception, *args: str) -> tuple[int, str]:
    def api(_s, method, path, **_kw):
        assert (method, path) == ("GET", "/api/requests/req_v")
        if isinstance(request, Exception):
            raise request
        return request

    monkeypatch.setattr(cli, "_api", api)
    monkeypatch.setattr(Settings, "load", classmethod(lambda cls, path=None: settings))
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["verify", "req_v", *args])
    return exit_info.value.code, capsys.readouterr().out


def _request(result: TaskResult, **extra) -> dict:
    return {"id": "req_v", "text": "CD276 DE", "status": "done", "outcome": None,
            "results": {"s1": result.model_dump(mode="json")}, **extra}


async def test_recorded_hash_matches_exit_0(tmp_path, monkeypatch, capsys):
    settings, result = await _run(tmp_path, monkeypatch, lambda wd: _write(wd, "outputs/table.tsv", BODY))
    assert result.output_sha256 == {"outputs/table.tsv": hashlib.sha256(BODY).hexdigest()}

    code, out = _verify(monkeypatch, capsys, settings, _request(result))
    assert code == 0, out
    assert "outputs/table.tsv" in out and "문제 없음 (exit 0)" in out


async def test_one_changed_byte_is_a_mismatch_exit_1(tmp_path, monkeypatch, capsys):
    settings, result = await _run(tmp_path, monkeypatch, lambda wd: _write(wd, "outputs/table.tsv", BODY))
    (Path(result.workdir) / "outputs" / "table.tsv").write_bytes(BODY[:-2] + b"6\n")

    code, out = _verify(monkeypatch, capsys, settings, _request(result), "--json")
    report = json.loads(out)
    assert code == 1 and report["exit_code"] == 1
    [row] = report["files"]
    assert row["status"] == "mismatch" and row["recorded_sha256"] == hashlib.sha256(BODY).hexdigest()
    assert row["sha256"] == hashlib.sha256(BODY[:-2] + b"6\n").hexdigest()
    assert row["agent_id"] == "analyst" and row["task_id"] == "task-v"


async def test_deleted_file_is_missing(tmp_path, monkeypatch, capsys):
    settings, result = await _run(tmp_path, monkeypatch, lambda wd: _write(wd, "outputs/table.tsv", BODY))
    (Path(result.workdir) / "outputs" / "table.tsv").unlink()

    code, out = _verify(monkeypatch, capsys, settings, _request(result), "--json")
    assert code == 1 and [row["status"] for row in json.loads(out)["files"]] == ["missing"]


async def test_deleted_output_without_a_recorded_hash_is_missing(tmp_path, monkeypatch, capsys):
    """An output labhq listed but could not hash (over output_hash_max_bytes, changed while hashed, a record from
    before #334) is still checked for presence: gone is missing, not unrecorded."""
    settings, result = await _run(tmp_path, monkeypatch, lambda wd: _write(wd, "outputs/table.tsv", BODY),
                                  hash_max_bytes=8)
    assert result.outputs == ["outputs/table.tsv"] and result.output_sha256 == {}

    code, out = _verify(monkeypatch, capsys, settings, _request(result), "--json")
    assert code == 0 and [row["status"] for row in json.loads(out)["files"]] == ["unrecorded"]

    (Path(result.workdir) / "outputs" / "table.tsv").unlink()
    code, out = _verify(monkeypatch, capsys, settings, _request(result), "--json")
    report = json.loads(out)
    assert code == 1 and [(row["path"], row["status"]) for row in report["files"]] == [("outputs/table.tsv", "missing")]


async def test_declared_folder_output_is_present_while_it_holds_a_file(tmp_path, monkeypatch, capsys):
    """The walker lists files only: a declared folder with a file under it is there; once removed it is missing."""
    settings, result = await _run(tmp_path, monkeypatch, lambda wd: _write(wd, "outputs/plots/volcano.png", BODY),
                                  outputs=("plots",))
    assert result.outputs == ["outputs/plots"]

    code, out = _verify(monkeypatch, capsys, settings, _request(result), "--json")
    assert code == 0, out
    assert [(row["path"], row["status"]) for row in json.loads(out)["files"]] == [("outputs/plots", "unrecorded")]

    shutil.rmtree(Path(result.workdir) / "outputs" / "plots")
    code, out = _verify(monkeypatch, capsys, settings, _request(result), "--json")
    assert code == 1 and [row["status"] for row in json.loads(out)["files"]] == ["missing"]


async def test_output_inside_a_restricted_zone_is_unchecked_not_missing(tmp_path, monkeypatch, capsys):
    """The walker never lists a restricted zone, so an output there cannot be shown absent."""
    from labhq.settings import DataZone

    settings, result = await _run(tmp_path, monkeypatch, lambda wd: _write(wd, "outputs/table.tsv", BODY))
    zone = Path(result.workdir) / "outputs" / "cohort"
    _write(zone, "rows.tsv", b"row\n")
    settings.policy.data_zones = [DataZone(path=str(zone))]
    recorded = result.model_copy(update={"outputs": [*result.outputs, "outputs/cohort/rows.tsv"]})

    code, out = _verify(monkeypatch, capsys, settings, _request(recorded), "--json")
    rows = {row["path"]: row for row in json.loads(out)["files"]}
    zoned = rows["outputs/cohort/rows.tsv"]
    assert rows["outputs/table.tsv"]["status"] == "ok"
    assert zoned["status"] == "unchecked" and "통제 구역" in zoned["detail"]
    assert code == 1


async def test_unreported_output_is_listed_as_a_warning(tmp_path, monkeypatch, capsys):
    def write(wd):
        _write(wd, "outputs/table.tsv", BODY)
        _write(wd, "outputs/scratch.tsv", b"x")

    settings, result = await _run(tmp_path, monkeypatch, write)
    code, out = _verify(monkeypatch, capsys, settings, _request(result))
    assert code == 0 and "보고하지 않은 산출(경고)" in out and "s1: outputs/scratch.tsv" in out


def _research(report: str, problems: list[str]) -> dict:
    ledger = {"claims": [{"id": "c1", "status": "supported"}],
              "evidence": [{"id": "e1", "status": "observed", "source": {"artifact_id": "a1"}}],
              "links": [{"claim_id": "c1", "evidence_id": "e1", "relation": "supports"}]}
    receipt = {"decision": "approved", "artifact_sha256": {"s1/a1": "a" * 64}, "refused_evidence": [],
               "unsupported_claims": []}
    return {"id": "req_v", "text": "research", "status": "done", "outcome": "research_reported", "report": report,
            "plan": {"steps": [{"id": "s1", "agent_id": "analyst"}]},
            "results": {"s1": {"task_id": "task-r", "agent_id": "analyst", "ok": True, "structured": ledger}},
            "research_contract": {"plan_sha256": "p" * 64, "checkpoints": {"cp2": receipt},
                                  "report_check": {"anchors": 1, "problems": problems}}}


def test_research_report_anchors_are_checked_again(tmp_path, monkeypatch, capsys):
    settings = _settings(tmp_path)
    code, out = _verify(monkeypatch, capsys, settings, _research("CD276 is up [[claim:s1/c1]].", []))
    assert code == 0, out

    broken = _research("CD276 is up [[claim:s1/c9]].", [])  # recorded as clean, but the anchor names no claim
    code, out = _verify(monkeypatch, capsys, settings, broken, "--json")
    report = json.loads(out)
    assert code == 1
    assert report["report_check"]["rerun"]["problems"] == ["[[claim:s1/c9]]: claim c9 is not in step s1's ledger"]
    assert report["report_check"]["same"] is False
    assert any(problem.startswith("보고서 앵커:") for problem in report["problems"])


async def test_bundle_holds_three_records_and_no_output_file(tmp_path, monkeypatch, capsys):
    secret = b"cohort-row-must-not-leave\n"
    settings, result = await _run(tmp_path, monkeypatch, lambda wd: _write(wd, "outputs/table.tsv", secret))
    out_zip = tmp_path / "audit.zip"

    code, out = _verify(monkeypatch, capsys, settings, _request(result), "--bundle", str(out_zip))
    assert code == 0 and str(out_zip) in out
    with zipfile.ZipFile(out_zip) as bundle:
        assert sorted(bundle.namelist()) == sorted(BUNDLE_FILES)
        contents = {name: bundle.read(name) for name in bundle.namelist()}
    assert all(secret.strip() not in body for body in contents.values())
    readme = contents["README.md"].decode("utf-8")
    assert NO_FILES_LINE in readme and "CD276 DE" in readme and "labhq 판본" in readme
    [artifact] = json.loads(contents["artifacts.json"])
    assert artifact["path"] == "outputs/table.tsv" and artifact["sha256"] == hashlib.sha256(secret).hexdigest()
    assert artifact["agent_id"] == "analyst" and artifact["task_id"] == "task-v" and artifact["size"] == len(secret)
    assert set(json.loads(contents["claims.json"])) >= {"ledgers", "artifact_sha256", "report_check"}


async def test_link_in_outputs_is_not_followed(tmp_path, monkeypatch, capsys):
    settings, result = await _run(tmp_path, monkeypatch, lambda wd: _write(wd, "outputs/table.tsv", BODY))
    target = Path(result.workdir) / "outputs" / "table.tsv"
    outside = tmp_path / "outside.tsv"
    outside.write_bytes(BODY)  # same bytes: following the link would make the hash match
    target.unlink()
    try:
        os.symlink(outside, target)
    except (OSError, NotImplementedError):
        pytest.skip("this account cannot create a file symlink")

    code, out = _verify(monkeypatch, capsys, settings, _request(result), "--json")
    [row] = json.loads(out)["files"]
    assert code == 1 and row["status"] == "unreadable" and row["sha256"] is None and "링크" in row["detail"]


def _folder_link(link: Path, target: Path) -> None:
    """A symlink, or on Windows a junction when this account cannot make symlinks; skip when neither works."""
    try:
        os.symlink(target, link, target_is_directory=True)
        return
    except (OSError, NotImplementedError):
        pass
    if os.name == "nt":
        made = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True)
        if made.returncode == 0:
            return
    pytest.skip("this account cannot create a folder link")


async def test_folder_link_in_outputs_is_not_followed(tmp_path, monkeypatch, capsys):
    settings, result = await _run(tmp_path, monkeypatch, lambda wd: _write(wd, "outputs/sub/table.tsv", BODY))
    outside = tmp_path / "outside"
    _write(outside, "table.tsv", BODY)
    shutil.rmtree(Path(result.workdir) / "outputs" / "sub")
    _folder_link(Path(result.workdir) / "outputs" / "sub", outside)
    recorded = result.model_copy(update={"outputs": ["outputs/sub/table.tsv"],
                                         "output_sha256": {"outputs/sub/table.tsv": hashlib.sha256(BODY).hexdigest()}})

    code, out = _verify(monkeypatch, capsys, settings, _request(recorded), "--json")
    [row] = json.loads(out)["files"]
    assert code == 1 and row["status"] == "missing" and row["sha256"] is None


async def test_workdir_not_on_this_pc_or_unknown_request_exit_2(tmp_path, monkeypatch, capsys):
    settings, result = await _run(tmp_path, monkeypatch, lambda wd: _write(wd, "outputs/table.tsv", BODY))
    elsewhere = _settings(tmp_path / "other-pc")
    bundle = tmp_path / "audit.zip"
    code, out = _verify(monkeypatch, capsys, elsewhere, _request(result), "--bundle", str(bundle))
    assert code == 2 and "작업 폴더" in out and not bundle.exists()

    missing = httpx.HTTPStatusError("404", request=httpx.Request("GET", "http://gw/api/requests/req_v"),
                                    response=httpx.Response(404))
    code, out = _verify(monkeypatch, capsys, settings, missing)
    assert code == 2 and "요청이 없습니다" in out


def test_recorded_unc_workdir_is_never_looked_up(tmp_path, monkeypatch):
    """A gateway record naming a UNC path (host share) must not reach the file system: an lstat there already opens
    SMB and sends NTLM, before any containment check could refuse it (PR #339 review)."""
    from labhq.evidence import audit

    looked: list[str] = []
    monkeypatch.setattr(audit, "_is_plain_dir", lambda path: looked.append(str(path)) or False)
    root = tmp_path / "workspace_root"
    (root / "2026-10-03").mkdir(parents=True)
    for recorded in (r"\attacker\share\x\task_v_analyst", "//attacker/share/task_v_analyst",
                     str(root / ".." / "elsewhere" / "task_v_analyst")):
        looked.clear()
        found, _reason = audit.locate_workdir(root, {"workdir_id": "task_v_analyst", "workdir": recorded})
        assert found is None
        assert all("attacker" not in path and "elsewhere" not in path for path in looked), looked


EMPTY_REPORT = {"request_id": "req_v", "files": [], "problems": [], "unreported_outputs": {}}

def test_bundle_never_writes_through_a_planted_partial_link(tmp_path):
    """A worker could plant `<bundle>.partial` as a link to a PI file; writing the bundle must not truncate it."""
    from labhq.evidence.audit import write_bundle

    victim = tmp_path / "victim.txt"
    victim.write_text("keep me", encoding="utf-8")
    out = tmp_path / "bundle.zip"
    try:
        os.symlink(victim, tmp_path / "bundle.zip.partial")
    except (OSError, NotImplementedError):
        pytest.skip("this account cannot create a file symlink")
    write_bundle(EMPTY_REPORT, {"id": "req_v"}, out)
    assert victim.read_text(encoding="utf-8") == "keep me"
    assert set(zipfile.ZipFile(out).namelist()) == set(BUNDLE_FILES)


def test_bundle_path_without_a_file_name_is_refused(tmp_path):
    from labhq.evidence.audit import write_bundle

    with pytest.raises(ValueError):
        write_bundle(EMPTY_REPORT, {"id": "req_v"}, tmp_path)
