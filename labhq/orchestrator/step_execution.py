"""Bind versioned research steps to the existing request-wide execution route."""

from ..research.contract import plan_sha256


_PRE_DISPATCH_PROOFS = ("reuse_tools", "reuse_no_request_inputs", "reuse_input_proof")
_EXECUTION_RECORDS = ("results", "step_decisions", "pending_revisions", *_PRE_DISPATCH_PROOFS)


class ReapprovalRequired(RuntimeError):
    """The first research dispatch was withheld because the approved runners changed."""


class ReplanRequired(RuntimeError):
    """Changing runner assignments would invalidate existing evidence or reuse approval."""


class FirstDispatch:
    """Check dispatch placement without changing a plan whose execution has begun."""

    def __init__(self, request):
        self.plan = request["plan"]
        self.can_reapprove = not (any(request.get(key) for key in _EXECUTION_RECORDS)
                                  or (request.get("research_contract") or {}).get("continuation"))
        self.needs_reapproval = False

    def before_send(self, choice, agents):
        if self.needs_reapproval or bind_draft(self.plan, choice, agents, refresh_runner=True) != self.plan:
            if not self.can_reapprove:
                raise ReplanRequired("approved runner assignments changed; revise the plan and obtain a new CP1")
            # Once stale, no sibling can start under this approval, even if the roster changes back.
            self.needs_reapproval = True
            raise ReapprovalRequired("step runners changed before dispatch; renewed CP1 required")
        self.can_reapprove = False

    def discard_unsent_proofs(self, request):
        # Only a stale first-dispatch guard can abandon preparation; no research step entered dispatch.
        for key in _PRE_DISPATCH_PROOFS:
            request.pop(key, None)


def can_offer(choice, roster):
    """Offer the opt-in after an explicit execution choice with a worker on that route."""
    return bool(choice and choice.get("approval_id") and any(
        worker.get("runner_id") and (choice["mode"] == "local" or worker["runner_id"] == choice["runner_id"])
        for worker in roster
    ))


def bind_draft(plan, choice, agents, *, refresh_runner=False):
    """Resolve placement before CP1 and refresh the server-owned runner after recruitment."""
    if plan.get("step_execution_version") is None or not choice or not isinstance(plan.get("steps"), list):
        return plan
    return {**plan, "steps": [
        {**step,
         "execution": choice["mode"] if step.get("execution") is None else step["execution"],
         "runner_id": (agents.get(step["agent_id"], {}).get("runner_id")
                       if refresh_runner or step.get("runner_id") is None else step["runner_id"])}
        if isinstance(step, dict) and isinstance(step.get("agent_id"), str) else step
        for step in plan["steps"]
    ]}


def placement_errors(steps, choice, agents):
    """A versioned plan must use the request's mode and each worker's current runner."""
    if not choice:
        return ["versioned step execution requires a request execution location"]
    if not isinstance(steps, list):
        return ["execution plan steps must be a list"]
    errors = []
    for step in steps:
        if not isinstance(step, dict):
            errors.append("execution plan step must be an object")
            continue
        sid, runner, agent = step.get("id"), step.get("runner_id"), step.get("agent_id")
        if step.get("execution") != choice["mode"]:
            errors.append(f"{sid}: execution must be {choice['mode']}; mixed plans are refused")
        if not isinstance(runner, str) or not runner.strip():
            errors.append(f"{sid}: versioned step execution requires runner_id")
        elif not isinstance(agent, str) or agents.get(agent, {}).get("runner_id") != runner:
            errors.append(f"{sid}: runner_id differs from the assigned worker's runner")
    return errors


def refresh_for_approval(plan, request, choice, agents):
    """Refresh the server's runner assignments before any research step has run."""
    refreshed = bind_draft(plan, choice, agents, refresh_runner=True)
    changed = refreshed != plan
    if changed and any(request.get(key) for key in _EXECUTION_RECORDS):
        raise ValueError("step runner changed after execution began; revise the plan to preserve the original evidence")
    return refreshed, changed


def require_approved_placement(plan, steps, contract):
    if plan.get("step_execution_version") is None:
        return
    approval = contract.get("approval") or {}
    if (steps != plan.get("steps") or approval.get("approved") is not True
            or approval.get("target_sha256") != contract.get("plan_sha256")
            or approval.get("target_sha256") != plan_sha256(plan)):
        raise ValueError("step execution differs from the approved CP1 plan; renewed approval required")


def runner_pin(plan, step, environment_pin):
    if plan.get("step_execution_version") is None:
        return environment_pin
    runner = step["runner_id"]
    if environment_pin and environment_pin != runner:
        raise ValueError(f"{step['id']}: environment runner differs from the approved step runner")
    return runner


def card_detail(plan):
    if plan.get("step_execution_version") is None:
        return {}
    return {"단계 실행 위치 · runner": "\n".join(
        f"{step['id']} · {step['execution']} · {step['runner_id']}" for step in plan["steps"]
    )}


def upstream_inputs(results, step_ids):
    """Dispatch the same hash-bound inputs for a DAG turn and a report handoff."""
    artifacts = {sid: {"task_id": result.task_id, "workdir": result.workdir,
                       "outputs": list(result.outputs), "output_sha256": dict(result.output_sha256)}
                 for sid in step_ids if (result := results.get(sid)) and result.ok and result.workdir and result.outputs}
    return {"upstream_artifacts": artifacts, "upstream_dirs": [row["workdir"] for row in artifacts.values()],
            "upstream_steps": {sid: row["workdir"] for sid, row in artifacts.items()}}
