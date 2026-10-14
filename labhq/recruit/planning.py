"""Finish approved technical hiring before freezing a request's plan (#549)."""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import time

import yaml

from ..models import Task, TaskResult
from . import paper2agent, paper_skills, provenance

PLAN_RULE = (
    "When the request includes reproducing an existing paper's analysis exactly, assign that work to the "
    "paper's technician/full contract hire, even if a core employee knows the method. Add a recruit proposal "
    "with paper, repo, focus, reason and step_ids naming all its reproduction/method-specification/validation "
    "steps. In the draft use the closest core employee's agent_id as the fallback; labhq replaces it with the "
    "active hire before CP1. Never invent a future agent id. Reuse the same paper's talent when available. "
    "If both the existing method and a new implementation are requested, put reproduction (hire) and new "
    "implementation (bioinfo-agent) in parallel with identical upstream dependencies, then a comparison step "
    "depending on both. For a Nextflow workflow, sequence the hire's method/tool specification, bioinfo-agent's "
    "workflow implementation, and the hire's validation using depends_on. These rules also apply outside the "
    "research lane. Unavailable hiring falls back to the named core employee with a plan warning.")


def paper_key(value):
    return paper_skills.paper_id(value) or str(value or "").strip().rstrip("/").casefold()


def _proposals(plan):
    steps = {s["id"]: s for s in plan.get("steps", [])}
    proposals = []
    for row in plan.get("recruit") or []:
        ids = list(dict.fromkeys(s for s in row.get("step_ids", []) if s in steps))
        if not (row.get("paper") or row.get("repo")):
            continue
        existing = next((p for p in proposals if paper_key(p.get("paper") or p.get("repo")) ==
                         paper_key(row.get("paper") or row.get("repo"))), None)
        if existing:
            existing["step_ids"] = list(dict.fromkeys(existing["step_ids"] + ids))
        else:
            proposals.append({**row, "step_ids": ids})
    return proposals


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
        agent = hub.agents.get(aid)
        if agent is None:
            agent = next((a for entry in getattr(hub, "last_runner_rosters", {}).values()
                          for a in entry.get("agents", []) if a.get("id") == aid), {})
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
    """Only the current, unfrozen draft's receipts gate restart readiness."""
    if req.get("research_contract") or req.get("results"):
        return set()
    batch = (req.get("plan_recruitment") or {}).get(_batch_key(_proposals(req.get("plan") or {})), {})
    changed = [_refresh_receipt(row, hub) for row in batch.get("results", {}).values()]
    restored = _restore_fallbacks(req.get("plan") or {}, batch)
    if any(changed) or restored:
        hub.save_request(req["id"])
    return {row["agent_id"] for row in batch.get("results", {}).values()
            if row.get("status") == "active" and row.get("agent_id")}


async def prepare_plan(plan, rid, hub, run):
    plan = copy.deepcopy(plan)
    steps = {s["id"]: s for s in plan.get("steps", [])}
    proposals = _proposals(plan)
    if not proposals:
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
        if not active and not row["step_ids"]:
            warning = f"{pid}: {reason}; 기존 담당을 유지합니다"
            if warning not in plan.setdefault("warnings", []):
                plan["warnings"].append(warning)
        rows.append({"paper": row.get("paper", ""), "step_ids": row["step_ids"], **receipt})
    plan["recruit_results"] = rows
    return plan


async def run_task(runner, task):
    """A tracked runner task wraps the existing recruiter and announces the roster before its receipt."""
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
        for spec in runner.registry.talent_pool():
            c = spec.contract
            if not c or paper_key(c.paper or c.repo) != pid or c.kind not in {"technician", "full"}:
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
        name = "plan-" + hashlib.sha256(pid.encode()).hexdigest()[:24]
        spec = await paper2agent.Recruitment(runner, {
            **row, "name": name, "request_id": task.request_id, "plan_task_id": task.id,
            "focus": row.get("focus", "") + "\nDeliver a technician/full hire with executable paper tools.",
        }).run()
        runner._send_roster()
        if not spec or not spec.contract or spec.contract.status != "active" or spec.contract.kind not in {"technician", "full"}:
            return {"status": "failed", "error": "기술자형 수습 미통과"}
        return {"status": "active", "agent_id": spec.id, "reused": False}

    try:
        receipt = await asyncio.wait_for(recruit(), timeout=runner.s.runner.task_timeout_s)
    except asyncio.TimeoutError:
        receipt = {"status": "timed_out"}
    except Exception as exc:
        receipt = {"status": "failed", "error": type(exc).__name__}
    return TaskResult(task_id=task.id, agent_id=task.agent_id, ok=True, cost_usd=0,
                      structured=receipt, text="계획 전 채용: " + receipt["status"])
