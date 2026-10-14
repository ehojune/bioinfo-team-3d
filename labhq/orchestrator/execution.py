"""One compute location for the lifetime of a request (#430)."""

from ..util import output_relpath

QUESTION = "이 요청을 HPC에서 처음부터 끝까지 할까요, 이 PC에서 할까요?"
OPTIONS = [
    "HPC: 큐 대기 시간이 있고 서버 사용료가 들 수 있습니다. 데이터와 중간 결과는 HPC에 둡니다.",
    "이 PC: 큐 대기 없이 시작하지만 큰 작업은 오래 걸립니다. PC 자원을 쓰고 데이터도 이 PC에 둡니다.",
]


def available_runner(hub):
    """Only an online, built-in scheduler with a processing worker can own the request."""
    for aid, agent in hub.agents.items():
        host = getattr(hub, "agent_runner", {}).get(aid) or agent.get("runner_id")
        caps = getattr(hub, "runner_capabilities", {}).get(host, {})
        if (host in getattr(hub, "runners", {}) and caps.get("scheduler") in {"sge", "pbs", "slurm", "mock"}
                and agent.get("hpc_tools") and "hpc" in agent.get("builtin_mcp", [])):
            return host
    return None


def selection(note):
    # The existing clarify card composes "1. question\na) option"; do not infer a choice from prose.
    choices = [mode for mode, option in zip(("hpc", "local"), OPTIONS) if option in note]
    return choices[0] if len(choices) == 1 else None


def plan_rule(choice):
    mode = choice["mode"]
    if mode == "local":
        return "\n\nExecution location fixed by this request: local. Every step execution=local. No HPC jobs."
    return ("\n\nExecution location fixed by the PI: HPC for the WHOLE request. Every DAG step must declare "
            "execution=hpc and use a worker with built-in hpc tools on runner " + choice["runner_id"] + ". "
            "All download, preparation, QC, analysis and figure/report-file generation runs in scheduler jobs "
            "in ONE request workspace. Use hpc_submit; job cwd is outputs/, so strip outputs/ from output "
            "paths inside scripts. Submit, end the turn and resume after the job finishes. "
            "Local staff only write/submit scripts and read permitted summaries. "
            "Never process data locally or copy upstream data between steps. Declare distinct output paths "
            "outputs/<step-id>/<file> across steps. The CSO report and request bundle collect results only at the end.")


def plan_errors(steps, choice, agents):
    if not choice:
        return []  # historical plans keep their frozen hash and execution contract
    errors, outputs = [], set()
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
            agent = agents.get(step.get("agent_id"), {})
            if (agent.get("runner_id") != choice["runner_id"] or not agent.get("hpc_tools")
                    or "hpc" not in agent.get("builtin_mcp", [])):
                errors.append(f"{step.get('id')}: requires built-in HPC tools on the selected runner")
            for path in step.get("outputs") or []:
                path = output_relpath(path)
                if not path:
                    errors.append(f"{step.get('id')}: HPC outputs must stay in outputs/")
                    continue
                if path in outputs:
                    errors.append(f"{step.get('id')}: shared workspace output collision: {path}")
                outputs.add(path)
    return errors


def record_line(choice):
    label = "HPC" if choice["mode"] == "hpc" else "이 PC"
    return f"실행 위치: {label} · 요청 전체에 적용 · 선택 근거: {choice['source']}"
