"""#1279: a host that keeps answering HTTP 5xx/429 stops getting sends. Since #910 such answers are given back to the
quota and counted as `transient` instead; any other send is still counted."""

import asyncio

import httpx
import pytest

from labhq.runner import lookup_admission as la
from labhq.runner.lookup_admission import LookupAdmission, breaker_wait, usage_note

EBI = "www.ebi.ac.uk"
OPENALEX = "api.openalex.org"


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def ledger(tmp_path, limit=50):
    clock = Clock()
    return LookupAdmission(tmp_path / "lookup.sqlite3", limit, clock=clock), clock


def send(admission, host, status, request_id="req_1", tool="fulltext"):
    """One admitted send whose answer is recorded, as the tool does when its call ends."""
    answer = admission.admit(request_id, "task_1", "lit_scout", tool, host)
    if answer["admitted"]:
        admission.finish(request_id, "task_1", [[answer["seq"], status]], "failed" if status >= 400 else "ok")
    return answer


def test_three_consecutive_5xx_pause_the_host_without_counting(tmp_path):
    admission, _ = ledger(tmp_path)
    for _ in range(3):
        assert send(admission, EBI, 503)["admitted"]
    refused = admission.admit("req_1", "task_1", "lit_scout", "fulltext", EBI)
    assert refused["admitted"] is False and refused["reason"] == "host_paused" and refused["host"] == EBI
    assert 0 < refused["retry_after_s"] <= la.BREAKER_COOLDOWN_S
    usage = admission.usage("req_1")
    assert (usage["used"], usage["transient"]) == (0, 3)  # the 503s are transient; the refused one was not sent at all


def test_other_hosts_and_other_requests_are_not_paused(tmp_path):
    admission, _ = ledger(tmp_path)
    for _ in range(3):
        send(admission, EBI, 503)
    assert send(admission, OPENALEX, 200)["admitted"]
    assert send(admission, EBI, 200, request_id="req_2")["admitted"]


def test_two_5xx_or_an_interrupting_answer_do_not_pause(tmp_path):
    admission, _ = ledger(tmp_path)
    for status in (503, 503, 200, 503, 503):
        assert send(admission, EBI, status)["admitted"]
    assert send(admission, EBI, 503)["admitted"]  # the third in a row is still sent...
    assert send(admission, EBI, 503)["admitted"] is False  # ...and the fourth is not


def test_a_rate_limit_that_clears_within_one_lookup_is_not_cut(tmp_path):
    admission, _ = ledger(tmp_path)
    for _ in range(4):  # the #910 8th run: OpenAlex answered 429 four times, then 200
        assert send(admission, OPENALEX, 429)["admitted"]
    assert send(admission, OPENALEX, 200)["admitted"]
    for _ in range(5):
        assert send(admission, "api.crossref.org", 429)["admitted"]
    assert send(admission, "api.crossref.org", 429)["admitted"] is False


def test_mixed_429_and_5xx_count_toward_the_five(tmp_path):
    admission, _ = ledger(tmp_path)
    for status in (503, 429, 503, 429, 503):
        assert send(admission, EBI, status)["admitted"]
    assert send(admission, EBI, 503)["admitted"] is False


def test_probe_after_the_cooldown_and_a_failed_probe_pauses_again(tmp_path):
    admission, clock = ledger(tmp_path)
    for _ in range(3):
        send(admission, EBI, 503)
    clock.now += la.BREAKER_COOLDOWN_S + 1
    assert send(admission, EBI, 503)["admitted"], "one probe is allowed once the cooldown has passed"
    assert send(admission, EBI, 503)["admitted"] is False
    clock.now += la.BREAKER_COOLDOWN_S + 1
    assert send(admission, EBI, 200)["admitted"]  # this probe succeeds and closes the breaker
    assert send(admission, EBI, 503)["admitted"]


def test_the_pause_starts_when_the_last_answer_came_not_when_it_was_sent(tmp_path):
    admission, clock = ledger(tmp_path)
    for _ in range(2):
        send(admission, EBI, 503)
    answer = admission.admit("req_1", "task_1", "lit_scout", "fulltext", EBI)
    clock.now += 120  # the third 503 took two minutes to arrive
    admission.finish("req_1", "task_1", [[answer["seq"], 503]], "failed")
    assert admission.paused_hosts("req_1") == {EBI: round(la.BREAKER_COOLDOWN_S)}
    clock.now += la.BREAKER_COOLDOWN_S - 1
    assert admission.admit("req_1", "task_1", "lit_scout", "fulltext", EBI)["reason"] == "host_paused"


def test_a_file_made_before_answered_at_existed_is_migrated(tmp_path):
    import sqlite3
    path = tmp_path / "old.sqlite3"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE sends (request_id TEXT NOT NULL, seq INTEGER NOT NULL, task_id TEXT, agent_id TEXT, "
               "tool TEXT NOT NULL, host TEXT NOT NULL, admitted_at REAL NOT NULL, http_status INTEGER, outcome TEXT, "
               "stage TEXT, PRIMARY KEY (request_id, seq))")
    for seq in (1, 2, 3):
        db.execute("INSERT INTO sends VALUES ('req_1', ?, 't', 'a', 'fulltext', ?, 1000.0, 503, 'failed', NULL)", (seq, EBI))
    db.commit()
    db.close()
    admission = LookupAdmission(path, 50, clock=Clock())
    assert admission.paused_hosts("req_1") == {EBI: round(la.BREAKER_COOLDOWN_S)}  # old rows fall back to their send time


def answer_at(admission, clock, answer, status, when):
    clock.now = when
    admission.finish("req_1", "task_1", [[answer["seq"], status]], "failed" if status >= 400 else "ok")


def test_answers_are_read_in_the_order_they_came_not_the_order_they_were_sent(tmp_path):
    admission, clock = ledger(tmp_path)
    sends = [admission.admit("req_1", "task_1", "lit_scout", "fulltext", EBI) for _ in range(4)]
    for number, status, when in ((4, 503, 1010), (3, 503, 1011), (2, 503, 1012), (1, 200, 1020)):
        answer_at(admission, clock, sends[number - 1], status, when)
    assert admission.paused_hosts("req_1") == {}  # the earliest send answered last, with a 200: not a streak
    admission, clock = ledger(tmp_path / "b")
    sends = [admission.admit("req_1", "task_1", "lit_scout", "fulltext", EBI) for _ in range(4)]
    for number, status, when in ((2, 200, 1000), (4, 503, 1001), (3, 503, 1002), (1, 503, 1030)):
        answer_at(admission, clock, sends[number - 1], status, when)
    assert list(admission.paused_hosts("req_1")) == [EBI]  # three 503s are the newest answers although seq 2 sits between


def test_only_one_probe_goes_after_the_cooldown_until_it_is_answered(tmp_path):
    admission, clock = ledger(tmp_path)
    for _ in range(3):
        send(admission, EBI, 503)
    clock.now += la.BREAKER_COOLDOWN_S + 1
    probe = admission.admit("req_1", "task_1", "lit_scout", "fulltext", EBI)
    assert probe["admitted"]
    second = admission.admit("req_1", "task_2", "analyst", "fulltext", EBI)
    assert second["admitted"] is False and second["reason"] == "host_paused"
    assert admission.usage("req_1")["used"] == 1 and admission.usage("req_1")["transient"] == 3  # the probe in flight counts, the held one does not
    admission.finish("req_1", "task_1", [[probe["seq"], 200]], "ok")
    assert admission.admit("req_1", "task_2", "analyst", "fulltext", EBI)["admitted"]  # the probe succeeded


def test_a_probe_that_never_answers_does_not_hold_the_host_for_ever(tmp_path):
    admission, clock = ledger(tmp_path)
    for _ in range(3):
        send(admission, EBI, 503)
    clock.now += la.BREAKER_COOLDOWN_S + 1
    assert admission.admit("req_1", "task_1", "lit_scout", "fulltext", EBI)["admitted"]  # lost: never finished
    clock.now += la.PROBE_STALE_S + 1
    assert admission.admit("req_1", "task_2", "analyst", "fulltext", EBI)["admitted"]


def test_a_probe_that_ends_without_an_answer_restarts_the_cooldown(tmp_path):
    admission, clock = ledger(tmp_path)
    for _ in range(3):
        send(admission, EBI, 503)
    clock.now += la.BREAKER_COOLDOWN_S + 1
    probe = admission.admit("req_1", "task_1", "lit_scout", "fulltext", EBI)
    assert probe["admitted"]
    admission.finish("req_1", "task_1", [[probe["seq"], None]], "timeout")  # no HTTP answer at all
    again = admission.admit("req_1", "task_2", "analyst", "fulltext", EBI)
    assert again["admitted"] is False and again["reason"] == "host_paused"
    assert round(again["retry_after_s"]) == round(la.BREAKER_COOLDOWN_S)


def test_unrecorded_sends_are_not_evidence(tmp_path):
    admission, _ = ledger(tmp_path)
    for _ in range(4):
        assert admission.admit("req_1", "task_1", "lit_scout", "fulltext", EBI)["admitted"]  # status not reported yet


def test_a_paused_hosts_failures_leave_the_quota_to_other_hosts(tmp_path):
    admission, _ = ledger(tmp_path, limit=4)
    for _ in range(3):
        send(admission, EBI, 503)
    for _ in range(4):
        assert send(admission, OPENALEX, 200)["admitted"]  # #910: the three 503s did not spend the quota
    assert admission.admit("req_1", "task_1", "lit_scout", "fulltext", OPENALEX)["reason"] == "exhausted"


def test_usage_note_names_a_paused_host(tmp_path):
    admission, _ = ledger(tmp_path)
    assert "Paused" not in usage_note(admission, "req_1")
    for _ in range(3):
        send(admission, EBI, 503)
    assert admission.paused_hosts("req_1") == {EBI: round(la.BREAKER_COOLDOWN_S)}
    note = usage_note(admission, "req_1")
    assert "0 of 50 used" in note and "3 of 50 such answers" in note and f"{EBI} (300s left)" in note
    assert "nothing is counted" in note


def test_breaker_wait_is_a_pure_rule():
    assert breaker_wait([], None, 0) == 0
    assert breaker_wait([503, 503], 0, 1) == 0
    assert breaker_wait([503, 503, 503], 0, 100) == la.BREAKER_COOLDOWN_S - 100
    assert breaker_wait([503, 503, 503], 0, la.BREAKER_COOLDOWN_S + 1) == 0
    assert breaker_wait([200, 503, 503, 503], 0, 1) == 0


# The tool process keeps the last answers itself: a send's status reaches the file only when its tool call ends, so the
# retries inside one call would otherwise go on.
@pytest.fixture
def annot_mcp(monkeypatch):
    from labhq.tools import annot_mcp as module
    monkeypatch.setattr(module, "LOOKUP_LIMITED", True)
    monkeypatch.setattr(module, "BROKER_TOKEN", "token")
    module.HOST_ANSWERS.clear()
    yield module
    module.HOST_ANSWERS.clear()


def test_in_call_retries_stop_at_the_third_503_and_never_reach_the_broker(annot_mcp, monkeypatch):
    admitted, finished = [], []

    async def fake_admit(tool, host):
        admitted.append(host)
        return len(admitted)

    async def fake_finish(sends, outcome):
        finished.append((sends, outcome))

    monkeypatch.setattr(annot_mcp, "_admit", fake_admit)
    monkeypatch.setattr(annot_mcp, "_finish", fake_finish)
    transport = httpx.MockTransport(lambda request: httpx.Response(503))

    async def scenario():
        async with annot_mcp._admitted("fulltext") as hooks, httpx.AsyncClient(transport=transport, **hooks) as client:
            outcomes = []
            for _ in range(5):  # annot.Http would retry up to its five attempts
                try:
                    outcomes.append((await client.get(f"https://{EBI}/europepmc/x")).status_code)
                except annot_mcp.LookupHostPaused as exc:
                    outcomes.append("paused")
                    assert EBI in str(exc) and "5xx/429" in str(exc)
            return outcomes

    assert asyncio.run(scenario()) == [503, 503, 503, "paused", "paused"]
    assert admitted == [EBI] * 3  # the two paused ones were not even offered to the counter
    assert [sends for sends, _ in finished] == [[[1, 503], [2, 503], [3, 503]]]  # one receipt, three counted sends


def test_pause_of_one_host_does_not_refuse_the_rest_of_the_call(annot_mcp, monkeypatch):
    async def fake_admit(tool, host):
        return 1

    async def fake_finish(sends, outcome):
        pass

    monkeypatch.setattr(annot_mcp, "_admit", fake_admit)
    monkeypatch.setattr(annot_mcp, "_finish", fake_finish)
    transport = httpx.MockTransport(lambda request: httpx.Response(503 if request.url.host == EBI else 200))

    async def scenario():
        async with annot_mcp._admitted("fulltext") as hooks, httpx.AsyncClient(transport=transport, **hooks) as client:
            for _ in range(3):
                await client.get(f"https://{EBI}/x")
            with pytest.raises(annot_mcp.LookupHostPaused):
                await client.get(f"https://{EBI}/x")
            return (await client.get(f"https://{OPENALEX}/works")).status_code

    assert asyncio.run(scenario()) == 200


def test_a_paused_answer_from_the_broker_becomes_a_host_pause_not_a_limit_refusal(annot_mcp, monkeypatch):
    def handler(request):
        return httpx.Response(200, json={"admitted": False, "reason": "host_paused", "host": EBI, "retry_after_s": 120,
                                         "used": 3, "limit": 20})

    real = httpx.AsyncClient
    monkeypatch.setattr(annot_mcp.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(annot_mcp, "BROKER", "http://broker.test")
    with pytest.raises(annot_mcp.LookupHostPaused) as excinfo:
        asyncio.run(annot_mcp._admit("fulltext", EBI))
    assert "120" in str(excinfo.value) and not isinstance(excinfo.value, annot_mcp.LookupRefused)


# ---- #1282: the live source check reports a resting host as host_paused, not as the request's limit ----
def _live_check(admission):
    import json
    from pathlib import Path

    from labhq.evidence.verify import LiveSourceResolver, verify_sources
    from tests.test_evidence_verify import build, claim, link, row

    doi = "10.1093/nar/gks1195"
    fixture = Path(__file__).parent / "fixtures" / "live_sources" / "crossref_normal.json"
    sent = []

    def handler(request):
        sent.append(request)
        return httpx.Response(200, request=request, json=json.loads(fixture.read_text(encoding="utf-8")))

    resolver = LiveSourceResolver(transport=httpx.MockTransport(handler), admission=admission, request_id="req_1")
    result = build([claim("c1")], [row("e1", "doi", doi, kind="literature_claim")], [link("c1", "e1")])
    return asyncio.run(verify_sources(result, resolver)).evidence[0].resolution, sent, resolver


def test_a_resting_host_is_host_paused_with_its_host_and_wait_and_keeps_the_quota(tmp_path):
    admission, _ = ledger(tmp_path, limit=50)
    for _ in range(3):
        send(admission, "api.crossref.org", 503, tool="source_verify")
    resolution, sent, resolver = _live_check(admission)
    assert (resolution.lookup, resolution.status, resolution.error_kind) == ("failed", "requires_verification",
                                                                             "host_paused")
    assert "api.crossref.org" in resolution.detail and "조회 몫은 남아 있고" in resolution.detail
    assert not sent and admission.usage("req_1")["transient"] == 3 and admission.usage("req_1")["used"] == 0  # nothing sent for the refused ones
    from labhq.evidence.verify import LookupFailed
    with pytest.raises(LookupFailed) as caught:
        asyncio.run(resolver.lookup("doi", "10.1093/nar/gks1195"))
    assert caught.value.kind == "host_paused" and caught.value.host == "api.crossref.org"
    assert 0 < caught.value.retry_after_s <= la.BREAKER_COOLDOWN_S


def test_an_exhausted_quota_is_still_lookup_limit(tmp_path):
    admission, _ = ledger(tmp_path, limit=2)
    for _ in range(2):
        admission.admit("req_1", "task_1", "bio", "annot", "pubmed.ncbi.nlm.nih.gov")
    resolution, sent, _ = _live_check(admission)
    assert resolution.error_kind == "lookup_limit" and not sent


# ---- #1282: the CP1 plan carries the resting hosts as a warning ----
def test_the_plan_warning_names_the_resting_hosts_and_replaces_its_own_earlier_line():
    from labhq.orchestrator.cso import PAUSED_HOST_WARNING, with_paused_host_warning

    plan = {"steps": [], "warnings": ["other warning", PAUSED_HOST_WARNING + "old.example(5초 남음)"]}
    cleaned = with_paused_host_warning(plan, {"www.ebi.ac.uk": 120})
    assert cleaned["warnings"][0] == "other warning" and len(cleaned["warnings"]) == 2
    line = cleaned["warnings"][1]
    assert line.startswith(PAUSED_HOST_WARNING) and "www.ebi.ac.uk(120초 남음)" in line and "old.example" not in line
    assert "조회 몫을 쓰지 않으며" in line  # it does not claim any quota is left
    assert with_paused_host_warning(cleaned, {})["warnings"] == ["other warning"]  # the host opened again
    assert with_paused_host_warning("not a plan", {"h": 1}) == "not a plan"


def test_the_orchestrator_reads_the_resting_hosts_of_its_own_request_only(tmp_path):
    from types import SimpleNamespace

    from labhq.orchestrator.cso import Orchestrator
    from labhq.settings import Settings

    settings = Settings()
    settings.runner.state_dir = str(tmp_path / "state")
    me = SimpleNamespace(hub=SimpleNamespace(s=settings))
    assert Orchestrator._paused_lookup_hosts(me, "req_1") == {}  # no cap configured: nothing is counted
    settings.annot.request_lookup_limit = 50
    admission = la.admission_for(settings)
    for _ in range(3):
        send(admission, EBI, 503)
    paused = Orchestrator._paused_lookup_hosts(me, "req_1")
    assert list(paused) == [EBI] and 0 < paused[EBI] <= la.BREAKER_COOLDOWN_S
    assert Orchestrator._paused_lookup_hosts(me, "req_2") == {}


@pytest.mark.asyncio
async def test_a_research_plan_reaches_cp1_with_the_resting_host_warning():
    from labhq.orchestrator.cso import PAUSED_HOST_WARNING, Orchestrator
    from tests.test_cp1_input_fit_796 import no_native_workers, run_plans  # noqa: F401 - the same CP1 flow

    class Resting(Orchestrator):
        def _paused_lookup_hosts(self, rid):
            return {EBI: 90}

    hub, req = await run_plans(["raw_counts", "raw_counts"], orchestrator=Resting)
    warnings = hub.approvals[0]["detail"]["warnings"]
    assert any(w.startswith(PAUSED_HOST_WARNING) and EBI in w for w in warnings)
    assert req["outcome"] == "plan_approved"  # a note, never a reason to stop
