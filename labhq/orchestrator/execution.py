"""One compute location for the lifetime of a request (#430)."""

import re
from pathlib import PurePosixPath

from ..util import output_relpath

QUESTION = "이 요청을 HPC에서 처음부터 끝까지 할까요, 이 PC에서 할까요?"
OPTIONS = [
    "HPC: 큐 대기 시간이 있고 서버 사용료가 들 수 있습니다. 데이터와 중간 결과는 HPC에 둡니다.",
    "이 PC: 큐 대기 없이 시작하지만 큰 작업은 오래 걸립니다. PC 자원을 쓰고 데이터도 이 PC에 둡니다.",
]


def available_runner(hub, agent_id=None, request_id=None):
    """Only an online, built-in scheduler with a processing worker can own the request."""
    request_agents = getattr(hub, "request_agents", None)
    view = request_agents(request_id) if callable(request_agents) else hub.agents
    agents = view.items() if agent_id is None else ((agent_id, view.get(agent_id, {})),)
    cfg = hub.s.orchestrator
    excluded = {"cso", "chief_of_staff", "sci_reviewer", "editor", "facilities",
                cfg.cso_agent, cfg.chief_of_staff_agent, cfg.reviewer_agent}
    for aid, agent in agents:
        if agent_id is None and aid in excluded:
            continue
        request_runner = getattr(hub, "request_runner", None)
        host = (request_runner(aid, request_id) if callable(request_runner) else
                getattr(hub, "agent_runner", {}).get(aid) or agent.get("runner_id"))
        caps = getattr(hub, "runner_capabilities", {}).get(host, {})
        if (host in getattr(hub, "runners", {}) and caps.get("scheduler") in {"sge", "pbs", "slurm", "mock"}
                and agent.get("whole_request_hpc") is True
                and agent.get("hpc_tools") and "hpc" in agent.get("builtin_mcp", [])):
            return host
    return None


def selection(note):
    choices = {mode for mode, option in zip(("hpc", "local"), OPTIONS) if option in note}
    # The approve CLI documents `--note "1: a"`; accept only an unambiguous answer to this one question.
    short = re.fullmatch(r"\s*(?:1\s*:\s*)?([ab12])\s*[.)]?\s*", note, re.I)
    if short:
        choices.add("hpc" if short[1].lower() in {"a", "1"} else "local")
    return next(iter(choices)) if len(choices) == 1 else None


def plan_rule(choice):
    mode = choice["mode"]
    if mode == "local":
        return "\n\nExecution location fixed by this request: local. Every step execution=local. No HPC jobs."
    return ("\n\nExecution location fixed by the PI: HPC for the WHOLE request. Every DAG step must declare "
            "execution=hpc and use a worker with built-in hpc tools on runner " + choice["runner_id"] + ". "
            "All download, preparation, QC, analysis and figure/report-file generation runs in scheduler jobs "
            "in isolated steps/<step-id>/ workspaces. Use hpc_submit; job cwd is this step's outputs/, so strip outputs/ from output "
            "paths inside scripts. Submit, end the turn and resume after the job finishes. "
            "Local staff only write/submit scripts and read permitted summaries. "
            "Never process data locally. Read only the runner's hash-verified ancestor snapshots in request data/<producer-step>/; "
            "never read sibling workspaces. Different steps may declare identical output names. "
            "Runner-root .venv environment_binding(s) are unsupported on HPC and will be refused before dispatch. "
            "Keep any job-local environment under this step's outputs/ and record its versions there. "
            "Existing project/reference files also require a scheduler-readable request-input snapshot; "
            "stored precedent full texts are likewise unsupported until that snapshot contract exists. "
            "such requests are refused before processing until that snapshot contract exists. "
            "The CSO report and request bundle collect results only at the end.")


def input_errors(metadata, choice):
    if choice and choice.get("mode") == "hpc" and metadata.get("precedent_texts"):
        return ["HPC precedent texts require a verified scheduler-readable precedent snapshot; "
                "processing refused before dispatch"]
    if choice and choice.get("mode") == "hpc" and (metadata.get("project_dirs") or metadata.get("reference_dirs")):
        return ["HPC request inputs require a verified scheduler-readable request-input snapshot; "
                "processing refused before dispatch"]
    return []


def plan_errors(steps, choice, agents):
    if not choice:
        return []  # historical plans keep their frozen hash and execution contract
    errors = []
    if not isinstance(steps, list):
        return ["execution plan steps must be a list"]
    for step in steps or []:
        if not isinstance(step, dict):
            errors.append("execution plan step must be an object")
            continue
        mode = step.get("execution") or "local"
        if mode != choice["mode"]:
            errors.append(f"{step.get('id')}: execution must be {choice['mode']}; mixed plans are refused")
        if choice["mode"] == "hpc":
            outputs = []
            if (step.get("environment_binding") or step.get("environment_bindings")
                    or any(output_relpath(path) == "outputs/env/requirements.lock.txt"
                           for path in step.get("outputs") or [])):
                errors.append(f"{step.get('id')}: HPC environment bindings require a verified scheduler-readable "
                              "environment snapshot; plan refused before dispatch")
            agent = agents.get(step.get("agent_id"), {})
            if (agent.get("runner_id") != choice["runner_id"] or not agent.get("hpc_tools")
                    or agent.get("whole_request_hpc") is not True
                    or "hpc" not in agent.get("builtin_mcp", [])):
                errors.append(f"{step.get('id')}: requires whole-request HPC eligibility and built-in tools on the selected runner")
            for path in [*(step.get("outputs") or []), *(step.get("optional_outputs") or [])]:
                path = output_relpath(path)
                if not path:
                    errors.append(f"{step.get('id')}: HPC outputs must stay in outputs/")
                    continue
                parts = PurePosixPath(path.casefold()).parts
                if any(parts[:len(old)] == old or old[:len(parts)] == parts for old in outputs):
                    errors.append(f"{step.get('id')}: step workspace output collision: {path}")
                outputs.append(parts)
    return errors


def record_line(choice):
    label = "HPC" if choice["mode"] == "hpc" else "이 PC"
    return f"실행 위치: {label} · 요청 전체에 적용 · 선택 근거: {choice['source']}"
