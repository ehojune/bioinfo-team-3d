"""Fake recruiter/Paper2Skill only: no download, CLI conversion, contract or trial instance."""
import asyncio
import copy
import json
import hashlib
import re
from pathlib import Path

import pytest

from labhq.models import AgentSpec, ContractInfo, Employment, Engine, Task, TaskResult
from labhq.orchestrator.cso import Orchestrator
from labhq.runner.daemon import Runner
from labhq.settings import Settings
from labhq.util import output_relpath
from tests.test_cso import FakeHub
from tests.test_research_protocol import MiniHub, valid_plan
from tests.test_request_bundle import pending_bundle_fixture, manifest_rows


SELECTED = {"paper_id": "PMID:12345678", "title": "Paired method", "reason": "Preserves donor pairing",
            "step_ids": ["s1"]}
EVIDENCE = {"coverage_version": 2, "paper_id": "PMID:12345678", "source": "synthetic", "access_level": "full_text",
            "extraction": "complete", "text_file": "/synthetic/paper.md", "text_sha256": "a" * 64,
            "supplements_required": False, "supplements": "unknown",
            "evidence": [{"kind": kind, "start_line": 1, "end_line": 1} for kind in ("methods", "qc")]}
PRECEDENTS = {"status": "ok", "papers": [{"title": "Paired method", "citations": [
              "PMID:12345678; access_level=full_text"], "fulltext_receipt": EVIDENCE}],
              "required": [], "recommended": [], "limitations": []}


@pytest.fixture(autouse=True)
def deterministic_paper_io(monkeypatch):
    """Fake external/native boundaries; keep real owned writes, fsync and SQLite semantics."""
    monkeypatch.setattr('labhq.adapters.claude_code._git', lambda *args: None)
    monkeypatch.setattr('labhq.integrations.rounds._git_commit', lambda: 'a' * 40)
    monkeypatch.setattr('labhq.runner.workspace.platform.node', lambda: 'fixture-host')
    async def inline(fn, *args, **kwargs):
        return fn(*args, **kwargs)
    async def commit(_lane, fn, *args, **kwargs):
        return fn(*args, **kwargs)
    monkeypatch.setattr(asyncio, 'to_thread', inline)
    monkeypatch.setattr('labhq.durable.run', commit)


@pytest.fixture
def runner(tmp_path, monkeypatch):
    s = Settings()
    for key in ("state_dir", "workspace_root", "agents_dir", "talent_dir"):
        setattr(s.runner, key, str(tmp_path / key))
    s.engines.claude_code.env = {"CLAUDE_CONFIG_DIR": str(tmp_path / "staff")}
    source = Path(s.runner.workspace_root) / "scout" / "paper.md"
    source.parent.mkdir(parents=True)
    source.write_text("# Synthetic methods and QC\n", encoding="utf-8")
    monkeypatch.setitem(EVIDENCE, "text_file", str(source))
    monkeypatch.setitem(EVIDENCE, "text_sha256", hashlib.sha256(source.read_bytes()).hexdigest())
    source.with_name(source.name + ".receipt.json").write_text(json.dumps(EVIDENCE), encoding="utf-8")
    r = Runner(s)
    r.registry.agents = {name: AgentSpec(id=name, name=name, role="test", engine=Engine.claude_code,
                                       builtin_mcp=[]) for name in ("worker", "other", "recruiter")}
    monkeypatch.setattr("labhq.doctor._paper2agent_row", lambda *a, **kw: {"status": "ok"})
    monkeypatch.setattr("labhq.recruit.paper2agent.installed_commit", lambda *a: "a" * 40)
    seen = []

    class Adapter:
        async def run(self, ctx):
            seen.append(ctx)
            if ctx.task.meta.get("kind") == "paper_skill":
                skill = ctx.workdir / "delivered"
                skill.mkdir()
                (skill / "SKILL.md").write_text("# Paired method\nSee figures/table.txt", encoding="utf-8")
                (skill / "figures").mkdir()
                (skill / "figures/table.txt").write_text("paper evidence", encoding="utf-8")
                return TaskResult(task_id=ctx.task.id, agent_id=ctx.agent.id, ok=True, cost_usd=0.25,
                                  structured={"kind": "consultant", "skill_dir": str(skill),
                                              "verification_status": "verified"})
            for name in ctx.task.meta.get('required_outputs', []):
                relative = output_relpath(name)
                assert relative is not None
                target = ctx.workdir / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text('Synthetic fixture output\n', encoding='utf-8')
            copies = list(ctx.workdir.glob(".agents/skills/paper-*/SKILL.md"))
            ctx.task.meta["test_copies"] = len(copies)
            if copies:
                assert (copies[0].parent / "figures/table.txt").read_text() == "paper evidence"
                from labhq.adapters import get_adapter
                command = get_adapter(ctx.agent.engine, s).build_command(ctx)
                if ctx.agent.engine == Engine.claude_code:
                    assert "--plugin-dir" in command and "--disable-slash-commands" not in command
                else:
                    assert "--ignore-user-config" in command and str(ctx.workdir) in command
            text = (re.search(r'\[paper-skill:[^\]]+\]', ctx.task.context)[0]
                    if copies and ctx.task.meta.get('test_reference') else 'done')
            return TaskResult(task_id=ctx.task.id, agent_id=ctx.agent.id, ok=True, cost_usd=0, text=text)

    monkeypatch.setattr("labhq.runner.daemon.get_adapter", lambda *a: Adapter())
    return r, seen


def build_task(rid="r"):
    return Task(agent_id="recruiter", request_id=rid, prompt="p",
                meta={"kind": "paper_skill", "paper_skill": {**copy.deepcopy(SELECTED), "fulltext_receipt": copy.deepcopy(EVIDENCE)}})


@pytest.mark.asyncio
async def test_selection_conversion_cp1_scope_appendix_and_bundle(runner, tmp_path):
    r, seen = runner
    plan = valid_plan(steps=2)
    plan['steps'][0]['depends_on'] = ['s2']
    plan['steps'][1]['agent_id'] = 'other'
    plan["paper_skills"] = [copy.deepcopy(SELECTED)]
    s = r.s
    s.research.enabled = True
    s.orchestrator.chief_of_staff_agent = None
    s.orchestrator.precedent_agent = "lit_scout"
    s.orchestrator.reviewer_agent = None

    async def reply(task):
        if task.meta["kind"] == "precedent":
            return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, cost_usd=0,
                              structured={k: v for k, v in PRECEDENTS.items() if k != "status"})
        if task.meta["kind"] == "plan":
            assert "paper_skills" in task.prompt
            return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, structured=plan, cost_usd=0)
        if task.meta['kind'] == 'step' and task.meta['step_id'] == 's2':
            # Fake upstream work supplies lead time. The selected ready step never waits for conversion.
            batch = orch.paper_builds['r']
            assert len(batch.jobs) == 1
            await asyncio.gather(*batch.jobs.values())
        return await r.run_task(task)

    hub = MiniHub(s, reply, mode="plan_only", work_kind="research")
    hub.requests['r']['budget_usd'] = 15  # approved upfront: two normal slots plus one optional turn
    hub.agents.update({name: {"id": name, "name": name, "role": "test", "engine": "mock"}
                       for name in ("lit_scout", "recruiter", "other")})
    await Orchestrator(hub).run_request("r")
    req = hub.requests["r"]
    assert req["status"] == "done", req.get("report")
    rows = req["plan"].get("paper_skill_results", [])
    assert len(rows) == 1 and rows[0]["status"] == "deferred" and not seen
    assert [t.meta["kind"] for t in hub.calls] == ["precedent", "plan"]
    cp1 = next(c for c in hub.approvals if c["kind"] == "research_plan")
    assert "참고 논문 스킬: Paired method → worker" in cp1["summary"]
    assert rows[0]["selection_sha256"] in cp1["detail"]["plan_canonical"]
    assert '승인 후' in cp1['summary']
    frozen = copy.deepcopy(req['plan'])

    # Exercise the actual DAG dispatch and runner; another step and request get no copies.
    dag = FakeHub(reply)
    dag.s = s
    dag.requests['r'].update(plan=req['plan'], budget_usd=req['budget_usd'], research_contract={
        key: copy.deepcopy(req['research_contract'][key]) for key in ('plan_sha256', 'approval')})
    steps = req['plan']['steps']  # execute exactly the dependency/agent scope shown at CP1
    dag_results = {}
    orch = Orchestrator(dag)
    await orch.run_dag("r", "request", steps, dag_results)
    assert set(dag_results) == {'s1', 's2'} and all(result.ok for result in dag_results.values())
    assert all(result.outputs for result in dag_results.values())
    assert sum(ctx.task.meta.get('kind') == 'paper_skill' for ctx in seen) == 1
    assert not orch.paper_builds and not any(orch.paper_budget_holds.values())
    contexts = {ctx.task.agent_id: ctx for ctx in seen if ctx.task.meta.get("kind") == "step"}
    assert contexts["worker"].task.meta["test_copies"] == 1
    assert req['plan'] == frozen  # deferred build never edits the CP1-frozen plan
    built = contexts['worker'].task.meta['paper_skills'][0]
    assert built['paper2agent_commit'] == 'a' * 40
    assert contexts["other"].task.meta["test_copies"] == 0
    assert not list(contexts["worker"].workdir.glob(".agents/skills/paper-*/SKILL.md"))
    assert r.registry.agents["worker"].plugin_dirs == []
    assert not list(r.registry.contract_dir.glob("*.yaml"))
    conversion = next(ctx for ctx in seen if ctx.task.meta.get('kind') == 'paper_skill')
    assert conversion.task.budget_usd == min(s.recruit.max_budget_usd, s.policy.budget.per_task_usd) == 5
    assert conversion.settings.runner.task_timeout_s == s.runner.task_timeout_s

    bs, request, task = pending_bundle_fixture(tmp_path / "bundle", "r")
    request["plan"] = req["plan"]
    request["execution_record"] = req["execution_record"]
    request['results']['s1'] = dag_results['s1'].model_dump(mode='json')
    from labhq.request_bundle import build_request_bundle
    bundle = Path(build_request_bundle(request, bs, {"task-r": task})["path"])
    record = json.loads((bundle / "paper_skills.json").read_text(encoding="utf-8"))[0]
    assert record["paper_id"] == "pmid:12345678" and record['built']
    assert record['usage'][0]['skill_sha256'] == built['skill_sha256'] and record['called'] is False
    assert "skill_dir" not in record and "paper_skills.json" in manifest_rows(bundle)


@pytest.mark.asyncio
async def test_enriched_pmid_citations_build_full_text_and_record_abstract_skip(runner):
    """#660/#670: selection never disappears between PLAN and Paper2Skill."""
    from labhq.recruit.paper_skills import appendix, card_text, prepare_plan

    r, seen = runner
    plan = valid_plan()
    plan["paper_skills"] = [
        {**SELECTED, "paper_id": "PMID:40119824", "title": "Abstract paper"},
        {**SELECTED, "paper_id": "PMID:20492728", "title": "Full paper"},
    ]
    precedents = {"papers": [
        {"title": "Abstract paper", "citations": [
            "PubMed PMID: 40119824; access_level=abstract; n=209"]},
        {"title": "Full paper", "citations": [
            "PubMed PMID: 20492728; access_level=full_text; n=16,384"],
         "fulltext_receipt": {**EVIDENCE, "paper_id": "PMID:20492728"}},
    ]}

    out = await prepare_plan(plan, precedents, [{"id": "worker"}], "r", r.s, r.run_task)

    rows = out["paper_skill_results"]
    assert [(row["paper_id"], row["status"]) for row in rows] == [
        ("pmid:40119824", "skipped"), ("pmid:20492728", "deferred")]
    assert "receipt" in rows[0]["skip_reason"] and not seen
    assert [(row["selected"], row["built"], row["attached_steps"], row["called"]) for row in rows] == [
        (True, False, [], False), (True, False, [], False)]
    assert "선정됨" in card_text(out) and "만들어짐" in card_text(out)
    report = appendix(out, {})
    assert "붙음" in report and "결과 참조: 실행 전" in report and "receipt" in report and "호출됨" not in report


@pytest.mark.asyncio
async def test_talent_reuse_checks_hash_and_never_converts_again(runner):
    r, seen = runner
    first = await r.run_task(build_task())
    second = await r.run_task(build_task("other-request"))
    assert second.structured["status"] == "reused"
    assert len(seen) == 1
    skill = Path(first.structured["skill_dir"])
    (skill / "SKILL.md").write_text("changed", encoding="utf-8")
    changed = await r.run_task(build_task("third"))
    assert changed.structured["status"] == "failed" and len(seen) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", [False, True])
async def test_conversion_failure_or_missing_install_keeps_plan_running(runner, monkeypatch, missing):
    r, seen = runner
    if missing:
        monkeypatch.setattr("labhq.doctor._paper2agent_row", lambda *a, **kw: {"status": "fail"})
    else:
        class Failed:
            async def run(self, ctx):
                return TaskResult(task_id=ctx.task.id, agent_id=ctx.agent.id, ok=False, error="fake conversion failed")
        monkeypatch.setattr("labhq.runner.daemon.get_adapter", lambda *a: Failed())
    plan = {**valid_plan(), "paper_skills": [SELECTED]}
    r.s.research.enabled = True
    r.s.orchestrator.chief_of_staff_agent = None
    r.s.orchestrator.reviewer_agent = None

    async def reply(task):
        if task.meta["kind"] == "plan":
            return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, structured=plan, cost_usd=0)
        return await r.run_task(task)

    hub = MiniHub(r.s, reply, mode="plan_only", work_kind="research")
    hub.agents["recruiter"] = {"id": "recruiter", "name": "R", "role": "recruit", "engine": "mock"}
    hub.requests["r"]["analysis_precedents"] = PRECEDENTS
    await Orchestrator(hub).run_request("r")
    assert hub.requests["r"]["status"] == "done"
    assert any(card["kind"] == "research_plan" for card in hub.approvals)
    out = hub.requests["r"]["plan"]
    assert out["paper_skill_results"][0]["status"] == 'deferred' and not seen
    from labhq.recruit.paper_skills import attachments, resolve_attachments
    resolved = await resolve_attachments(attachments(out, 's1', 'worker', 'r'), r.s, r.run_task)
    assert resolved[0]['status'] == ('unavailable' if missing else 'failed')
    if missing:
        assert resolved[0]['error'] == '스킬 장착 불가(Paper2Agent 미설치)' and not seen


@pytest.mark.asyncio
async def test_existing_contract_talent_is_advice_without_activation(runner, tmp_path):
    from labhq.recruit.provenance import folder_sha256
    r, seen = runner
    skill = tmp_path / "old-skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text("advice", encoding="utf-8")
    r.registry.save_contract(AgentSpec(id="c_old", name="old", role="paper", employment=Employment.contract,
        contract=ContractInfo(paper=SELECTED["paper_id"], status="expired", kind="consultant", hired_at=1, expires_at=2,
                              skill_dir=str(skill), skill_sha256=folder_sha256(skill),
                              verification={"paper2agent_commit": "b" * 40})))
    result = await r.run_task(build_task())
    assert result.structured["status"] == "failed" and "receipt" in result.structured["error"] and not seen
    assert r.registry.agents["c_old"].contract.status == "expired"


def test_selection_requires_precedent_ids_core_steps_and_caps_at_two():
    from labhq.recruit.paper_skills import selections
    plan = {"steps": [{"id": "s1", "agent_id": "worker"}, {"id": "s2", "agent_id": "contract"}],
            "paper_skills": [{**SELECTED, "paper_id": f"PMID:{i}"} for i in range(1, 5)]}
    precedent = {"papers": [{"title": str(i), "citations": [f"PMID:{i}"]} for i in range(1, 4)]}
    roster = [{"id": "worker", "employment": "core"}, {"id": "contract", "employment": "contract"}]
    assert len(selections(plan, precedent, roster)) == 2
    plan["paper_skills"] = [{**SELECTED, "paper_id": "PMID:4"}, {**SELECTED, "paper_id": "PMID:1", "step_ids": ["s2"]}]
    assert selections(plan, precedent, roster) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [("request_id", "other"), ("agent_id", "other"), ("step_id", "s2")])
async def test_attachment_scope_rejects_wrong_request_employee_or_step(runner, field, value):
    r, seen = runner
    built = await r.run_task(build_task())
    assert built.structured.get("status") == "ready"
    row = {**built.structured, "request_id": "r", "agent_id": "worker", "step_id": "s1", field: value}
    await r.run_task(Task(agent_id="worker", request_id="r", prompt="p",
                          meta={"kind": "step", "step_id": "s1", "paper_skills": [row]}))
    assert seen[-1].task.meta["test_copies"] == 0


@pytest.mark.asyncio
async def test_two_papers_have_distinct_restart_keys_and_keep_their_own_receipts():
    from labhq.recruit.paper_skills import prepare_plan, resolve_attachments, attachments
    from labhq.gateway.server import Hub
    tasks = []

    async def convert(task):
        tasks.append(task)
        return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True,
                          structured={"status": "ready", "paper_id": "pmid:12345678"})

    plan = {"steps": [{"id": "s1", "agent_id": "worker"}],
            "paper_skills": [SELECTED, {**SELECTED, "paper_id": "PMID:87654321"}]}
    precedents = {**PRECEDENTS, "papers": [*PRECEDENTS["papers"],
                  {"title": "Another method", "citations": [
                      "PMID:87654321; access_level=full_text"],
          "fulltext_receipt": {**EVIDENCE, "paper_id": "PMID:87654321"}}]}
    out = await prepare_plan(plan, precedents, [{"id": "worker"}], "r", Settings(), convert)
    assert not tasks
    resolved = await resolve_attachments(attachments(out, 's1', 'worker', 'r'), Settings(), convert)
    first = tasks[0]
    prior = {"request_id": "r", "kind": "paper_skill", "step_id": first.meta["step_id"],
             "payload": first.model_dump(mode="json")}
    assert Hub._matches_recovery(first, prior)
    assert not Hub._matches_recovery(tasks[1], prior)
    assert resolved[1]["status"] == "failed"


@pytest.mark.asyncio
@pytest.mark.parametrize("hold", ["login", "quota"])
async def test_optional_paper_conversion_does_not_park_on_staff_account_hold(hold):
    async def dispatch(task):
        raise AssertionError("held engine must not run")
    hub = FakeHub(dispatch)
    setattr(hub, hold + "_hold", lambda engine: {"resume_at": 1})
    result = await Orchestrator(hub).run_step(build_task())
    assert not result.ok and not hub.calls and not hub.approvals


@pytest.mark.asyncio
@pytest.mark.parametrize("engine", [Engine.claude_code, Engine.codex])
@pytest.mark.parametrize('referenced', [False, True])
async def test_each_staff_engine_gets_local_skill_and_usage_receipt_only_for_its_task(runner, engine, referenced):
    r, seen = runner
    built = await r.run_task(build_task())
    assert built.structured.get("status") == "ready"
    r.registry.agents["worker"].engine = engine
    row = {**built.structured, "request_id": "r", "agent_id": "worker", "step_id": "s1"}
    result = await r.run_task(Task(agent_id="worker", request_id="r", prompt="p",
                                  meta={"kind": "step", "step_id": "s1", "paper_skills": [row],
                                        'test_reference': referenced}))
    assert result.ok and seen[-1].task.meta["test_copies"] == 1
    usage = result.provenance["runs"][result.task_id]["paper_skills"]
    assert [{key: item[key] for key in ('paper_id', 'paper2agent_commit', 'skill_sha256')} for item in usage] == [
        {key: row[key] for key in ('paper_id', 'paper2agent_commit', 'skill_sha256')}]
    assert usage[0]['called'] is referenced
    assert usage[0]['reason'] == ('result_reference' if referenced else 'no_result_reference')
    workdir = seen[-1].workdir
    assert not list(workdir.glob(".agents/skills/paper-*/SKILL.md"))
    assert not list(workdir.glob(".labhq-paper-skills/skills/*/SKILL.md"))
    # Even an explicitly reused notebook cannot expose the prior request's skill.
    await r.run_task(Task(agent_id="worker", request_id="next", prompt="new request"), workdir_override=workdir)
    assert seen[-1].task.meta["test_copies"] == 0
