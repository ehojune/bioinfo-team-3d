"""#670: no skill from a paper whose full text was not reached (it stays a planning reference), and a meta-analysis
skill also goes to the literature steps that extract its included studies."""
import pytest

from labhq.recruit.paper_skills import attachments, cited_access, prepare_plan, selections
from labhq.settings import Settings
from tests.test_paper_skill_attach import EVIDENCE, SELECTED

ROSTER = [{"id": "worker"}, {"id": "lit_scout"}]
STEPS = [{"id": "s1", "agent_id": "worker"}, {"id": "s_lit", "agent_id": "lit_scout"},
         {"id": "s_lit2", "agent_id": "lit_scout"}]


async def _convert(task):  # never reached before CP1: a call here is the failure
    raise AssertionError("no paper skill is built before CP1")


def _prepare(plan, precedents):
    return prepare_plan(plan, precedents, ROSTER, "r", Settings(), _convert)


def test_cited_access_takes_the_best_stated_level():
    assert cited_access(["PMID:1; access_level=abstract"]) == "abstract"
    assert cited_access(["PMID:1; access_level=abstract", "PMID:1 access_level=full_text"]) == "full_text"
    assert cited_access(["PMID:1"]) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("citations, seen", [(["PMID:40119824; access_level=abstract"], "초록만 확인했습니다"),
                                             (["PMID:40119824"], "본문 접근을 확인하지 못했습니다")])
async def test_a_paper_without_reached_full_text_makes_no_skill_and_stays_a_planning_reference(citations, seen):
    plan = {"steps": STEPS, "paper_skills": [{**SELECTED, "paper_id": "PMID:40119824", "title": "Abstract paper"}]}
    precedents = {"papers": [{"title": "Abstract paper", "citations": citations}]}
    out = await _prepare(plan, precedents)
    row = out["paper_skill_results"][0]
    assert row["status"] == "skipped" and not row.get("built")
    assert seen in row["skip_reason"] and "계획 참고로만" in row["skip_reason"]
    assert attachments(out, "s1", "worker", "r") == []
    assert any("논문 스킬 생략" in warning for warning in out["warnings"])


@pytest.mark.asyncio
async def test_a_partly_extracted_full_text_keeps_its_own_reason():
    plan = {"steps": STEPS, "paper_skills": [SELECTED]}
    partial = {**EVIDENCE, "extraction": "partial"}
    precedents = {"papers": [{"title": "Paired method", "citations": ["PMID:12345678; access_level=full_text"],
                              "fulltext_receipt": partial}]}
    row = (await _prepare(plan, precedents))["paper_skill_results"][0]
    assert row["status"] == "skipped" and "계획 참고로만" not in row["skip_reason"]


@pytest.mark.asyncio
async def test_a_full_text_meta_analysis_also_goes_to_the_literature_extraction_steps():
    plan = {"steps": STEPS, "paper_skills": [{**SELECTED, "title": "A meta-analysis of donor pairing"}]}
    precedents = {"papers": [{"title": "A meta-analysis of donor pairing", "citations": ["PMID:12345678; access_level=full_text"],
                              "fulltext_receipt": EVIDENCE}]}
    out = await _prepare(plan, precedents)
    row = out["paper_skill_results"][0]
    assert row["status"] == "deferred"
    assert row["step_ids"] == ["s1", "s_lit", "s_lit2"] and row["extraction_step_ids"] == ["s_lit", "s_lit2"]
    assert row["agent_ids"] == ["worker", "lit_scout"]
    assert [a["step_id"] for a in attachments(out, "s_lit", "lit_scout", "r")] == ["s_lit"]
    assert [a["step_id"] for a in attachments(out, "s1", "worker", "r")] == ["s1"]


def test_a_method_paper_is_not_extended_and_a_plan_without_literature_steps_gains_none():
    paper = {"title": "Paired method", "citations": ["PMID:12345678"]}
    plan = {"steps": STEPS, "paper_skills": [SELECTED]}
    chosen = selections(plan, {"papers": [paper]}, ROSTER)
    assert chosen[0]["step_ids"] == ["s1"] and "extraction_step_ids" not in chosen[0]
    meta = {"title": "Systematic review and meta-analysis", "citations": ["PMID:12345678"]}
    chosen = selections({"steps": [STEPS[0]], "paper_skills": [SELECTED]}, {"papers": [meta]}, ROSTER)
    assert chosen[0]["step_ids"] == ["s1"] and "extraction_step_ids" not in chosen[0]


# ---- #660: what a built skill folder holds, read from its files and never summarised ----
def _skill_folder(root, pid="pmid:12345678", digest="a" * 64, skill_md=None, figures=2, tables=1):
    import hashlib
    import json

    folder = root / ("paper-skill-" + hashlib.sha256(pid.encode()).hexdigest()[:24])
    skill = folder / "skill" / digest
    (skill / "references").mkdir(parents=True)
    (skill / "assets" / "figure").mkdir(parents=True)
    (skill / "assets" / "table").mkdir(parents=True)
    (skill / "SKILL.md").write_text(skill_md if skill_md is not None else
                                    '---\nname: demo-paper\ndescription: "Read and answer questions about Demo"\n---\n\n# Read this paper\n',
                                    encoding="utf-8")
    (skill / "references" / "paper.md").write_text("x" * 3000, encoding="utf-8")
    (skill / "references" / "supplement.md").write_text("s", encoding="utf-8")
    for i in range(figures):
        (skill / "assets" / "figure" / f"f{i}.jpg").write_bytes(b"j")
    for i in range(tables):
        (skill / "assets" / "table" / f"t{i}.csv").write_text("a,b", encoding="utf-8")
    (folder / "paper-skill.json").write_text(json.dumps({
        "paper_id": pid, "status": "ready", "skill_sha256": digest, "paper2agent_commit": "c" * 40,
        "skill_dir": "/private/skill", "runner_id": "r"}), encoding="utf-8")
    return folder


def test_skill_content_reads_the_name_the_paper_text_size_and_the_parts_without_summarising(tmp_path):
    from labhq.recruit.paper_skills import skill_content

    content = skill_content(_skill_folder(tmp_path), "a" * 64)
    assert content == {"name": "demo-paper", "description": "Read and answer questions about Demo",
                       "fulltext_bytes": 3000, "supplement": True, "figures": 2, "tables": 1,
                       "supp_figures": 0, "supp_tables": 0}


def test_skill_content_is_none_for_a_folder_that_is_not_this_receipts_plain_skill(tmp_path):
    import os

    from labhq.recruit.paper_skills import skill_content

    folder = _skill_folder(tmp_path)
    assert skill_content(folder, "b" * 64) is None                        # the hash does not name a skill here
    assert skill_content(folder, "not-a-hash") is None
    assert skill_content(tmp_path / "paper-skill-missing", "a" * 64) is None
    (folder / "skill" / ("a" * 64) / "SKILL.md").write_text("no front matter", encoding="utf-8")
    assert skill_content(folder, "a" * 64)["name"] is None                 # an odd file is not an error, just no name
    huge = _skill_folder(tmp_path, pid="pmid:7", skill_md="---\nname: " + "n" * 10_000_000 + "\n---\n")
    assert skill_content(huge, "a" * 64)["name"] is None or len(skill_content(huge, "a" * 64)["name"]) <= 80
    link = _skill_folder(tmp_path, pid="pmid:8")
    target = tmp_path / "elsewhere"
    target.mkdir()
    os.replace(link / "skill", tmp_path / "moved")
    try:
        os.symlink(tmp_path / "moved", link / "skill", target_is_directory=True)
    except (OSError, NotImplementedError):
        return  # no symlink right on this machine
    assert skill_content(link, "a" * 64) is None                          # a link on the way is never followed


def test_talent_line_shows_the_skill_facts_and_where_the_paper_text_lives_without_a_path(tmp_path):
    from types import SimpleNamespace

    from labhq.cli import talent_lines

    folder = _skill_folder(tmp_path, skill_md='---\nname: demo-paper\ndescription: "x"\n---\n')
    line, = talent_lines(SimpleNamespace(talent_pool=lambda: [], talent_dir=tmp_path))
    assert folder.name in line and "이름=demo-paper" in line and "논문 본문 파일 3 KB" in line
    assert "그림 2·표 1" in line and "보충자료 있음" in line and "기록 시각(파일) " in line
    assert "이 PC의 이 폴더에만 있고 요청 묶음에는 들어가지 않음" in line
    assert "요약" not in line and "/private/skill" not in line and "skill/" not in line


def test_talent_line_without_a_readable_skill_folder_is_the_old_line_plus_the_date(tmp_path):
    from types import SimpleNamespace

    import shutil

    from labhq.cli import talent_lines

    folder = _skill_folder(tmp_path)
    shutil.rmtree(folder / "skill")
    line, = talent_lines(SimpleNamespace(talent_pool=lambda: [], talent_dir=tmp_path))
    assert "논문 자문 스킬 생성됨" in line and "이름=" not in line and "논문 본문 파일" not in line
    assert line.rstrip().endswith(line.split("기록 시각(파일) ")[1]) and "기록 시각(파일) " in line


# ---- #660: which request first built a skill, from the request records ----
def test_skill_origins_take_the_earliest_request_that_built_it_and_never_a_reuse():
    from labhq.recruit.paper_skills import skill_origins

    ready = lambda **extra: {"paper_id": "PMID:12345678", "status": "ready", "skill_sha256": "a" * 64, **extra}
    origins = skill_origins([
        ("req_late", 200.0, [ready()]),
        ("req_first", 100.0, [ready()]),
        ("req_reuse", 50.0, [ready(status="reused")]),          # a reuse says an earlier request built it
        ("req_other_hash", 10.0, [ready(skill_sha256="b" * 64)]),
        ("req_bad", "soon", [ready()]), ("req_none", 5.0, None), ("req_row", 6.0, ["x", {"status": "ready"}]),
        ("req_flag", True, [ready()]),
    ])
    assert origins[("pmid:12345678", "a" * 64)] == {"request_id": "req_first", "created_at": 100.0}
    assert origins[("pmid:12345678", "b" * 64)]["request_id"] == "req_other_hash"
    assert len(origins) == 2


def _gateway_store(tmp_path, requests):
    import json
    import sqlite3

    path = tmp_path / "state" / "gateway.sqlite3"
    path.parent.mkdir(parents=True)
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE state (kind TEXT NOT NULL, key TEXT NOT NULL, body TEXT NOT NULL, PRIMARY KEY(kind, key))")
    for rid, body in requests.items():
        db.execute("INSERT INTO state VALUES ('request', ?, ?)", (rid, json.dumps(body)))
    db.commit()
    db.close()
    return path


def _settings(tmp_path):
    return Settings.model_validate({"gateway": {"state_dir": str(tmp_path / "state")}})


def test_the_cli_reads_origins_from_the_local_store_read_only_and_says_why_when_it_cannot(tmp_path):
    from labhq import cli

    rows = [{"paper_id": "pmid:12345678", "status": "ready", "skill_sha256": "a" * 64}]
    path = _gateway_store(tmp_path, {"req_one": {"created_at": 123.0, "plan": {"paper_skill_results": rows}},
                                     "req_none": {"created_at": 5.0, "plan": {}}})
    before = (path.stat().st_mtime_ns, path.stat().st_size)
    origins, error = cli._paper_skill_origins(_settings(tmp_path))
    assert error is None and origins == {("pmid:12345678", "a" * 64): {"request_id": "req_one", "created_at": 123.0}}
    assert (path.stat().st_mtime_ns, path.stat().st_size) == before          # nothing was written
    origins, error = cli._paper_skill_origins(_settings(tmp_path / "elsewhere"))
    assert origins is None and "gateway.sqlite3" in error                    # a missing store is not created
    assert not (tmp_path / "elsewhere").exists()
    path.write_bytes(b"not a database" * 100)
    origins, error = cli._paper_skill_origins(_settings(tmp_path))
    assert origins is None and "읽지 못했습니다" in error


def test_the_talent_line_names_the_building_request_or_why_it_falls_back_to_the_file_time(tmp_path):
    from types import SimpleNamespace

    from labhq.cli import talent_lines

    _skill_folder(tmp_path)
    reg = SimpleNamespace(talent_pool=lambda: [], talent_dir=tmp_path)
    built = {("pmid:12345678", "a" * 64): {"request_id": "req_one", "created_at": 86400.0 * 400}}
    assert "만든 요청 req_one · " in talent_lines(reg, built, None)[0] and "(요청 기록)" in talent_lines(reg, built, None)[0]
    line, = talent_lines(reg, {}, None)                                       # the store was read; nothing built it
    assert "기록 시각(파일) " in line and "(요청 기록에 없음)" in line and "만든 요청" not in line
    line, = talent_lines(reg, None, "gateway 기록 파일(gateway.sqlite3)이 없습니다")
    assert "(gateway 기록 파일(gateway.sqlite3)이 없습니다)" in line and "만든 요청" not in line
    line, = talent_lines(reg)                                                 # no origin lookup at all
    assert "기록 시각(파일) " in line and "요청 기록을 읽지 않음" in line


def test_skill_content_never_follows_a_linked_talent_folder_or_inner_directory(tmp_path):
    import os
    import shutil

    from labhq.recruit.paper_skills import skill_content

    digest = "a" * 64

    def linked(src, dst):
        try:
            os.symlink(src, dst, target_is_directory=True)
            return True
        except (OSError, NotImplementedError):
            pass
        if os.name != "nt":
            return False
        import subprocess  # a directory junction needs no privilege and is a link the reader must not follow either

        return subprocess.run(["cmd", "/c", "mklink", "/J", str(dst), str(src)], capture_output=True).returncode == 0

    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "paper.md").write_text("secret", encoding="utf-8")
    for inner in ("references", "assets"):
        folder = _skill_folder(tmp_path / inner)
        target = folder / "skill" / digest / inner
        shutil.rmtree(target)
        if not linked(outside, target):
            return  # no symlink right on this machine
        assert skill_content(folder, digest) is None, inner
    real = _skill_folder(tmp_path / "real", pid="pmid:9")
    if linked(real, tmp_path / "paper-skill-link"):
        assert skill_content(tmp_path / "paper-skill-link", digest) is None, "the talent folder itself is a link"


def test_a_directory_junction_is_seen_without_os_path_isjunction_as_on_python_310_and_311(tmp_path, monkeypatch):
    """Review: those versions have no os.path.isjunction, so a link check built on it never saw a junction."""
    import os
    import subprocess

    from labhq.recruit.paper_skills import _linked, skill_content

    if os.name != "nt":
        return  # a junction is a Windows reparse point
    monkeypatch.delattr(os.path, "isjunction", raising=False)
    target, link = tmp_path / "target", tmp_path / "link"
    target.mkdir()
    if subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True).returncode != 0:
        return  # no junction right on this machine
    assert _linked(link) is True and _linked(target) is False and _linked(tmp_path / "missing") is False
    folder = _skill_folder(tmp_path / "real")
    inner = folder / "skill" / ("a" * 64) / "references"
    import shutil

    shutil.rmtree(inner)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "paper.md").write_text("secret", encoding="utf-8")
    assert subprocess.run(["cmd", "/c", "mklink", "/J", str(inner), str(outside)], capture_output=True).returncode == 0
    assert skill_content(folder, "a" * 64) is None
