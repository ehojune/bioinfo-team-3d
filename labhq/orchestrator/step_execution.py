"""Bind versioned research steps to the runners approved at CP1."""

import json
import re

from ..intake import reference_dirs
from ..research.contract import plan_sha256
from ..wsl_paths import shared_path


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
    """Validate the step routes and the workspace boundary for mixed dependencies."""
    if not choice:
        return ["versioned step execution requires a request execution location"]
    if not isinstance(steps, list):
        return ["execution plan steps must be a list"]
    errors = []
    mixed = is_mixed({'step_execution_version': 1, 'steps': steps}, choice)
    for step in steps:
        if not isinstance(step, dict):
            errors.append("execution plan step must be an object")
            continue
        sid, runner, agent = step.get("id"), step.get("runner_id"), step.get("agent_id")
        mode = step.get("execution")
        if mode != choice["mode"] and not (mixed and mode == 'hpc'):
            errors.append(f"{sid}: execution must be {choice['mode']}; mixed plans are refused")
        if not isinstance(runner, str) or not runner.strip():
            errors.append(f"{sid}: versioned step execution requires runner_id")
        elif not isinstance(agent, str) or agents.get(agent, {}).get("runner_id") != runner:
            errors.append(f"{sid}: runner_id differs from the assigned worker's runner")
    if errors or not mixed:
        return errors
    if not any(step['execution'] == 'local' for step in steps):
        return ['A PC-default mixed plan must include a PC step']
    if any(not isinstance(step.get('id'), str) or not isinstance(step.get('depends_on', []), list)
           or any(not isinstance(parent, str) for parent in step.get('depends_on', [])) for step in steps):
        return ['invalid mixed execution dependency graph']
    by_id = {step['id']: step for step in steps}
    for sid, step in by_id.items():
        pending = list(step.get('depends_on') or [])
        visited = set()
        while pending:
            parent = pending.pop()
            if parent in visited or parent not in by_id:
                continue  # graph validation reports missing steps and cycles
            visited.add(parent)
            ancestor = by_id[parent]
            if step['execution'] == 'hpc' and ancestor['execution'] == 'local':
                errors.append(f'{sid}: PC-to-HPC dependency on {parent} is unsupported')
            if step['execution'] == 'local' and ancestor['execution'] == 'hpc' and (
                    step['runner_id'] != ancestor['runner_id']):
                errors.append(f'{sid}: HPC-to-PC dependency on {parent} requires the same runner')
            pending.extend(ancestor.get('depends_on') or [])
    return errors


def is_mixed(plan, choice):
    steps = plan.get('steps')
    return bool(plan.get('step_execution_version') == 1 and choice and choice.get('mode') == 'local'
                and choice.get('approval_id') and choice.get('mixed_hpc_runner_id') and isinstance(steps, list)
                and any(isinstance(step, dict) and step.get('execution') == 'hpc'
                        for step in steps))


def step_location(choice, plan, step, request_id):
    if not is_mixed(plan, choice):
        return choice
    return {'mode': step['execution'], 'scope': 'step', 'runner_id': step['runner_id'], 'workspace': request_id}


def task_location(request, task):
    choice, plan = request.get('execution_location'), request.get('plan') or {}
    submitted = task.meta.get('execution_location') or {}
    if task.meta.get('kind') not in {'step', 'result_correction', 'slot_repair', 'wrap_up'} or not is_mixed(plan, choice):
        if submitted.get('scope') == 'step':
            raise ValueError('mixed task has no approved mixed plan')
        return choice
    require_approved_placement(plan, plan['steps'], request.get('research_contract') or {})
    step = next((step for step in plan['steps'] if step['id'] == task.meta.get('step_id')), None)
    if not step or step['agent_id'] != task.agent_id or step['runner_id'] != task.meta.get('session_runner'):
        raise ValueError('task does not match its approved step and runner')
    location = step_location(choice, plan, step, task.request_id)
    if location != task.meta.get('execution_location'):
        raise ValueError('task execution location differs from the approved step')
    return location


def reuse_location(choice, plan, request_id):
    if is_mixed(plan, choice):
        return {'mode': 'hpc', 'runner_id': choice['mixed_hpc_runner_id'], 'workspace': request_id}
    return choice or {}


def mixed_hpc(location):
    return bool(location and location.get('scope') == 'step' and location.get('mode') == 'hpc')


def hpc_ancestor_meta(location, linked, by_id, results):
    if not location or location.get('scope') != 'step' or location.get('mode') != 'local':
        return {}
    revisions = {sid: results[sid].provenance.get('hpc_step_revision') for sid in linked
                 if by_id[sid]['execution'] == 'hpc' and sid in results and results[sid].ok
                 and (results[sid].outputs or by_id[sid].get('outputs'))}
    return {'hpc_ancestor_revisions': revisions} if revisions else {}


def reuse_input_request(task, request):
    if mixed_hpc(task.meta.get('execution_location')):
        return {}
    return request


def isolate_hpc_task(task, request):
    """Keep PC path inputs out of the wire metadata and the text sent to a mixed HPC step."""
    if not mixed_hpc(task.meta.get('execution_location')):
        return task
    paths = set()
    for path in (*reference_dirs(request.get('references')), *(request.get('project_dirs') or []),
                 *(task.meta.get('reference_dirs') or []), *(task.meta.get('project_dirs') or [])):
        if not isinstance(path, str) or not path:
            continue
        aliases = {path, path.replace('\\', '/')}
        try:
            canonical = shared_path(path, 'windows')
        except ValueError:
            pass
        else:
            aliases.update((canonical, canonical.replace('/', '\\'), shared_path(path, 'wsl')))
        paths.update(aliases)
        paths.update(json.dumps(alias)[1:-1] for alias in aliases)
    ordered = sorted(paths, key=lambda path: (-len(path), path))
    pattern = re.compile('|'.join(map(re.escape, ordered)), re.I) if paths else None

    def scrub(value):
        return pattern.sub('[PC input withheld]', value) if pattern and isinstance(value, str) else value

    excluded = {'reference_dirs', 'project_dirs', 'precedent_texts', 'hpc_input_approval', 'references'}
    text_fields = {'request', 'instruction', 'title'}
    meta = {key: scrub(value) if key in text_fields else value for key, value in task.meta.items()
            if key not in excluded}
    return task.model_copy(update={'meta': meta, 'prompt': scrub(task.prompt), 'context': scrub(task.context)})


def describe_schema(schema, choice):
    option = schema.get('properties', {}).get('step_execution_version')
    if option and choice and choice.get('mixed_hpc_runner_id'):
        # research_plan_schema returns a fresh option only when offering versioned placement.
        option['description'] = 'Use 1 to bind each step to its approved location and runner, including in mixed plans.'
    return schema


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
