"""#910: a send answered with HTTP 5xx/429 does not spend the request's lookup quota, and a service that keeps failing
still ends the lookups at the transient cap. The 8th run: Europe PMC answered 503 fifteen times and OpenAlex 429 four
times before one 200, and all 20 sends were spent without one paper body."""

from labhq.runner import lookup_admission as la
from labhq.runner.lookup_admission import LookupAdmission, usage_note

EUROPE_PMC = "www.ebi.ac.uk"
OPENALEX = "api.openalex.org"


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def ledger(tmp_path, limit=20, **kwargs):
    clock = Clock()
    return LookupAdmission(tmp_path / "lookup.sqlite3", limit, clock=clock, **kwargs), clock


def send(admission, clock, host, status, request_id="req_1"):
    """One send whose answer is recorded; the clock moves past any pause so only the quota decides."""
    answer = admission.admit(request_id, "task_1", "lit_scout", "fulltext", host)
    if answer.get("reason") == "host_paused":
        clock.now += answer["retry_after_s"] + 1
        answer = admission.admit(request_id, "task_1", "lit_scout", "fulltext", host)
    if answer["admitted"]:
        admission.finish(request_id, "task_1", [[answer["seq"], status]], "ok" if status < 400 else "failed")
    return answer


def test_the_eighth_runs_failures_leave_the_quota_for_the_lookups_that_answer(tmp_path):
    admission, clock = ledger(tmp_path)
    for _ in range(15):
        assert send(admission, clock, EUROPE_PMC, 503)["admitted"]
    for _ in range(4):
        assert send(admission, clock, OPENALEX, 429)["admitted"]
    usage = admission.usage("req_1")
    assert (usage["used"], usage["remaining"], usage["transient"]) == (0, 20, 19)
    for index in range(20):  # the whole quota is still there for answers
        assert send(admission, clock, OPENALEX, 200)["admitted"], index
    refused = admission.admit("req_1", "task_1", "lit_scout", "fulltext", OPENALEX)
    assert refused["admitted"] is False and refused["reason"] == "exhausted" and refused["used"] == 20


def test_a_service_that_only_fails_stops_at_the_transient_cap(tmp_path):
    admission, clock = ledger(tmp_path)
    sent = 0
    while send(admission, clock, EUROPE_PMC, 503)["admitted"]:
        sent += 1
        assert sent <= 20
    assert sent == 20
    refused = admission.admit("req_1", "task_1", "lit_scout", "fulltext", OPENALEX)
    assert refused["admitted"] is False and refused["reason"] == "transient exhausted"
    assert (refused["transient"], refused["transient_limit"], refused["used"]) == (20, 20, 0)


def test_the_transient_cap_can_differ_from_the_quota(tmp_path):
    admission, clock = ledger(tmp_path, limit=20, transient_limit=5)
    for _ in range(5):
        assert send(admission, clock, OPENALEX, 429)["admitted"]
    assert admission.admit("req_1", "task_1", "lit_scout", "fulltext", OPENALEX)["reason"] == "transient exhausted"


def test_sends_in_flight_and_sends_without_an_answer_stay_counted(tmp_path):
    admission, _ = ledger(tmp_path, limit=3)
    pending = admission.admit("req_1", "task_1", "lit_scout", "fulltext", OPENALEX)
    lost = admission.admit("req_1", "task_1", "lit_scout", "fulltext", OPENALEX)
    admission.finish("req_1", "task_1", [[lost["seq"], None]], "timeout")
    error = admission.admit("req_1", "task_1", "lit_scout", "fulltext", OPENALEX)
    admission.finish("req_1", "task_1", [[error["seq"], 404]], "failed")  # a 4xx other than 429 is the caller's
    assert pending["admitted"] and admission.usage("req_1")["used"] == 3
    assert admission.admit("req_1", "task_1", "lit_scout", "fulltext", OPENALEX)["reason"] == "exhausted"


def test_send_numbers_stay_unique_when_failures_are_given_back(tmp_path):
    admission, clock = ledger(tmp_path)
    seqs = [send(admission, clock, OPENALEX, status)["seq"] for status in (503, 200, 429, 200)]
    assert seqs == [1, 2, 3, 4]
    assert [row["seq"] for row in admission.sends("req_1")] == [1, 2, 3, 4]
    assert admission.usage("req_1")["used"] == 2


def test_the_pre_plan_share_does_not_count_failures_either(tmp_path):
    admission, clock = ledger(tmp_path, limit=10, precedent_share=0.2)  # share cap 2
    for status in (503, 503, 200):
        answer = admission.admit("req_1", "task_1", "lit_scout", "fulltext", OPENALEX, stage="precedent")
        assert answer["admitted"]
        admission.finish("req_1", "task_1", [[answer["seq"], status]], "ok")
    assert admission.admit("req_1", "task_1", "lit_scout", "fulltext", OPENALEX, stage="precedent")["admitted"]
    assert admission.admit("req_1", "task_1", "lit_scout", "fulltext", OPENALEX,
                           stage="precedent")["reason"] == "stage share exhausted"


def test_the_usage_note_says_failures_are_not_counted(tmp_path):
    admission, clock = ledger(tmp_path)
    assert "such answers" not in usage_note(admission, "req_1")
    send(admission, clock, OPENALEX, 429)
    note = usage_note(admission, "req_1")
    assert "0 of 20 used" in note and "1 of 20 such answers" in note and "except one answered with HTTP 3xx/5xx/429" in note
    assert la.failing_status(503) and la.failing_status(429) and not la.failing_status(404)


def test_the_tool_says_the_service_keeps_failing_not_that_the_quota_is_spent(monkeypatch):
    import asyncio

    import httpx
    import pytest

    from labhq.tools import annot_mcp

    monkeypatch.setattr(annot_mcp, "LOOKUP_LIMITED", True)
    monkeypatch.setattr(annot_mcp, "BROKER_TOKEN", "token")
    monkeypatch.setattr(annot_mcp, "BROKER", "http://broker.test")
    answer = {"admitted": False, "reason": "transient exhausted", "transient": 20, "transient_limit": 20, "used": 0,
              "limit": 20}
    real = httpx.AsyncClient
    monkeypatch.setattr(annot_mcp.httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=answer)), **kw))
    with pytest.raises(annot_mcp.LookupRefused) as caught:
        asyncio.run(annot_mcp._admit("fulltext", OPENALEX))
    assert "5xx/429" in str(caught.value) and "20회" in str(caught.value) and "조회 몫은 남아" in str(caught.value)


def test_the_source_check_reports_the_transient_cap_as_could_not_verify(tmp_path):
    import asyncio

    import httpx
    import pytest

    from labhq.evidence.verify import LiveSourceResolver, LookupFailed

    admission, clock = ledger(tmp_path, limit=20, transient_limit=1)
    send(admission, clock, OPENALEX, 503)
    sent = []
    resolver = LiveSourceResolver(transport=httpx.MockTransport(lambda r: sent.append(r) or httpx.Response(200)),
                                  admission=admission, request_id="req_1")
    with pytest.raises(LookupFailed) as caught:
        asyncio.run(resolver.lookup("doi", "10.1093/nar/gks1195"))
    assert caught.value.kind == "lookup_limit" and "5xx/429" in str(caught.value.detail) and not sent


def test_sends_in_flight_hold_places_under_the_transient_cap(tmp_path):
    """Concurrent sends near the cap: each one in flight may come back 503, so they cannot all pass and overshoot it."""
    admission, clock = ledger(tmp_path, limit=20, transient_limit=3)
    send(admission, clock, OPENALEX, 503)
    send(admission, clock, OPENALEX, 503)
    flying = admission.admit("req_1", "task_1", "lit_scout", "fulltext", OPENALEX)
    assert flying["admitted"]
    refused = admission.admit("req_1", "task_2", "analyst", "fulltext", OPENALEX)
    assert refused["admitted"] is False and refused["reason"] == "transient exhausted"
    admission.finish("req_1", "task_1", [[flying["seq"], 200]], "ok")  # it answered: the place is free again
    assert admission.admit("req_1", "task_2", "analyst", "fulltext", OPENALEX)["admitted"]
    assert admission.usage("req_1")["transient"] == 2


def test_a_long_tool_call_keeps_its_transient_places_until_it_reports(tmp_path):
    """A tool reports its sends only when the whole call ends, which can take minutes of retries: its unanswered sends
    keep their places however long that is, and a send never reported keeps it (as it keeps its place in `used`)."""
    admission, clock = ledger(tmp_path, limit=20, transient_limit=2)
    first = admission.admit("req_1", "task_1", "lit_scout", "fulltext", OPENALEX)
    second = admission.admit("req_1", "task_1", "lit_scout", "fulltext", OPENALEX)
    clock.now += 10 * 60
    assert admission.admit("req_1", "task_2", "analyst", "fulltext", OPENALEX)["reason"] == "transient exhausted"
    admission.finish("req_1", "task_1", [[first["seq"], 503], [second["seq"], 503]], "failed")
    assert admission.usage("req_1")["transient"] == 2
    assert admission.admit("req_1", "task_2", "analyst", "fulltext", OPENALEX)["reason"] == "transient exhausted"


def test_a_redirect_is_given_back_and_the_hop_that_answers_is_the_one_counted(tmp_path):
    """#910 10th run: 10 of 22 sends were 301/302 hops. A 302 then a 200 spends one send of the quota."""
    admission, clock = ledger(tmp_path, limit=2)
    send(admission, clock, "doi.org", 302)
    send(admission, clock, "www.nejm.org", 301)
    send(admission, clock, "www.nejm.org", 200)
    usage = admission.usage("req_1")
    assert (usage["used"], usage["remaining"], usage["transient"]) == (1, 1, 0)  # redirects take no transient cap
    assert send(admission, clock, OPENALEX, 200)["admitted"]
    assert admission.admit("req_1", "task_1", "lit_scout", "fulltext", OPENALEX)["reason"] == "exhausted"


def test_redirects_never_pause_a_host(tmp_path):
    admission, clock = ledger(tmp_path)
    for _ in range(la.BREAKER_ANY + 2):
        assert send(admission, clock, "doi.org", 301)["admitted"]
    assert admission.paused_hosts("req_1") == {}


# ---- #910 r9: an ancestor's .tmp/literature papers reach the next step's .tmp/literature ------------------------------
# The 10th run's abstract stayed in the literature step's .tmp/literature/ (licensed text is never an output) and the
# next step, which receives only its ancestors' outputs, never saw it.

import os  # noqa: E402
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402

from labhq.runner import step_inputs  # noqa: E402


def _paper_folder(tmp_path):
    root = tmp_path / "runs"
    lit = root / "d" / "task_1_lit_scout" / ".tmp" / "literature"
    lit.mkdir(parents=True)
    (lit / "doi-10.1200_jco.21.02752.md").write_bytes(b"KRYSTAL-1 abstract: ORR 53.3% (8/15)\n")
    (lit / "doi-10.1200_jco.21.02752.md.receipt.json").write_bytes(b'{"paper_id": "doi:10.1200/jco.21.02752"}')
    (lit / "big.md").write_bytes(b"x" * 300)
    (lit / "inbox").mkdir()
    (lit / "inbox" / "nested.pdf").write_bytes(b"%PDF")
    step = root / "d" / "task_2_analyst"
    step.mkdir(parents=True)
    return root, lit, step


def test_an_ancestors_papers_reach_the_step_tmp_not_its_outputs(tmp_path):
    root, lit, step = _paper_folder(tmp_path)
    remaining = [10_000]
    step_inputs._copy_literature(root.resolve(), lit.parent.parent.relative_to(root).parts, step, 200, remaining)
    copied = step / ".tmp" / "literature"
    assert sorted(p.name for p in copied.iterdir()) == ["doi-10.1200_jco.21.02752.md",
                                                       "doi-10.1200_jco.21.02752.md.receipt.json"]
    assert "ORR 53.3%" in (copied / "doi-10.1200_jco.21.02752.md").read_text(encoding="utf-8")
    assert not (step / "outputs").exists()  # big.md is over max_file, the inbox folder is not copied
    assert remaining[0] == 10_000 - sum(p.stat().st_size for p in copied.iterdir())


def test_a_paper_the_step_already_has_is_kept_and_the_budget_is_respected(tmp_path):
    root, lit, step = _paper_folder(tmp_path)
    mine = step / ".tmp" / "literature"
    mine.mkdir(parents=True)
    (mine / "doi-10.1200_jco.21.02752.md").write_bytes(b"the step's own copy")
    remaining = [45]
    step_inputs._copy_literature(root.resolve(), lit.parent.parent.relative_to(root).parts, step, 1000, remaining)
    assert (mine / "doi-10.1200_jco.21.02752.md").read_bytes() == b"the step's own copy"
    assert sorted(p.name for p in mine.iterdir()) == ["doi-10.1200_jco.21.02752.md",
                                                     "doi-10.1200_jco.21.02752.md.receipt.json"]
    assert remaining[0] == 45 - len(b'{"paper_id": "doi:10.1200/jco.21.02752"}')


@pytest.mark.skipif(os.name == "nt", reason="creating a symlink needs a privilege on Windows")
def test_a_link_in_the_ancestors_folder_is_not_followed(tmp_path):
    root, lit, step = _paper_folder(tmp_path)
    secret = tmp_path / "secret.txt"
    secret.write_text("private")
    (lit / "link.md").symlink_to(secret)
    step_inputs._copy_literature(root.resolve(), lit.parent.parent.relative_to(root).parts, step, 1000, [10_000])
    assert not (step / ".tmp" / "literature" / "link.md").exists()


def test_an_ancestor_without_papers_changes_nothing(tmp_path):
    root = tmp_path / "runs"
    ancestor = root / "d" / "task_1"
    ancestor.mkdir(parents=True)
    step = root / "d" / "task_2"
    step.mkdir()
    step_inputs._copy_literature(root.resolve(), ancestor.relative_to(root).parts, step, 1000, [10])
    assert not (step / ".tmp").exists()


def test_stage_inputs_copies_the_papers_of_each_accepted_ancestor():
    """The copy runs where the ancestor's outputs are staged, only after that ancestor was accepted."""
    source = Path(step_inputs.__file__).read_text(encoding="utf-8")
    body = source[source.index("def stage_inputs("):]
    assert body.index("staged.append(") < body.index("_copy_literature(")
    assert body.index("_copy_literature(") < body.index("except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc")
