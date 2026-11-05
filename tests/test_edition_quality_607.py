"""Edition quality (#607): sources, an untruncated editor input, one link line, and redraft."""
import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from labhq import cli, edition_sources
from labhq.gateway.server import Hub as RealHub, create_app
from labhq.result_storage import (EDITOR_OTHER_CHARS, EDITOR_REPORT_CHARS, document_links, draft_documents, editor_prompt,
                                  fit_record, readme_excerpt)
from labhq.settings import Settings
from tests.test_result_storage import Hub

# Shaped like the trial request's files (req_75b2fd4b0c): accession numbers and DOIs in prose, PMIDs listed with a
# slash and no second label, DOI/PubMed links in markdown, and a version table with a Source column.
REPORT = """# GSM3868436(SMC14-T) 분석 최종 보고

## 1. 결론과 권고
GSM3868436 종양 단일세포 자료를 GSE132465의 짝지은 10명과 GSE144735의 6쌍으로 비교했습니다.

## 선행 연구 기준
- **precedent.1 (했음)**: 범위는 GSE132465 paired 10명과 구분했습니다(GSE132465; Lee 2020, doi:10.1038/s41588-020-0636-z).
- **precedent.4 (했음)**: 4개 방법을 비교했습니다(doi:10.1038/s41467-020-19015-1).
- 외부 실측 bulk CRC 코호트를 쓸 수 있습니다(PMC10571664).
"""
APPENDIX = """## 부록: 실행 기록
- P3 · S9_bio_review: “Isella 2015 / Calon 2015 Nat Genet, PMID 25706628 / 25706627” 원본 링크를 추가하세요:
  Isella [PubMed](https://pubmed.ncbi.nlm.nih.gov/25706627/), [DOI](https://doi.org/10.1038/ng.3224);
  Calon [PubMed](https://pubmed.ncbi.nlm.nih.gov/25706628/), [DOI](https://doi.org/10.1038/ng.3225).
- 실패한 조회: http://127.0.0.1:8787/api/health 와 http://gateway.local/x 는 출처가 아닙니다.
"""
VERSIONS = """﻿# Resource versions and lookup record

| Resource | Version/release | Lookup scope/status | Source |
|---|---|---|---|
| Open Targets | release not verified | Per-gene links only. | https://platform.opentargets.org/ |
| MSigDB | 2026.1.Hs | Version identified from the Hallmark analysis. | https://docs.gsea-msigdb.org/MSigDB/Release_Notes/MSigDB_2026.1.Hs/ |

- PubMed search returned records (PMID: 35151227; DOI: 10.1016/j.gendis.2021.12.008). Sources: https://pubmed.ncbi.nlm.nih.gov/35151227/
"""


def sources_of(report, appendix="", versions=None, **extra):
    hub = Hub(Path("unused"))
    req = {"id": "req_test", "text": "비교", "report": report, "report_appendix": appendix, **extra}
    assets = {"steps/S8_lookup/outputs/lookup/resource_versions.md": versions} if versions else None
    return draft_documents(req, hub.s, assets=assets)["SOURCES.md"]


def test_sources_list_every_identifier_of_a_trial_shaped_report():
    text = sources_of(REPORT, APPENDIX, VERSIONS)
    for expected in (
            "[GSE132465](https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE132465)",
            "[GSE144735](https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE144735)",
            "[GSM3868436](https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSM3868436)",
            "[PMID 25706627](https://pubmed.ncbi.nlm.nih.gov/25706627/)",
            "[PMID 25706628](https://pubmed.ncbi.nlm.nih.gov/25706628/)",
            "[PMID 35151227](https://pubmed.ncbi.nlm.nih.gov/35151227/)",
            "[10.1038/ng.3224](https://doi.org/10.1038/ng.3224)",
            "[10.1038/ng.3225](https://doi.org/10.1038/ng.3225)",
            "[10.1038/s41588-020-0636-z](https://doi.org/10.1038/s41588-020-0636-z)",
            "[10.1016/j.gendis.2021.12.008](https://doi.org/10.1016/j.gendis.2021.12.008)",
            "[PMC10571664](https://pmc.ncbi.nlm.nih.gov/articles/PMC10571664/)",
            "- https://platform.opentargets.org/",
            "- MSigDB: 2026.1.Hs (https://docs.gsea-msigdb.org/MSigDB/Release_Notes/MSigDB_2026.1.Hs/)",
            "- Open Targets: release not verified (https://platform.opentargets.org/)"):
        assert expected in text, expected
    assert "출처를 찾지 못했습니다" not in text
    for title in ("## 논문 DOI", "## PubMed", "## GEO 데이터", "## 웹 주소", "## 소프트웨어·자료 버전"):
        assert title in text
    assert text.index("## 논문 DOI") < text.index("## PubMed") < text.index("## GEO 데이터") < text.index("## 웹 주소")


def test_sources_are_deduplicated_and_skip_private_links():
    text = sources_of(REPORT, APPENDIX, VERSIONS)
    assert text.count("acc=GSE132465)") == 1  # named five times in the report, listed once
    assert text.count("[10.1038/ng.3224]") == 1  # a bare DOI and a doi.org link are the same source
    assert text.count("[PMID 25706627]") == 1 and text.count("[10.1016/j.gendis.2021.12.008]") == 1
    assert "127.0.0.1" not in text and "gateway.local" not in text  # a local service is not a source
    web = text.split("## 웹 주소")[1].split("##")[0]
    assert "doi.org" not in web and "pubmed.ncbi.nlm.nih.gov/25706627" not in web


def test_sources_cover_sequencing_records_and_attached_references():
    report = ("Reads SRR1234567 from SRP123456 and SRX1234567, array E-MTAB-5678, project PRJNA400000 and PRJEB12345. "
              "See https://example.org/paper.html?token=abc123&page=2.")
    text = sources_of(report, references=[{"kind": "doi", "value": "10.1234/example"},
                                          {"kind": "pmid", "value": "123456"},
                                          {"kind": "github", "value": "https://github.com/owner/repo"}])
    for expected in ("SRR1234567", "SRP123456", "SRX1234567", "E-MTAB-5678", "PRJNA400000", "PRJEB12345",
                     "https://doi.org/10.1234/example", "[PMID 123456]", "- https://github.com/owner/repo",
                     "- https://example.org/paper.html"):
        assert expected in text, expected
    assert "abc123" not in text  # a query that names a token is dropped
    assert "www.ebi.ac.uk/ena/browser/view/PRJEB12345" in text


def test_sources_say_none_found_only_when_nothing_is_found():
    assert "출처를 찾지 못했습니다" in sources_of("특별한 인용이 없는 보고서입니다.")
    assert "출처를 찾지 못했습니다" not in sources_of("자료는 GSE1234 입니다.")
    assert "출처를 찾지 못했습니다" not in sources_of("본문\n\n## References\nSmith (2020). Example study.\n")


def test_resource_versions_of_the_bundle_reach_sources_from_the_edition_assets():
    text = sources_of("보고서", versions=VERSIONS)
    assert "- MSigDB: 2026.1.Hs" in text and "[PMID 35151227]" in text
    assert "- Resource: Version" not in text  # the table header is not a row


PRIVATE_VERSIONS = """| Resource | Version | Source |
|---|---|---|
| Local service | 1.0 | http://127.0.0.1:8787/api/version |
| Gateway | 2.0 | http://gateway.local/v |
| Internal | 3.0 | https://host.internal/v |
| Lab LAN | 4.0 | http://192.168.1.5/v |
| Dev box | 5.0 | http://localhost:8000/v |
| Ten net | 6.0 | http://10.0.0.5/v |
| Signed | 7.0 | https://data.example.org/v?token=abc123&page=2 |
| Public | 8.0 | https://example.org/release |
| Note | see http://127.0.0.1:9/x | |
"""


def test_version_sources_get_the_same_privacy_filter_as_every_other_link():
    text = sources_of("보고서", versions=PRIVATE_VERSIONS)
    for private in ("127.0.0.1", "gateway.local", "host.internal", "192.168", "localhost", "10.0.0.5", "abc123"):
        assert private not in text, private  # a public edition never names an internal service or a credential
    for row in ("- Local service: 1.0\n", "- Gateway: 2.0\n", "- Internal: 3.0\n", "- Lab LAN: 4.0\n",
                "- Dev box: 5.0\n", "- Ten net: 6.0\n"):
        assert row in text, row  # the version stays; only the private address goes
    assert "- Signed: 7.0 (https://data.example.org/v)\n" in text  # the credential query is dropped, the page kept
    assert "- Public: 8.0 (https://example.org/release)\n" in text
    assert "- Note: see (비공개 주소 생략)\n" in text  # a private link inside a cell is masked too


def test_a_doi_in_a_link_is_one_doi_without_the_links_query():
    report = ("See https://doi.org/10.1234/example?utm_source=x and doi.org/10.5678/other?token=abc123 and "
              "https://publisher.example.org/article/10.4321/xyz?session=9 and a bare 10.9999/plain.")
    assert edition_sources.collect([report]).listing("doi") == [
        "10.1234/example", "10.4321/xyz", "10.5678/other", "10.9999/plain"]
    text = sources_of(report)
    assert text.count("[10.1234/example](https://doi.org/10.1234/example)") == 1
    for query in ("utm_source", "abc123", "session=9", "token="):
        assert query not in text, query


def long_report(parts=40, size=2500):
    sections = [f"## 절 {i}\n" + (f"절 {i}의 내용입니다. " * (size // 14)) for i in range(parts)]
    return "# 긴 보고서\n\n" + "\n\n".join(sections) + "\n\n## 마지막 절\nEND-OF-REPORT-MARKER 마지막 결론입니다."


def editor_hub(tmp_path, report, **extra):
    settings = Settings()
    settings.gateway.state_dir = str(tmp_path / "state")
    settings.runner.workspace_root = str(tmp_path / "runs")
    hub = RealHub(settings)
    hub.requests["req_test"] = {"id": "req_test", "status": "done", "text": "비교", "report": report,
                                "result_storage": {"mode": "local", "status": "selected"}, **extra}
    hub.agents["editor"] = {}
    return hub


def readme_of(hub):
    return Path(hub.requests["req_test"]["result_storage"]["draft_path"], "README.md").read_text(encoding="utf-8")


async def test_editor_gets_the_whole_report_and_no_cut_off_wording(tmp_path, monkeypatch):
    report = long_report(parts=8)  # about 20k characters: well past the old 1800
    assert len(report) > 15_000
    hub = editor_hub(tmp_path, report)
    editor = AsyncMock(return_value=SimpleNamespace(ok=True, structured={"readme": "# 정리\n결과를 정리했습니다."}))
    monkeypatch.setattr(hub.orchestrator, "run_step", editor)
    await hub.result_storage.finish("req_test")
    prompt = editor.call_args.args[0].prompt
    assert "END-OF-REPORT-MARKER" in prompt and "절 7의 내용입니다." in prompt
    # the report goes in whole, not as the README draft's first 1800 characters (the edition adds its abstract figure
    # under the title, so the text after the title is compared)
    assert report.strip().split("\n\n", 1)[1] in prompt
    assert "끊" not in prompt and "잘리" not in prompt and "전체를 그대로 담았습니다" in prompt
    assert "(전문)" in prompt and "[개요]" not in prompt


def test_a_report_over_the_bound_is_an_outline_plus_whole_sections_and_names_what_is_left():
    report = long_report(parts=60, size=4000)
    assert len(report) > EDITOR_REPORT_CHARS
    prompt = editor_prompt({"report.md": report, "PROGRESS.md": "p", "REPRODUCE.md": "r", "SOURCES.md": "s"}, "요청")
    assert "[개요]" in prompt and "- 절 59" in prompt  # every heading is in the outline
    assert "절 0의 내용입니다." in prompt  # the first sections are whole
    assert "입력 크기 때문에" in prompt and "report.md의 절 55" in prompt  # the cut sections are named
    assert "불완전하다고 쓰지 말고" in prompt and "끊" not in prompt
    assert len(prompt) < EDITOR_REPORT_CHARS + 20_000
    whole, omitted = fit_record("짧은 글", 100)
    assert (whole, omitted) == ("짧은 글", [])


def test_fit_record_keeps_five_thousand_sections_inside_the_bound():
    record = "".join(f"## 절 {number}\n내용 {number}\n" for number in range(5000))
    body, omitted = fit_record(record, EDITOR_OTHER_CHARS)
    assert len(body) + len("; ".join(omitted)) <= EDITOR_OTHER_CHARS
    assert omitted[-1] == "절 4999" and any("개 절" in title for title in omitted)


async def test_the_readme_never_claims_a_cut_because_of_our_own_truncation(tmp_path, monkeypatch):
    report = long_report(parts=8)
    hub = editor_hub(tmp_path, report)  # no editor reply: the deterministic README
    monkeypatch.setattr(hub.orchestrator, "run_step", AsyncMock(side_effect=RuntimeError("down")))
    await hub.result_storage.finish("req_test")
    readme = readme_of(hub)
    assert "끊겨" not in readme and "끊겼" not in readme
    assert readme.count("](report.md)") == 1 and "이어지는 내용은 report.md에 있습니다" in readme
    excerpt = readme_excerpt(report).split("\n\n…(이어지는")[0]
    assert report.startswith(excerpt) and excerpt.endswith("입니다.")  # a long paragraph is cut at a sentence end
    paragraphs = "문단 하나입니다.\n\n" * 400
    excerpt = readme_excerpt(paragraphs).split("\n\n…(이어지는")[0]
    assert excerpt.endswith("문단 하나입니다.") and paragraphs.startswith(excerpt + "\n\n")  # else between paragraphs


async def test_the_readme_has_one_link_line_whatever_the_editor_wrote(tmp_path, monkeypatch):
    own = "# 정리\n본문\n\n자세한 기록\n[최종 보고](report.md) · [진행 기록](PROGRESS.md) · [재현 방법](REPRODUCE.md) · [출처](SOURCES.md)"
    cases = ((own, ""), ("# 정리\n[결과](report.md)만 링크", "[진행 기록](PROGRESS.md)"),
             ("# 정리\n링크 없음", "[전체 결과·그림·표](report.md)"))
    for number, (readme, expected_new) in enumerate(cases):
        hub = editor_hub(tmp_path / f"case{number}", REPORT)
        monkeypatch.setattr(hub.orchestrator, "run_step",
                            AsyncMock(return_value=SimpleNamespace(ok=True, structured={"readme": readme})))
        await hub.result_storage.finish("req_test")
        text = readme_of(hub)
        for name in ("report.md", "PROGRESS.md", "REPRODUCE.md", "SOURCES.md"):
            assert text.count(f"]({name})") == 1, (name, text)
        assert expected_new in text
    assert document_links("[a](report.md) [b](./PROGRESS.md) [c](REPRODUCE.md#x) [d](SOURCES.md)") == ""


async def test_the_edition_folder_is_rebuilt_without_leftovers(tmp_path, monkeypatch):
    hub = editor_hub(tmp_path, REPORT)
    monkeypatch.setattr(hub.orchestrator, "run_step",
                        AsyncMock(return_value=SimpleNamespace(ok=True, structured={"readme": "# 첫 번째"})))
    await hub.result_storage.finish("req_test")
    draft = Path(hub.requests["req_test"]["result_storage"]["draft_path"])
    (draft / "old-only.txt").write_text("leftover", encoding="utf-8")
    (draft / "steps" / "x").mkdir(parents=True)
    (draft / "steps" / "x" / "gone.csv").write_text("a,b", encoding="utf-8")
    hub.requests["req_test"]["result_storage"] = {"mode": "local", "status": "selected"}
    await hub.result_storage.finish("req_test")
    assert not (draft / "old-only.txt").exists() and not (draft / "steps").exists()
    assert (draft / "README.md").is_file() and (draft / "SOURCES.md").is_file()


# ----- redraft -----

def redraft_hub(tmp_path, monkeypatch, replies, app=False):
    settings = Settings()
    settings.gateway.state_dir = str(tmp_path / "state")
    settings.runner.workspace_root = str(tmp_path / "runs")
    settings.gateway.client_token = "test-token"
    if app:
        app = create_app(settings)
    hub = app.state.hub if app else RealHub(settings)
    hub.requests["req_test"] = {"id": "req_test", "status": "done", "text": "비교", "report": REPORT,
                                "pi_reviewed_at": 1, "result_storage": {"mode": "existing", "repo": "owner/study",
                                                                          "status": "selected"}}
    hub.agents["editor"] = {}
    editor = AsyncMock(side_effect=[SimpleNamespace(ok=True, structured={"readme": text}) for text in replies])
    monkeypatch.setattr(hub.orchestrator, "run_step", editor)
    upload = AsyncMock()
    monkeypatch.setattr(hub.result_storage, "upload", upload)
    return (hub, editor, upload, app) if app else (hub, editor, upload)


async def publish_card(hub, not_id=None):
    for _ in range(300):
        for aid, entry in hub.approvals.items():
            if entry["approval"]["kind"] == "result_publish" and aid != not_id:
                return entry["approval"]
        await asyncio.sleep(0.02)
    raise AssertionError("no publish card")


async def test_redraft_reruns_the_editor_and_replaces_the_pending_publish_card(tmp_path, monkeypatch):
    hub, editor, upload = redraft_hub(tmp_path, monkeypatch, ["# 첫 정리본", "# 두 번째 정리본"])
    hub.result_storage.schedule("req_test")
    first = await publish_card(hub)
    assert "첫 정리본" in first["detail"]["documents"]["README.md"]
    draft = Path(hub.requests["req_test"]["result_storage"]["draft_path"])
    (draft / "old-only.txt").write_text("leftover", encoding="utf-8")

    reply = await hub.result_storage.redraft("req_test")
    assert reply == {"request_id": "req_test", "status": "redrafting", "closed_cards": [first["id"]]}
    second = await publish_card(hub, not_id=first["id"])
    assert "두 번째 정리본" in second["detail"]["documents"]["README.md"]
    assert first["id"] not in hub.approvals and hub.store.get("approval_decision", first["id"])["state"] == "expired"
    assert [a for a in hub.approvals.values() if a["approval"]["kind"] == "result_publish"] == [hub.approvals[second["id"]]]
    assert hub.requests["req_test"]["result_storage"]["status"] == "draft"
    assert editor.await_count == 2 and not (draft / "old-only.txt").exists()
    upload.assert_not_awaited()

    await hub.resolve_approval(second["id"], True)
    await hub.result_storage.tasks["req_test"]
    assert hub.requests["req_test"]["result_storage"]["status"] == "published"
    assert upload.await_args.args[1]["README.md"].startswith("# 두 번째 정리본")
    await hub.result_storage.close()


async def test_redraft_after_the_pi_declined(tmp_path, monkeypatch):
    hub, editor, upload = redraft_hub(tmp_path, monkeypatch, ["# 거절된 정리본", "# 다시 만든 정리본"])
    hub.result_storage.schedule("req_test")
    first = await publish_card(hub)
    await hub.resolve_approval(first["id"], False)
    await hub.result_storage.tasks["req_test"]
    assert hub.requests["req_test"]["result_storage"]["status"] == "declined"
    reply = await hub.result_storage.redraft("req_test")
    assert reply["closed_cards"] == []
    second = await publish_card(hub)
    assert "다시 만든 정리본" in second["detail"]["documents"]["README.md"]
    assert hub.requests["req_test"]["result_storage"]["status"] == "draft"
    upload.assert_not_awaited()
    await hub.result_storage.close()


async def test_redraft_refuses_published_and_not_yet_drafted_editions(tmp_path, monkeypatch):
    hub, editor, _ = redraft_hub(tmp_path, monkeypatch, [])
    service, saved = hub.result_storage, hub.requests["req_test"]["result_storage"]
    saved["status"] = "published"
    with pytest.raises(ValueError, match="이미 게시한 정리본은 다시 만들 수 없습니다"):
        await service.redraft("req_test")
    for status in ("selected", "local"):  # `failed` is allowed since the publish retry (#607); tests/test_publish_retry_607.py
        saved["status"] = status
        with pytest.raises(ValueError, match="다시 만들 정리본이 없습니다"):
            await service.redraft("req_test")
    saved["status"] = "declined"
    hub.requests["req_test"]["status"] = "running"
    with pytest.raises(ValueError, match="끝난 요청만"):
        await service.redraft("req_test")
    assert editor.await_count == 0 and saved["status"] == "declined"


async def test_redraft_api_and_cli(tmp_path, monkeypatch, capsys):
    hub, editor, _, app = redraft_hub(tmp_path, monkeypatch, ["# 새 정리본"], app=True)
    hub.requests["req_test"]["result_storage"]["status"] = "declined"
    hub.requests["req_pub"] = {**hub.requests["req_test"], "id": "req_pub",
                               "result_storage": {"mode": "existing", "repo": "o/r", "status": "published"}}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
        assert (await client.post("/api/requests/req_test/storage/redraft")).status_code == 401
        client.headers["Authorization"] = "Bearer test-token"
        assert (await client.post("/api/requests/missing/storage/redraft")).status_code == 404
        refused = await client.post("/api/requests/req_pub/storage/redraft")
        assert refused.status_code == 409 and "이미 게시한 정리본" in refused.json()["detail"]
        ok = await client.post("/api/requests/req_test/storage/redraft")
        assert ok.status_code == 200 and ok.json()["status"] == "redrafting"
    card = await publish_card(hub)
    assert "새 정리본" in card["detail"]["documents"]["README.md"]
    await hub.result_storage.close()

    calls = []
    monkeypatch.setattr(cli, "_api", lambda s, method, path, **kw: calls.append((method, path)) or
                        {"request_id": "req_x", "status": "redrafting", "closed_cards": ["a1"]})
    monkeypatch.setattr(Settings, "load", classmethod(lambda cls, path=None: hub.s))
    cli.main(["storage", "req_x", "--redraft"])
    assert calls == [("POST", "/api/requests/req_x/storage/redraft")]
    assert "편집자를 다시 돌립니다 (이전 게시 카드 1장 닫음)" in capsys.readouterr().out


# ----- round 3: one host gate, balanced DOI parentheses, short labelled PMIDs, publish vs redraft -----

PRIVATE_LINKS = [
    "http://127.0.0.1\\example.org/secret", "http://gateway.local\\example.org/x",
    "http://127.0.0.1\\\texample.org/x", "http://gateway.local \\\nexample.org/x",
    "http://localhost.\\\rexample.org/x", "http://10.0.0.1\\\x00example.org/x",
    "http://127.0.0.1/x", "http://127.0.0.1./x", "http://127.0.0.1.../x", "http://gateway.local./secret",
    "http://gateway.local/x", "https://host.internal./v", "http://LOCALHOST/x", "http://localhost./x",
    "http://x.localhost/x", "http://nas.lan/x", "http://router.home.arpa./x", "http://home.arpa/x",
    "http://intranet/x", "http://intranet./x",  # a single-label name
    "http://[::1]/x", "http://[::ffff:127.0.0.1]/x", "http://[::ffff:7f00:1]/x", "http://[fe80::1]/x",
    "http://[fc00::1]/x", "http://[::]/x", "http://[64:ff9b::7f00:1]/x", "http://[2002:7f00:1::]/x",
    "http://[fe80::1%25eth0]/x", "http://[::ffff:10.0.0.1]/x",  # IPv6, IPv4-mapped, NAT64, 6to4, zone id
    "http://2130706433/x", "http://0x7f.1/x", "http://0177.0.0.1/x", "http://127.1/x", "http://017700000001/x",
    "http://0x7f000001/x", "http://%31%32%37.0.0.1/x",  # decimal, hex, octal and short IPv4 forms, percent-encoded
    "http://10.0.0.5/", "http://172.16.0.1/", "http://192.168.1.5/", "http://169.254.169.254/", "http://0.0.0.0/",
    "http://240.0.0.1/", "http://100.64.0.1/", "http://224.0.0.1/",  # private, link-local, unspecified, reserved, shared, multicast
    "http://a@127.0.0.1/x", "http://user:pw@127.0.0.1/x", "ftp://example.org/x", "http:///x"]
PUBLIC_LINKS = {
    "https://example.org/a": "https://example.org/a",
    "https://example.org\\public/path": "https://example.org/public/path",
    "https://exa\tmple.org/x": "https://example.org/x",
    "http://8.8.8.8/x": "http://8.8.8.8/x",
    "http://134744072/x": "http://8.8.8.8/x",  # the same address in decimal
    "https://[2606:4700:4700::1111]/x": "https://[2606:4700:4700::1111]/x",
    "https://example.org./x": "https://example.org/x",  # the root dot goes
    "https://10.example.org/x": "https://10.example.org/x",  # digits first is still a name
    "https://user:pass@example.org/x": "https://example.org/x",  # userinfo goes
    "https://example.org/v?token=abc&p=1": "https://example.org/v",  # a credential query goes
    "https://example.org:8443/x": "https://example.org:8443/x",
    "https://EXAMPLE.org/Path#frag": "https://example.org/Path",
    "https://bücher.example/x": "https://xn--bcher-kva.example/x",  # IDNA
    "https://en.wikipedia.org/wiki/Foo_(bar)": "https://en.wikipedia.org/wiki/Foo_(bar)"}


@pytest.mark.parametrize("url", PRIVATE_LINKS)
def test_the_host_gate_drops_every_private_spelling(url):
    assert edition_sources.public_link(url) is None


@pytest.mark.parametrize("url, expected", PUBLIC_LINKS.items())
def test_the_host_gate_keeps_public_hosts_in_canonical_form(url, expected):
    assert edition_sources.public_link(url) == expected


def test_no_private_spelling_reaches_sources_by_any_route():
    report = " ".join(PRIVATE_LINKS + ["https://example.org/kept"])
    versions = "| Resource | Version | Source |\n|---|---|---|\n" + "".join(
        f"| Tool{number} | {number}.0 | {url} |\n" for number, url in enumerate(PRIVATE_LINKS))
    text = sources_of(report, versions=versions)
    for marker in ("127.0.0.1", "gateway.local", "host.internal", "localhost", "nas.lan", "home.arpa", "intranet",
                   "::1", "169.254", "192.168", "10.0.0.5", "172.16", "2130706433", "0x7f", "0177", "user:pw", "ftp://"):
        assert marker not in text, marker
    assert "- https://example.org/kept" in text and "- Tool3: 3.0\n" in text  # the public link and the version stay


def test_balanced_parentheses_stay_in_a_doi_and_a_closing_one_ends_it():
    doi = "10.1016/S0016-5085(98)70201-6"
    report = (f"bare {doi} here, and in prose ({doi}). Also (doi:10.1038/ng.3224), and "
              f"[link](https://doi.org/{doi}) and https://doi.org/{doi.replace('(', '%28').replace(')', '%29')} "
              "and https://en.wikipedia.org/wiki/Foo_(bar).")
    found = edition_sources.collect([report])
    assert found.listing("doi") == [doi, "10.1038/ng.3224"]  # one entry for each, never "10.1016/S0016-5085(98"
    assert found.listing("web") == ["https://en.wikipedia.org/wiki/Foo_(bar)"]
    assert "(https://doi.org/10.1016/S0016-5085%2898%2970201-6)" in sources_of(report)


def test_a_labelled_pmid_may_be_short_but_an_unlabelled_number_stays_strict():
    found = edition_sources.collect(["Legacy record PMID: 87; also PMID 12 / 34 and PMIDs 25706628, 25706627. "
                                     "See 99 and 1234567890 (PMID 1234567890)."])
    assert found.listing("pmid") == ["12", "87", "25706627", "25706628"]  # 34 follows only a separator; 99 has no label
    assert "[PMID 87](https://pubmed.ncbi.nlm.nih.gov/87/)" in sources_of("PMID: 87")
    assert "출처를 찾지 못했습니다" not in sources_of("PMID: 87")


async def test_redraft_is_refused_once_the_upload_started(tmp_path, monkeypatch):
    hub, editor, _ = redraft_hub(tmp_path, monkeypatch, ["# 첫 정리본"])
    started, release = asyncio.Event(), asyncio.Event()

    async def mid_upload(rid, docs, images=None, pdf=None):
        started.set()
        await release.wait()

    monkeypatch.setattr(hub.result_storage, "upload", mid_upload)
    hub.result_storage.schedule("req_test")
    card = await publish_card(hub)
    await hub.resolve_approval(card["id"], True)
    with pytest.raises(ValueError, match="게시가 시작됐습니다"):  # approved, the task has not reached the upload yet
        await hub.result_storage.redraft("req_test")
    await asyncio.wait_for(started.wait(), 5)
    task = hub.result_storage.tasks["req_test"]
    with pytest.raises(ValueError, match="게시가 시작됐습니다"):  # files are going to the repository
        await hub.result_storage.redraft("req_test")
    assert not task.done() and editor.await_count == 1  # nothing was cancelled or rebuilt
    assert hub.requests["req_test"]["result_storage"]["status"] == "draft"
    release.set()
    await task
    assert hub.requests["req_test"]["result_storage"]["status"] == "published"
    await hub.result_storage.close()


async def test_a_publish_answer_waits_for_a_redraft_that_holds_the_lock(tmp_path, monkeypatch):
    hub, editor, upload = redraft_hub(tmp_path, monkeypatch, ["# 첫 정리본"])
    hub.result_storage.schedule("req_test")
    card = await publish_card(hub)
    async with hub.result_storage.card_lock:  # a redraft is in progress
        answer = asyncio.create_task(hub.resolve_approval(card["id"], True))
        await asyncio.sleep(0.05)
        assert not answer.done() and card["id"] in hub.approvals  # the answer did not slip in before the redraft
        await hub.close_approval(card["id"], "정리본을 다시 만들어 이 게시 카드를 닫았습니다")  # what the redraft does
    with pytest.raises(KeyError):  # the replaced card can no longer be answered
        await answer
    upload.assert_not_awaited()
    await hub.result_storage.close()
