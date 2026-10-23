"""Finish approved technical hiring before freezing a request's plan (#549)."""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import time

import yaml

from ..models import Task, TaskResult
from ..interruption import ShutdownInterrupted
from . import paper2agent, paper_skills, provenance

PLAN_RULE = (
    "When the request includes reproducing an existing paper's analysis exactly, assign that work to the "
    "paper's technician/full contract hire, even if a core employee knows the method. Add a recruit proposal "
    "with paper, repo, focus, reason and step_ids naming all its reproduction/method-specification/validation "
    "steps. In the draft use the closest core employee's agent_id as the fallback; labhq replaces it with the "
    "active hire before CP1. Never invent a future agent id. Reuse the same paper's talent when available. "
    "If both the existing method and a new implementation are requested, put reproduction (hire) and new "
    "implementation (engineer or analyst) in parallel with identical upstream dependencies, then a comparison step "
    "depending on both. For a new Nextflow workflow, sequence the hire's method/tool specification, the engineer's "
    "or analyst's workflow implementation, and the hire's validation using depends_on. Supported nf-core execution "
    "stays with bioinfo-agent. These rules also apply outside the "
    "research lane. Unavailable hiring falls back to the named core employee with a plan warning.")


def paper_key(value):
    return paper_skills.paper_id(value) or str(value or "").strip().rstrip("/").casefold()


def _proposals(plan):
    errors = proposal_errors(plan)
    if errors:
        raise ValueError("; ".join(errors))
    return copy.deepcopy(plan.get("recruit") or [])


def proposal_errors(plan):
    """Validate fresh hiring proposals before approval; never discard an invalid assignment."""
    if not isinstance(plan, dict):
        return ["recruit: plan must be an object"]
    raw_steps = plan.get("steps")
    steps = {s["id"] for s in raw_steps if isinstance(s, dict) and isinstance(s.get("id"), str)} \
        if isinstance(raw_steps, list) else set()
    rows = plan.get("recruit", [])
    if not isinstance(rows, list):
        return ["recruit must be an array"]
    errors, papers, assigned = [], set(), set()
    fields = {"paper", "repo", "focus", "reason", "step_ids"}
    for index, row in enumerate(rows):
        prefix = f"recruit[{index}]"
        if not isinstance(row, dict):
            errors.append(f"{prefix} must be an object")
            continue
        if set(row) != fields:
            errors.append(f"{prefix} requires exactly {sorted(fields)}; reproduction uses technician/full hiring")
        for field in ("paper", "repo", "focus", "reason"):
            if not isinstance(row.get(field), str):
                errors.append(f"{prefix}.{field} must be a string")
            elif field in {"focus", "reason"} and not row[field].strip():
                errors.append(f"{prefix}.{field} must not be blank")
        if not any(isinstance(row.get(f), str) and row[f].strip() for f in ("paper", "repo")):
            errors.append(f"{prefix} needs paper or repo")
        pid = paper_key(next((row[f] for f in ("paper", "repo")
                              if isinstance(row.get(f), str) and row[f].strip()), ""))
        if pid in papers:
            errors.append(f"{prefix} repeats a paper; combine its steps in one proposal")
        papers.add(pid)
        ids = row.get("step_ids")
        if not isinstance(ids, list) or not ids or any(not isinstance(s, str) or not s.strip() for s in ids):
            errors.append(f"{prefix}.step_ids must be a nonempty array of step IDs")
        else:
            if len(ids) != len(set(ids)) or assigned.intersection(ids):
                errors.append(f"{prefix}.step_ids assigns a step more than once")
            if set(ids) - steps:
                errors.append(f"{prefix}.step_ids contains unknown steps: {sorted(set(ids) - steps)}")
            assigned.update(ids)
    return errors


def conversion_payload(row):
    # Assignment and explanation do not affect conversion. Keep the effective tool requirement versioned.
    return {"paper": paper_key(row.get("paper") or row.get("repo")), "repo": row.get("repo") or "",
            "focus": row.get("focus") or "", "kind": "technician/full", "version": 1}


def _batch_key(proposals):
    return hashlib.sha256(json.dumps(proposals, sort_keys=True).encode()).hexdigest()


def _refresh_receipt(receipt, hub):
    """Invalidate only confirmed ended contracts; an offline runner may still return.

    Read YAML without Registry.load(), which would archive expired contracts here.
    The active contract file takes precedence over an older talent-pool copy.
    """
    aid = receipt.get("agent_id")
    if receipt.get("status") != "active" or not aid:
        return False
    terms = None
    base = hub.s.path(hub.s.runner.agents_dir)
    contracts = hub.s.path(hub.s.runner.contract_dir) if hub.s.runner.contract_dir else base / "contract"
    for pattern, folder in (("*.yaml", contracts), ("*/contract.yaml", hub.s.path(hub.s.runner.talent_dir))):
        for path in folder.glob(pattern):
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8"))
            except (OSError, yaml.YAMLError):
                continue
            if isinstance(data, dict) and data.get("id") == aid and isinstance(data.get("contract"), dict):
                terms = data["contract"]
                break
        if terms is not None:
            break
    if terms is None:
        agent = getattr(hub, "agents", {}).get(aid)
        if agent is None:
            agent = hub.effective_roster(include_history=True).get(aid, {})
        terms = {"status": agent.get("contract_status"), "expires_at": agent.get("expires_at")}
    status = terms.get("status")
    expires = terms.get("expires_at")
    ended = status in {"expired", "archived", "released"}
    ended = ended or (isinstance(expires, (int, float)) and expires <= time.time())
    if not ended:
        return False
    receipt.update(status="failed", error="contract_ended")
    return True


def _restore_fallbacks(plan, batch):
    changed = False
    failed = {row.get("agent_id") for row in batch.get("results", {}).values()
              if row.get("status") == "failed" and row.get("error") == "contract_ended"}
    for step in plan.get("steps", []):
        fallback = batch.get("fallback_agents", {}).get(step["id"])
        if step["agent_id"] in failed and fallback and step["agent_id"] != fallback:
            step["agent_id"] = fallback
            changed = True
    return changed


def pending_receipt_agents(req, hub):
    """Reconcile all remaining assignments before waiting for a runner, including frozen plans."""
    plan = req.get("plan") or {}
    frozen = bool(req.get("research_contract") or req.get("results"))
    # Read legacy frozen proposals as saved; validation is for new plans, not canonical snapshots.
    batch = (req.get("plan_recruitment") or {}).get(_batch_key(plan.get("recruit") or []), {})
    changed = [_refresh_receipt(row, hub) for row in batch.get("results", {}).values()]
    remaining = {s["agent_id"] for s in plan.get("steps", []) if s["id"] not in (req.get("results") or {})}
    ended = set()
    for aid in remaining:
        probe = {"status": "active", "agent_id": aid}
        if _refresh_receipt(probe, hub):
            ended.add(aid)
    restored = False if frozen else _restore_fallbacks(plan, batch)
    if not frozen:
        for step in plan.get("steps", []):
            if step["id"] in (req.get("results") or {}) or step["agent_id"] not in ended:
                continue
            fallback = batch.get("fallback_agents", {}).get(step["id"])
            if fallback and fallback not in ended:
                step["agent_id"] = fallback
                restored = True
    unresolved = ended.intersection(s["agent_id"] for s in plan.get("steps", [])
                                   if s["id"] not in (req.get("results") or {}))
    if unresolved:
        req["recruit_contract_failure"] = sorted(unresolved)
    if any(changed) or restored or unresolved:
        hub.save_request(req["id"])
    remaining_steps = {s["id"] for s in plan.get("steps", [])
                       if s["id"] not in (req.get("results") or {})}
    pending_papers = set()
    for proposal in plan.get("recruit") or []:
        step_ids = proposal.get("step_ids")
        # Legacy proposals without target metadata keep the conservative wait.
        if not isinstance(step_ids, list) or not step_ids or remaining_steps.intersection(step_ids):
            pending_papers.add(paper_key(proposal.get("paper") or proposal.get("repo")))
    return {row["agent_id"] for pid, row in batch.get("results", {}).items()
            if pid in pending_papers and row.get("status") == "active" and row.get("agent_id")}


async def prepare_plan(plan, rid, hub, run):
    plan = copy.deepcopy(plan)
    steps = {s["id"]: s for s in plan.get("steps", [])}
    proposals = _proposals(plan)
    if not proposals:
        return plan
    if not paper2agent.enabled(hub.s):
        plan["recruit_results"] = [{"paper": row.get("paper", ""), "step_ids": row["step_ids"],
                                    "status": "unavailable", "error": "recruit_disabled"} for row in proposals]
        plan["warnings"] = list(dict.fromkeys([*(plan.get("warnings") or []),
                                               "채용 생략: recruit.enabled=false; 기존 직원 배정을 유지합니다."]))
        return plan
    req = hub.requests[rid]
    # The approval and each receipt survive a restart between hiring and CP1.
    key = _batch_key(proposals)
    state = req.setdefault("plan_recruitment", {})
    batch = state.setdefault(key, {"results": {}})
    batch.setdefault("fallback_agents", {sid: step["agent_id"] for sid, step in steps.items()})

    async def dispatch(kind, payload=None):
        identity = key + ":" + paper_key((payload or {}).get("paper") or (payload or {}).get("repo"))
        task = Task(agent_id=hub.s.recruit.agent_id, request_id=rid, prompt="Prepare approved plan recruitment",
                    budget_usd=hub.s.recruit.max_budget_usd,
                    meta={"kind": kind, "recruit": payload or {}, "max_attempts": 1,
                          "recruit_identity": identity,
                          "title": "계획 전 파견직 채용", "activate_on_pass": True})
        try:
            res = await run(task)
            return res.structured if res.ok and isinstance(res.structured, dict) else {"status": "failed"}
        except ShutdownInterrupted:
            raise
        except Exception as exc:
            return {"status": "failed", "error": type(exc).__name__}

    if "check" not in batch:
        batch["check"] = await dispatch("recruit_check")
        hub.save_request(rid)
    available = batch["check"].get("status") == "ready"
    if available and "approval" not in batch and batch.get("approval_started"):
        # Gateway restart expires pending cards. Recover a decision already made; never open a second hire card.
        store = getattr(hub, "store", None)
        decisions = store.all("approval_decision").values() if store is not None else []
        saved = next((d for d in decisions if (a := d.get("approval", {})).get("request_id") == rid
                      and a.get("kind") == "recruit_plan" and a.get("detail", {}).get("target_sha256") == key), None)
        batch["approval"] = saved or {"approved": False, "state": "expired", "note": "채용 승인 대기 중단"}
    if available and "approval" not in batch:
        batch["approval_started"] = True
        hub.save_request(rid)
        batch["approval"] = await hub.request_approval(
            kind="recruit_plan", request_id=rid,
            summary=f"논문 재현 파견직 {len(proposals)}건 채용: 수습 통과하면 바로 투입합니다. "
                    "승인하면 변환·수습(인재풀에 있으면 재고용) 후 이번 계획을 확정합니다.",
            detail={"gate": "recruit_plan", "target_sha256": key, "recruits": proposals,
                    "activate_on_pass": True})
        hub.save_request(rid)
    approved = available and batch.get("approval", {}).get("approved")
    rows = []
    for row in proposals:
        pid = paper_key(row.get("paper") or row.get("repo"))
        if pid not in batch["results"]:
            batch["results"][pid] = (await dispatch("plan_recruit", row) if approved else
                                     {"status": ("timed_out" if batch.get("approval", {}).get("state") in {"timed_out", "expired"}
                                                 else "denied") if available else
                                      batch["check"].get("status", "failed")})
            hub.save_request(rid)
        receipt = batch["results"][pid]
        if _refresh_receipt(receipt, hub):
            hub.save_request(rid)
        _restore_fallbacks(plan, batch)
        agent = hub.agents.get(receipt.get("agent_id")) or {}
        active = (receipt.get("status") == "active" and agent.get("employment") == "contract"
                  and agent.get("contract_status") == "active")
        reason = {"unavailable": "Paper2Agent 미설치 또는 doctor 실패", "denied": "채용 거절",
                  "timed_out": "채용 시간 초과"}.get(receipt.get("status"), "채용·수습 실패 또는 직원 미접속")
        if receipt.get("error") == "contract_ended":
            reason = "채용 계약 만료 또는 해제"
        for sid in row["step_ids"]:
            if active:
                steps[sid]["agent_id"] = agent["id"]
            else:
                warning = f"{sid}: {reason}; 정규직 {steps[sid]['agent_id']}에 재배정"
                if warning not in plan.setdefault("warnings", []):
                    plan["warnings"].append(warning)
        rows.append({"paper": row.get("paper", ""), "step_ids": row["step_ids"], **receipt})
    plan["recruit_results"] = rows
    return plan


async def run_task(runner, task):
    """A tracked runner task wraps the existing recruiter and announces the roster before its receipt."""
    if not paper2agent.enabled(runner.s):
        return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=False, cost_usd=0, cost_known=True,
                          structured={"status": "unavailable", "error": "recruit_disabled"},
                          error="recruitment is disabled (recruit.enabled=false)")
    from ..doctor import _paper2agent_row

    async def recruit():
        if task.agent_id != runner.s.recruit.agent_id:
            raise ValueError("only the recruiter may prepare hiring")
        if _paper2agent_row(runner.s, list(runner.registry.agents.values()))["status"] == "fail":
            return {"status": "unavailable"}
        if task.meta["kind"] == "recruit_check":
            return {"status": "ready"}
        if task.meta.get("activate_on_pass") is not True:
            raise ValueError("plan hiring needs approval to activate on passing probation")
        row = task.meta["recruit"]
        pid = paper_key(row.get("paper") or row.get("repo"))
        payload = conversion_payload(row)
        for spec in runner.registry.talent_pool():
            c = spec.contract
            if not c or paper_key(c.paper or c.repo) != pid or c.kind not in {"technician", "full"}:
                continue
            if c.verification.get("conversion_payload") != payload:
                continue
            # A failed probation or changed payload must never be silently promoted by rehire.
            passed, _ = paper2agent.probation(c.kind, c.verification.get("probe_tools") or [],
                                             c.skill_dir, c.verification.get("offer_status"))
            if (not passed or c.status == "probation" or provenance.missing_hashes(spec)
                    or provenance.provenance_changes(spec)):
                return {"status": "failed", "error": "인재풀 수습·hash 확인 실패"}
            current = runner.registry.agents.get(spec.id)
            if not current or not current.contract or current.contract.expires_at <= time.time():
                spec = runner.registry.rehire(runner.registry._slug(spec), runner.s.recruit.default_ttl_days)
            elif current.contract.status != "active":
                return {"status": "failed", "error": "인재풀 직원이 active가 아님"}
            runner._send_roster()
            return {"status": "active", "agent_id": spec.id, "reused": True}
        name = "plan-" + _batch_key(payload)[:24]
        conversion = paper2agent.Recruitment(runner, {
            **row, "name": name, "request_id": task.request_id, "plan_task_id": task.id,
            "parent_budget_usd": task.budget_usd,
            "parent_budget_remaining_usd": task.request_budget_remaining_usd,
            "focus": row.get("focus", "") + "\nDeliver a technician/full hire with executable paper tools.",
        })
        spec = await conversion.run()
        runner._send_roster()
        if not spec or not spec.contract or spec.contract.status != "active" or spec.contract.kind not in {"technician", "full"}:
            return {"status": "failed", "error": "기술자형 수습 미통과"}
        spec.contract.verification["conversion_payload"] = payload
        await runner.registry.asave_contract(spec)
        return {"status": "active", "agent_id": spec.id, "reused": False}

    try:
        receipt = await asyncio.wait_for(recruit(), timeout=runner.s.runner.task_timeout_s)
    except asyncio.TimeoutError:
        receipt = {"status": "timed_out"}
    except Exception as exc:
        receipt = {"status": "failed", "error": type(exc).__name__}
    # The nested writer may have committed its result even if cancellation lost the Python return. Account that
    # task by its own ID, using the runner's ordered durable receipt rather than charging another outer unknown.
    costs = (await runner._recruitment_cost_fields(task) if runner.s.policy.budget.reserve_before_dispatch else {})
    return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True,
                      **(costs or {"cost_usd": 0, "cost_known": True}),
                      structured=receipt, text="계획 전 채용: " + receipt["status"])
