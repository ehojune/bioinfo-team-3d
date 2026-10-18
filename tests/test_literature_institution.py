"""Institution session and PI-supplied PDF fallback for key papers (#745, #746)."""

import base64
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from labhq.output_checks import literature_access_failures, quality_lines
from labhq.models import ApprovalRequest
from labhq.runner.daemon import Runner
from labhq.runner.daemon import labhq_mcp_timeout_s
from labhq.settings import LiteratureSettings, Settings
from labhq.tools import annot_literature as lit
from tests.test_private_paths import REAL_LABHQ_ENTRIES
from tests.test_request_lifecycle import CaptureSocket, ROSTER, _runner_settings, _settings


PDF = b"%PDF-1.4\nBT (Full paper supplied by the PI.) Tj ET\n%%EOF"


class AbstractOnly:
    def __init__(self, workdir: Path):
        self.workdir = workdir

    async def fulltext(self, ids):
        rows = []
        for paper_id in ids:
            paper = lit.parse_paper_id(paper_id)
            text_file = f".tmp/literature/{paper.file_name()}"
            target = self.workdir / text_file
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("# Abstract only\n", encoding="utf-8")
            rows.append({"input": paper_id, "ok": True, "found": True, "access_level": "abstract",
                         "doi": paper.value if paper.kind == "doi" else "10.1000/key", "title": "Key paper",
                         "text_file": text_file})
        return {"results": rows}


class FakeLimiter:
    def __init__(self, refusal=None):
        self.refusal = refusal
        self.calls = 0

    async def acquire(self):
        self.calls += 1
        return self.refusal


class FakeInstitution:
    def __init__(self, status="success"):
        self.status = status
        self.calls = []

    async def fetch(self, doi, destination):
        self.calls.append((doi, destination))
        if self.status == "success":
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(PDF)
        return lit.InstitutionAttempt(self.status)


def paper():
    return {"id": "10.1000/key", "title": "Key paper", "why_key": "주요 결론의 직접 근거",
            "needed": "Results의 Table 2", "tried": ["Europe PMC", "Unpaywall"]}


def coordinator(tmp_path, settings, institution, approval, *,
                extract=lambda _path: ("Key paper DOI 10.1000/key. Full paper text", "complete")):
    workdir = tmp_path / "work"
    workdir.mkdir()
    return lit.KeyFulltextCoordinator(
        AbstractOnly(workdir), settings, workdir, tmp_path / "state", institution=institution,
        limiter=FakeLimiter(), request_pdf=approval, extract_pdf=extract,
    )


@pytest.mark.asyncio
async def test_disabled_setting_never_calls_the_institution_route(tmp_path):
    institution = FakeInstitution()

    async def timed_out(_summary, _detail, timeout_s):
        assert timeout_s == 7200
        return {"approved": False, "state": "timed_out"}

    flow = coordinator(tmp_path, LiteratureSettings(), institution, timed_out)
    row = (await flow.run([paper()]))["results"][0]

    assert institution.calls == []
    assert row["access_level"] == "abstract"
    assert row["pdf_request_status"] == "timed_out"


@pytest.mark.asyncio
async def test_fake_session_receives_only_the_requested_doi_and_keeps_pdf_under_tmp(tmp_path):
    profile = tmp_path / "institution-profile"
    profile.mkdir()
    settings = LiteratureSettings(institution_enabled=True, institution_profile=str(profile),
                                  institution_tdm_allowed=True, external_model_allowed=True)
    institution = FakeInstitution()

    async def no_card(*_args):
        raise AssertionError("institution success must not open a PDF request card")

    flow = coordinator(tmp_path, settings, institution, no_card)
    row = (await flow.run([paper()]))["results"][0]

    assert [doi for doi, _path in institution.calls] == ["10.1000/key"]
    pdf_path = institution.calls[0][1].resolve()
    assert pdf_path.is_relative_to((tmp_path / "work" / ".tmp" / "literature").resolve())
    assert row["access_level"] == "full_text" and row["read_source"] == "institution_session"
    assert (tmp_path / "work" / row["text_file"]).read_text(encoding="utf-8").endswith("Full paper text\n")


@pytest.mark.asyncio
async def test_one_card_expires_after_configured_two_hours_and_records_abstract_fallback(tmp_path):
    cards = []

    async def timed_out(summary, detail, timeout_s):
        cards.append((summary, detail, timeout_s))
        return {"approved": False, "state": "timed_out"}

    settings = LiteratureSettings(external_model_allowed=True)
    flow = coordinator(tmp_path, settings, FakeInstitution(), timed_out)
    row = (await flow.run([paper()]))["results"][0]

    assert len(cards) == 1 and cards[0][2] == 2 * 3600
    assert cards[0][1]["papers"][0]["needed"] == "Results의 Table 2"
    assert cards[0][1]["institution"]["tdm_allowed"] is False
    assert cards[0][1]["external_model_allowed"] is True
    assert row["fallback"] == "abstract_or_open_access_alternative"
    failures = literature_access_failures(tmp_path / "work")
    assert failures == {"DOI:10.1000/key": "pdf_request_timed_out"}
    assert quality_lines([{"id": "lit"}], {"lit": {"literature_access_failures": failures}}) == [
        "핵심 논문 PDF 요청이 설정된 대기 상한 뒤 만료되어 초록 또는 다른 OA 논문으로 진행함: DOI:10.1000/key"]
    ledger = [json.loads(line) for line in (tmp_path / "work" / "outputs/literature_access.jsonl")
              .read_text(encoding="utf-8").splitlines()]
    assert ledger[-1]["request_status"] == "timed_out" and ledger[-1]["access_level"] == "abstract"


@pytest.mark.asyncio
async def test_pi_pdf_in_the_named_inbox_is_read_and_the_body_flow_continues(tmp_path):
    async def provide(_summary, detail, timeout_s):
        assert timeout_s == 7200 and len(detail["papers"]) == 1
        supplied = tmp_path / "work" / detail["papers"][0]["pdf_file"]
        supplied.parent.mkdir(parents=True, exist_ok=True)
        supplied.write_bytes(PDF)
        return {"approved": True, "state": "resolved"}

    settings = LiteratureSettings(external_model_allowed=True)
    flow = coordinator(tmp_path, settings, FakeInstitution(), provide)
    row = (await flow.run([paper()]))["results"][0]

    assert row["access_level"] == "full_text"
    assert row["read_source"] == "pi_provided_pdf"
    assert row["pdf_request_status"] == "received"
    assert (tmp_path / "work" / row["pdf_file"]).read_bytes().startswith(b"%PDF-")
    assert (tmp_path / "work" / row["text_file"]).read_text(encoding="utf-8").endswith("Full paper text\n")


@pytest.mark.asyncio
async def test_a_pmid_without_a_doi_is_not_presented_as_a_doi(tmp_path):
    class NoDoi(AbstractOnly):
        async def fulltext(self, ids):
            result = await super().fulltext(ids)
            result["results"][0].pop("doi")
            return result

    seen = []

    async def card(_summary, detail, _timeout_s):
        seen.append(detail["papers"][0])
        return {"approved": False, "state": "timed_out"}

    workdir = tmp_path / "work"
    workdir.mkdir()
    flow = lit.KeyFulltextCoordinator(NoDoi(workdir), LiteratureSettings(external_model_allowed=True), workdir,
                                      tmp_path / "state", institution=FakeInstitution(), limiter=FakeLimiter(),
                                      request_pdf=card)
    await flow.run([{**paper(), "id": "PMID:12345"}])

    assert seen[0]["doi"] is None
    assert Path(seen[0]["pdf_file"]).name == "pmid-12345.pdf"


@pytest.mark.asyncio
async def test_wrong_or_unreadable_pdf_does_not_replace_the_abstract(tmp_path):
    async def provide(_summary, detail, _timeout_s):
        supplied = tmp_path / "work" / detail["papers"][0]["pdf_file"]
        supplied.parent.mkdir(parents=True, exist_ok=True)
        supplied.write_bytes(PDF)
        return {"approved": True, "state": "resolved"}

    settings = LiteratureSettings(external_model_allowed=True)
    flow = coordinator(tmp_path, settings, FakeInstitution(), provide,
                       extract=lambda _path: ("A different article DOI 10.9999/wrong", "complete"))
    row = (await flow.run([paper()]))["results"][0]

    assert row["access_level"] == "abstract"
    assert row["pdf_request_status"] == "received_invalid"
    assert row["access_failure"] == "pdf_identity_mismatch"


@pytest.mark.asyncio
async def test_partial_pdf_text_is_identified_and_kept_inside_tmp(tmp_path):
    async def provide(_summary, detail, _timeout_s):
        supplied = tmp_path / "work" / detail["papers"][0]["pdf_file"]
        supplied.parent.mkdir(parents=True, exist_ok=True)
        supplied.write_bytes(PDF)
        return {"approved": True, "state": "resolved"}

    settings = LiteratureSettings(external_model_allowed=True)
    flow = coordinator(tmp_path, settings, FakeInstitution(), provide,
                       extract=lambda _path: ("Key paper DOI 10.1000/key. One page only.", "partial_text"))
    row = (await flow.run([paper()]))["results"][0]

    assert row["access_level"] == "full_text" and row["extraction"] == "partial_text"
    assert row["pdf_sha256"] == __import__("hashlib").sha256(PDF).hexdigest()


@pytest.mark.asyncio
async def test_scanned_or_damaged_pdf_keeps_abstract_and_records_partial_extraction(tmp_path):
    async def provide(_summary, detail, _timeout_s):
        supplied = tmp_path / "work" / detail["papers"][0]["pdf_file"]
        supplied.parent.mkdir(parents=True, exist_ok=True)
        supplied.write_bytes(PDF)
        return {"approved": True, "state": "resolved"}

    flow = coordinator(tmp_path, LiteratureSettings(external_model_allowed=True), FakeInstitution(), provide,
                       extract=lambda _path: ("", "partial_text"))
    row = (await flow.run([paper()]))["results"][0]

    assert row["access_level"] == "abstract" and row["extraction"] == "partial_text"
    assert row["access_failure"] == "empty_pdf_text" and row["pdf_sha256"]


def test_institution_settings_require_an_explicit_safe_route():
    with pytest.raises(ValidationError, match="institution_profile or institution_proxy"):
        LiteratureSettings(institution_enabled=True)
    with pytest.raises(ValidationError, match="without credentials"):
        LiteratureSettings(institution_enabled=True, institution_proxy="https://user:secret@proxy.example/?url=")
    configured = LiteratureSettings(institution_enabled=True,
                                    institution_proxy="https://proxy.example/login?url={url}")
    assert configured.pdf_request_timeout_s == 7200 and configured.institution_daily_limit == 5


def test_custom_institution_profile_is_a_private_path_and_extends_the_mcp_wait(tmp_path):
    profile = tmp_path / "institution-profile"
    profile.mkdir()
    settings = Settings(literature=LiteratureSettings(institution_enabled=True,
                                                      institution_profile=str(profile),
                                                      pdf_request_timeout_s=8000))
    entries = REAL_LABHQ_ENTRIES(settings, str(tmp_path))
    assert str(profile) in {path for path, _label in entries}
    assert labhq_mcp_timeout_s(settings) == 8120


@pytest.mark.asyncio
async def test_institution_limiter_enforces_spacing_and_daily_cap_without_storing_dois(tmp_path):
    now = [100.0]
    waits = []

    async def sleep(seconds):
        waits.append(seconds)
        now[0] += seconds

    limiter = lit.InstitutionLimiter(tmp_path, 10, 2, clock=lambda: now[0], sleep=sleep)
    assert await limiter.acquire() is None
    assert await limiter.acquire() is None
    assert waits == [10.0]
    assert await limiter.acquire() == "daily_limit"
    assert b"10." not in (tmp_path / "institution-quota.sqlite3").read_bytes()


@pytest.mark.asyncio
async def test_pdf_card_upload_is_relayed_without_gateway_persistence(tmp_path):
    import httpx

    from labhq.gateway.server import create_app

    settings = _settings(tmp_path)
    app = create_app(settings)
    hub = app.state.hub
    socket = CaptureSocket()
    hub.register_runner("local", socket, ROSTER, "inc")
    request = ApprovalRequest(kind="literature_pdf", summary="PDF", task_id="t1", agent_id="worker",
                              detail={"papers": [{**paper(), "doi": "10.1000/key",
                                                   "pdf_file": ".tmp/literature/inbox/key.pdf"}]})
    await hub.on_runner_message("local", {"type": "approval.requested", "data": request.model_dump(mode="json")})
    headers = {"Authorization": f"Bearer {settings.gateway.client_token}", "Content-Type": "application/pdf"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gw") as client:
        bad = await client.post(f"/api/approvals/{request.id}/literature-pdf/0", headers=headers, content=b"not pdf")
        good = await client.post(f"/api/approvals/{request.id}/literature-pdf/0", headers=headers, content=PDF)

    assert bad.status_code == 415 and good.status_code == 200
    relayed = next(message for message in socket.sent if message.get("type") == "approval.attachment")
    assert relayed["id"] == request.id and relayed["paper_index"] == 0
    assert base64.b64decode(relayed["content_b64"]) == PDF
    state_files = [path for path in Path(settings.gateway.state_dir).rglob("*") if path.is_file()]
    assert request.id in hub.approvals and not any(b"Full paper" in path.read_bytes() for path in state_files)


@pytest.mark.asyncio
async def test_runner_writes_a_card_attachment_only_to_its_fixed_inbox_path(tmp_path):
    settings = _runner_settings(tmp_path)
    runner = Runner(settings)
    workdir = tmp_path / "workspace"
    workdir.mkdir()
    request = ApprovalRequest(id="appr_pdf", kind="literature_pdf", summary="PDF", task_id="t1",
                              agent_id="worker", detail={"papers": [{"pdf_file":
                                  ".tmp/literature/inbox/doi-key.pdf"}]})
    runner.broker.pending_requests[request.id] = request
    runner.approval_tasks[request.id] = ("t1", "worker")
    runner.workspaces["t1"] = SimpleNamespace(dir=workdir)
    try:
        ok, _note = await runner._store_approval_attachment({
            "id": request.id, "paper_index": 0, "content_b64": base64.b64encode(PDF).decode("ascii")})
        escaped, _note = await runner._store_approval_attachment({
            "id": request.id, "paper_index": 1, "content_b64": base64.b64encode(PDF).decode("ascii")})
        assert ok is True and escaped is False
        assert (workdir / ".tmp/literature/inbox/doi-key.pdf").read_bytes() == PDF
    finally:
        runner.store.close()
