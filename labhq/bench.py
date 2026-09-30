"""Low-cost LabHQ versus single-session baseline benchmarks."""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from .util import atomic_write_text, free_port, strip_parent_claude_env

REPO = Path(__file__).resolve().parents[1]
BENCH_ROOT = REPO / "bench"
CASES_ROOT = BENCH_ROOT / "cases"
ARMS = ("labhq", "sonnet-max", "sol-ultra", "astra-ultra")
REQUIRED = {"id", "title", "request", "references", "scripted_pi_answers", "check", "budget_usd",
            "mock_answer"}


def _inside(root: Path, relative: str) -> Path:
    target = (root / relative).resolve()
    if target != root.resolve() and root.resolve() not in target.parents:
        raise ValueError(f"benchmark path escapes its root: {relative}")
    return target


def load_case(case_id: str, cases_root: Path | None = None) -> dict[str, Any]:
    root = (cases_root or CASES_ROOT).resolve()
    path = _inside(root, f"{case_id}.yaml")
    if not path.is_file():
        raise KeyError(f"unknown benchmark case {case_id!r}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or REQUIRED - set(data):
        missing = sorted(REQUIRED - set(data or {}))
        raise ValueError(f"invalid benchmark case {case_id}: missing {', '.join(missing)}")
    if data["id"] != case_id or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", case_id):
        raise ValueError(f"case id does not match filename: {case_id}")
    if not isinstance(data["references"], list) or not data["references"]:
        raise ValueError(f"case {case_id} needs reference material")
    if not isinstance(data["scripted_pi_answers"], list) or not data["scripted_pi_answers"]:
        raise ValueError(f"case {case_id} needs scripted PI answers")
    if any(not isinstance(item, dict) or not item.get("question_contains") or not item.get("answer")
           for item in data["scripted_pi_answers"]):
        raise ValueError(f"case {case_id} has an invalid scripted PI answer")
    if not isinstance(data["budget_usd"], (int, float)) or not 0 < data["budget_usd"] <= 100:
        raise ValueError(f"case {case_id} has an invalid budget")
    for reference in data["references"]:
        if not isinstance(reference, dict) or not reference.get("label") or not reference.get("path"):
            raise ValueError(f"case {case_id} has an invalid reference")
        if not _inside(BENCH_ROOT, reference["path"]).is_file():
            raise ValueError(f"case {case_id} reference is missing: {reference['path']}")
    check = data["check"]
    if not isinstance(check, dict) or not check.get("script"):
        raise ValueError(f"case {case_id} needs a deterministic check script")
    if not _inside(BENCH_ROOT, check["script"]).is_file():
        raise ValueError(f"case {case_id} check script is missing")
    return data


def load_cases(cases_root: Path | None = None) -> list[dict[str, Any]]:
    root = cases_root or CASES_ROOT
    cases = [load_case(path.stem, root) for path in root.glob("*.yaml")]
    return sorted(cases, key=lambda case: (int(case.get("order", 999)), case["id"]))


def _prompt(case: dict[str, Any]) -> str:
    sections = [case["request"].strip(), "\n참고 자료(이 실행에서 고정):"]
    total = 0
    for reference in case["references"]:
        body = _inside(BENCH_ROOT, reference["path"]).read_text(encoding="utf-8")
        total += len(body.encode("utf-8"))
        if total > 1_000_000:
            raise ValueError("benchmark references exceed 1 MB")
        source = f"\n출처: {reference['source']}" if reference.get("source") else ""
        sections.append(f"\n### {reference['label']}{source}\n{body.strip()}")
    sections.append("\n산출물은 근거와 검사 가능한 수치를 포함한 Markdown 보고서 하나로 작성한다.")
    return "\n".join(sections) + "\n"


def _default_output() -> Path:
    raw = os.environ.get("LABHQ_BENCH_DIR")
    return Path(raw).expanduser() if raw else Path("~/.labhq/bench").expanduser()


def _selected_arms(settings, arms: tuple[str, ...] | None = None) -> tuple[str, ...]:
    available = ("labhq", *settings.bench.arms)
    selected = available if arms is None else arms
    if not selected or len(set(selected)) != len(selected) or any(arm not in available for arm in selected):
        raise ValueError(f"select unique arms from: {', '.join(available)}")
    return selected


def _staff_mapping(settings, overrides: list[str] | None = None) -> dict[str, str]:
    mapping = dict(settings.bench.staff_model)
    for override in overrides or []:
        source, sep, target = override.partition("=")
        if not sep or not source.strip() or not target.strip() or "=" in target:
            raise ValueError("--staff-model must be FROM=TO")
        mapping[source.strip()] = target.strip()
    return mapping


def _real_commands(case: dict[str, Any], output_root: Path, run_dir: Path | None = None,
                   settings=None, arms: tuple[str, ...] | None = None) -> dict[str, list[str]]:
    from .adapters.claude_code import user_config_isolation
    from .settings import Settings

    settings = settings or Settings()
    prompt = _prompt(case)
    case_dir = run_dir or output_root / case["id"] / "<run-id>"
    budget = f"{float(case['budget_usd']):.2f}"
    claude_env = {**os.environ, **{k: os.path.expandvars(v) for k, v in settings.engines.claude_code.env.items()}}
    # Same expansion as AgentAdapter.run: configs write bins as ${LOCALAPPDATA}/... or ~/...
    expand = lambda value: os.path.expandvars(os.path.expanduser(value))
    claude = [expand(a) for a in (settings.engines.claude_code.bin, *settings.engines.claude_code.prefix_args)]
    codex = [expand(a) for a in (settings.engines.codex.bin, *settings.engines.codex.prefix_args)]
    commands = {}
    for arm in _selected_arms(settings, arms):
        if arm == "labhq":
            config = ["--config", settings.config_path] if settings.config_path else []
            commands[arm] = ["labhq", *config, "bench", "run", case["id"], "--engines", "real",
                             "--output", str(output_root), "--arms", "labhq"]
            commands[arm] += [arg for source, target in settings.bench.staff_model.items()
                              for arg in ("--staff-model", f"{source}={target}")]
            continue
        definition = settings.bench.arms[arm]
        if definition.engine == "claude_code":
            claude_settings = json.dumps(user_config_isolation(claude_env, case_dir / arm))
            commands[arm] = [*claude, "-p", prompt, "--output-format", "stream-json", "--verbose",
                             "--model", definition.model, "--effort", definition.effort,
                             "--max-budget-usd", budget, "--setting-sources", "local",
                             "--disable-slash-commands", "--settings", claude_settings,
                             "--disallowedTools", "Agent", "Task", "SendMessage", "TeamCreate"]
        else:
            commands[arm] = [*codex, "exec", "--json", "--skip-git-repo-check", "-C",
                             str(case_dir / arm), "-s", "workspace-write", "--ephemeral",
                             "--ignore-user-config", "--ignore-rules", "-m", definition.model,
                             "-c", f'model_reasoning_effort="{definition.effort}"',
                             "-o", str(case_dir / arm / "answer.md"), prompt]
    return commands


def print_dry_run(case: dict[str, Any], output_root: Path, settings=None,
                  arms: tuple[str, ...] | None = None) -> None:
    preview_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    preview_dir = output_root / case["id"] / preview_id
    for arm, command in _real_commands(case, output_root, preview_dir, settings, arms).items():
        print(f"[{arm}] {subprocess.list2cmdline(command)}")


async def _until(predicate, timeout: float, message: str) -> None:
    started = time.monotonic()
    while not predicate():
        if time.monotonic() - started > timeout:
            raise TimeoutError(message)
        await asyncio.sleep(0.05)


def _scripted_answer(case: dict[str, Any], summary: str, kind: str | None = None) -> tuple[bool, str] | None:
    if kind != "clarify":
        return None
    lowered = summary.lower()
    answers = case["scripted_pi_answers"]
    selected = next((item for item in answers if str(item["question_contains"]).lower() in lowered), None)
    if selected is None:
        return None
    return bool(selected.get("approved", True)), str(selected["answer"])


async def _run_labhq(case: dict[str, Any], arm_dir: Path, engines: str, base_settings) -> dict[str, Any]:
    import uvicorn

    from .gateway.server import RequestIn, create_app
    from .runner.daemon import Runner
    state = arm_dir / "state"
    shutil.copytree(base_settings.path(base_settings.runner.agents_dir), state / "agents")
    settings = base_settings.model_copy(deep=True)
    staff_models = []
    for path in sorted((state / "agents").rglob("*.yaml")):
        spec = yaml.safe_load(path.read_text(encoding="utf-8"))
        if spec.get("engine") == "claude_code" and spec.get("model") in settings.bench.staff_model:
            spec["model"] = settings.bench.staff_model[spec["model"]]
            path.write_text(yaml.safe_dump(spec, allow_unicode=True, sort_keys=False), encoding="utf-8")
        staff_models.append({"id": spec["id"], "engine": spec.get("engine"), "model": spec.get("model")})
    if settings.recruit.contract_engine == "claude_code":
        settings.recruit.contract_model = settings.bench.staff_model.get(
            settings.recruit.contract_model, settings.recruit.contract_model)
    port = free_port()
    settings.gateway.state_dir = str(state / "gateway")
    settings.gateway.port = port
    settings.gateway.url = f"ws://127.0.0.1:{port}"
    settings.runner.state_dir = str(state / "runner")
    settings.runner.workspace_root = str(state / "runs")
    settings.runner.talent_dir = str(state / "talent")
    settings.runner.agents_dir = str(state / "agents")
    settings.runner.broker_port = free_port()
    settings.runner.job_poll_s = 1
    settings.hpc.scheduler = "mock"
    if engines == "mock":
        settings.runner.force_engine = "mock"
    else:
        settings.runner.force_engine = None

    app = create_app(settings)
    hub = app.state.hub
    interventions = 0
    unscripted_approvals = 0
    original_publish = hub.publish

    async def publish(event: dict, *args, **kwargs) -> None:
        nonlocal interventions, unscripted_approvals
        await original_publish(event, *args, **kwargs)
        if event.get("type") == "approval.requested":
            interventions += 1
            data = event.get("data") or {}
            answer = _scripted_answer(case, str(data.get("summary") or ""), data.get("kind"))
            if answer is None:
                unscripted_approvals += 1
                approved, note = False, "미스크립트 승인: 승인 종류 또는 질문이 스크립트와 일치하지 않아 거절"
            else:
                approved, note = answer

            async def decide() -> None:
                await asyncio.sleep(0.01)
                try:
                    await hub.resolve_approval(event["data"]["id"], approved, note)
                except KeyError:
                    pass

            asyncio.create_task(decide())

    hub.publish = publish
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    server_task = asyncio.create_task(server.serve())
    runner = None
    runner_task = None
    started = time.monotonic()
    try:
        await _until(lambda: server.started, 15, "benchmark gateway did not start")
        runner = Runner(settings)
        runner_task = asyncio.create_task(runner.run_forever())
        await _until(lambda: "cso" in hub.agents, 20, "benchmark runner did not register")
        rid = hub.create_request(RequestIn(text=_prompt(case),
                                           budget_usd=float(case["budget_usd"]),
                                           meta={"case_id": case["id"]}))
        await _until(lambda: hub.requests[rid]["status"] != "running", 900, "benchmark request timed out")
        request = hub.requests[rid]
        record = hub.rounds.write(rid)
        answer = case["mock_answer"].strip() if engines == "mock" else str(request.get("report") or "").strip()
        (arm_dir / "answer.md").write_text(answer + "\n", encoding="utf-8")
        run = {
            "engine": "labhq", "mode": engines, "request_id": rid, "status": request["status"],
            "staff_model": dict(settings.bench.staff_model), "staff_models": staff_models,
            "pi_interventions": interventions, "cost_usd": request.get("cost_usd"),
            "pi_questions_observable": True,
            "unscripted_approvals": unscripted_approvals,
            "cost_known": request.get("cost_known", True), "usage": request.get("usage") or {},
            "duration_s": round(time.monotonic() - started, 3),
            "round_json": str(hub.rounds.directory / f"{rid}.json"),
            "round_status": record["result"]["status"],
        }
        (arm_dir / "run.json").write_text(json.dumps(run, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return run
    finally:
        if runner:
            runner.stop()
        server.should_exit = True
        await asyncio.sleep(0.2)
        if runner_task:
            runner_task.cancel()
            await asyncio.gather(runner_task, return_exceptions=True)
        try:
            await asyncio.wait_for(server_task, 5)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            server_task.cancel()
            await asyncio.gather(server_task, return_exceptions=True)


def _parse_claude(raw: str) -> tuple[str, dict[str, int], float | None]:
    answer, usage, cost = "", {}, None
    for line in raw.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("type") == "result":
            answer = str(event.get("result") or answer)
            cost = event.get("total_cost_usd")
            usage = {key: int(value) for key, value in (event.get("usage") or {}).items()
                     if isinstance(value, int) and "token" in key}
    return answer, usage, cost


def _parse_codex(raw: str, answer_path: Path) -> tuple[str, dict[str, int]]:
    usage: dict[str, int] = {}
    for line in raw.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("type") == "turn.completed":
            for key, value in (event.get("usage") or {}).items():
                if isinstance(value, int) and "token" in key:
                    usage[key] = usage.get(key, 0) + value
    answer = answer_path.read_text(encoding="utf-8") if answer_path.is_file() else ""
    return answer, usage


async def _run_baseline(case: dict[str, Any], arm: str, arm_dir: Path, engines: str,
                        command: list[str], settings) -> dict[str, Any]:
    started = time.monotonic()
    if engines == "mock":
        (arm_dir / "answer.md").write_text(case["mock_answer"].strip() + "\n", encoding="utf-8")
        run = {"engine": arm, "mode": "mock", "status": "done", "pi_interventions": 0,
               "cost_usd": 0.0, "cost_known": True, "usage": {},
               "duration_s": round(time.monotonic() - started, 3)}
    else:
        from .adapters.base import AgentAdapter, _resolve_command

        engine_name = settings.bench.arms[arm].engine
        engine = getattr(settings.engines, engine_name)
        env = strip_parent_claude_env({**os.environ, **{k: os.path.expandvars(v) for k, v in engine.env.items()}})
        if engine_name == "codex":
            from .adapters.base import child_config_dirs

            global_docs = [home / name for home in child_config_dirs(env, arm_dir,
                                                                      "CODEX_HOME", ".codex")
                           for name in ("AGENTS.md", "AGENTS.override.md") if (home / name).is_file()]
            if global_docs:
                raise RuntimeError("Codex baseline refused: global AGENTS instructions would change the comparison")
        command = _resolve_command(command, env, engine_name)
        timeout = float(case.get("timeout_s", settings.runner.task_timeout_s))
        group_args = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
                      if os.name == "nt" else {"start_new_session": True})
        process = await asyncio.create_subprocess_exec(*command, cwd=arm_dir, env=env,
                                                       stdin=asyncio.subprocess.DEVNULL,
                                                       stdout=asyncio.subprocess.PIPE,
                                                       stderr=asyncio.subprocess.PIPE, **group_args)
        drain = asyncio.create_task(process.communicate())
        error = None
        try:
            await asyncio.wait_for(asyncio.shield(drain), timeout)
        except asyncio.TimeoutError:
            error = f"timeout after {timeout:g}s"
        finally:
            # Shield the pipes so timeout, Ctrl+C and cancellation can kill and reap the tree.
            if not drain.done():
                await AgentAdapter._kill(process)
                await asyncio.shield(drain)
        stdout, stderr = drain.result()
        raw = stdout.decode("utf-8", "replace")
        (arm_dir / "events.jsonl").write_text(raw, encoding="utf-8")
        (arm_dir / "stderr.txt").write_text(stderr.decode("utf-8", "replace"), encoding="utf-8")
        if engine_name == "claude_code":
            answer, usage, cost = _parse_claude(raw)
            (arm_dir / "answer.md").write_text(answer.strip() + "\n", encoding="utf-8")
            known = cost is not None
        else:
            answer, usage = _parse_codex(raw, arm_dir / "answer.md")
            cost, known = None, False
        run = {"engine": arm, "mode": "real", "status": "done" if process.returncode == 0 and not error else "failed",
               "returncode": process.returncode, "pi_interventions": 0, "cost_usd": cost,
               "cost_known": known, "usage": usage, "duration_s": round(time.monotonic() - started, 3)}
        if error:
            run["error"] = error
    run["pi_questions_observable"] = False  # Noninteractive CLI: no question/answer channel.
    definition = settings.bench.arms[arm]
    run.update(model=definition.model, effort=definition.effort, cli_engine=definition.engine)
    (arm_dir / "run.json").write_text(json.dumps(run, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return run


async def _score(case: dict[str, Any], arm_dir: Path, run: dict[str, Any]) -> dict[str, Any]:
    answer = arm_dir / "answer.md"
    command = [sys.executable, str(_inside(BENCH_ROOT, case["check"]["script"])), str(arm_dir),
               *[str(arg) for arg in case["check"].get("args", [])]]
    checked = await asyncio.to_thread(subprocess.run, command, capture_output=True, text=True, encoding="utf-8",
                                      errors="replace", timeout=30,
                                      env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    if checked.returncode not in {0, 1}:
        raise ValueError("benchmark checker error: " + (checked.stdout + checked.stderr).strip())
    usage = run.get("usage") or {}
    if isinstance(usage.get("total_tokens"), int):
        token_total = usage["total_tokens"]
    else:
        keys = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
        token_total = sum(usage.get(key, 0) for key in keys if isinstance(usage.get(key, 0), int))
    return {
        "engine": run["engine"], "status": run["status"], "error": run.get("error"),
        "model": run.get("model"), "effort": run.get("effort"), "mode": run.get("mode"),
        "cli_engine": run.get("cli_engine"),
        "staff_model": run.get("staff_model"), "staff_models": run.get("staff_models"),
        "artifact_exists": answer.is_file() and bool(answer.read_text(encoding="utf-8").strip()),
        "checks_passed": checked.returncode == 0, "check_output": (checked.stdout + checked.stderr).strip(),
        "check": case["check"],
        "pi_interventions": int(run.get("pi_interventions") or 0), "cost_usd": run.get("cost_usd"),
        "pi_questions_observable": bool(run.get("pi_questions_observable", run["engine"] == "labhq")),
        "unscripted_approvals": int(run.get("unscripted_approvals") or 0),
        "cost_known": bool(run.get("cost_known")),
        "token_total": token_total,
        "usage": usage, "duration_s": run.get("duration_s"),
        "within_budget": None if run.get("cost_usd") is None else
                         float(run["cost_usd"]) <= float(case["budget_usd"]),
    }


def _comparison_markdown(result: dict[str, Any]) -> str:
    lines = [f"# Bench · {result['case_id']}", "",
             "| arm | model | effort | mode | 상태 | 산출물 | 검사 | PI 개입 | PI 질문 | 미스크립트 승인 | 비용(USD) | 상한 | tokens | 경과(초) |",
             "|---|---|---|---|---|---:|---:|---:|---|---:|---:|---:|---:|---:|"]
    for row in result["rows"]:
        cost = "미집계" if row["cost_usd"] is None else f"{row['cost_usd']:.4f}"
        cap = "미집계" if row["within_budget"] is None else "PASS" if row["within_budget"] else "FAIL"
        pi_questions = "감지 가능" if row.get("pi_questions_observable") else "감지 불가·답변 미제공"
        model = row.get("model") or "; ".join(
            f"{staff['id']}={staff.get('model') or 'default'}" for staff in row.get("staff_models") or []) or "—"
        lines.append(f"| {row['engine']} | {model} | {row.get('effort') or 'staff config'} | "
                     f"{row.get('mode') or result['mode']} | {row['status']} | {'OK' if row['artifact_exists'] else 'FAIL'} | "
                     f"{'PASS' if row['checks_passed'] else 'FAIL'} | {row['pi_interventions']} | "
                     f"{pi_questions} | "
                     f"{row.get('unscripted_approvals', 0)} | {cost} | "
                     f"{cap} | {row['token_total']} | {row['duration_s']:.3f} |")
    failures = [row for row in result["rows"] if row.get("error")]
    if failures:
        lines += ["", "## 실패 원인", *[f"- {row['engine']}: {row['error']}" for row in failures]]
    return "\n".join(lines) + "\n"


def _write_comparison(directory: Path, result: dict[str, Any]) -> None:
    for name, body in (("comparison.json", json.dumps(result, ensure_ascii=False, indent=2) + "\n"),
                       ("comparison.md", _comparison_markdown(result))):
        atomic_write_text(directory / name, body)


def report_case(case_id: str, output_root: Path, engines: str = "real") -> dict[str, Any]:
    """Use saved scores, never today's model definitions, to compare latest runs per arm."""
    case = load_case(case_id)
    directory = Path(output_root).expanduser().resolve() / case_id
    latest = {}
    for path in sorted(directory.glob("*/*/score.json")):
        row = json.loads(path.read_text(encoding="utf-8"))
        if row["mode"] == engines:
            latest[row["engine"]] = row
    if not latest:
        raise ValueError(f"no {engines} benchmark results for {case_id}")
    order = [*ARMS, *sorted(set(latest) - set(ARMS))]
    result = {"schema_version": 2, "case_id": case_id, "title": case["title"],
              "mode": engines, "rows": [latest[arm] for arm in order if arm in latest]}
    _write_comparison(directory, result)
    return result


async def rescore_case(case_id: str, output_root: Path, run_id: str | None = None,
                       all_runs: bool = False) -> list[dict[str, Any]]:
    """Recheck saved artifacts without running arms; default to the latest run."""
    case = load_case(case_id)
    if run_id is not None and all_runs:
        raise ValueError("--run-id and --all are mutually exclusive")
    directory = Path(output_root).expanduser().resolve() / case_id
    if run_id is not None:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", run_id):
            raise ValueError("invalid benchmark run id")
        candidates = [_inside(directory, run_id)]
    else:
        candidates = sorted(path for path in directory.iterdir() if path.is_dir() and
                            any(path.glob("*/run.json"))) if directory.is_dir() else []
        if not all_runs:
            candidates = candidates[-1:]
    if not candidates:
        raise ValueError(f"no saved benchmark runs for {case_id}")

    # Read and score everything first. A missing record or checker error must not
    # leave a half-rescored selection; execution metrics remain in run.json.
    updates = []
    for run_dir in candidates:
        run_dir = _inside(directory, run_dir.name)
        records = sorted(run_dir.glob("*/run.json"))
        if not records:
            raise ValueError(f"no saved arms for run {run_dir.name}")
        rows = []
        modes = set()
        for record in records:
            record = _inside(directory, str(record.relative_to(directory)))
            arm_dir = record.parent
            for filename in ("answer.md", "score.json"):
                _inside(directory, str((arm_dir / filename).relative_to(directory)))
            run = json.loads(record.read_text(encoding="utf-8"))
            if run.get("mode") not in {"real", "mock"}:
                raise ValueError(f"invalid saved mode for run {run_dir.name}")
            modes.add(run["mode"])
            score_path = arm_dir / "score.json"
            old = json.loads(score_path.read_text(encoding="utf-8")) if score_path.is_file() else None
            row = dict(old or {})
            checked = await _score(case, arm_dir, run)
            # Only grading changes. Preserve saved model, usage, cost and status.
            if old is None:
                row.update(checked)
            else:
                row.update({key: checked[key] for key in
                            ("artifact_exists", "checks_passed", "check_output", "check")})
            row["run_id"] = run_dir.name
            row["rescored_at"] = datetime.now(timezone.utc).isoformat()
            history = list(row.get("score_history") or [])
            if old is not None:
                history.append({"rescored_at": row["rescored_at"],
                                "score": {key: value for key, value in old.items()
                                          if key != "score_history"}})
            row["score_history"] = history
            rows.append((score_path, row))
        if len(modes) != 1:
            raise ValueError(f"mixed modes in saved run {run_dir.name}")
        result = {"schema_version": 2, "case_id": case_id, "run_id": run_dir.name,
                  "title": case["title"], "budget_usd": float(case["budget_usd"]),
                  "mode": modes.pop(), "rows": [row for _, row in rows]}
        updates.append((run_dir, rows, result))

    # Keep the aggregate's selected real/mock view when both were rescored.
    comparison_path = directory / "comparison.json"
    selected_mode = (json.loads(comparison_path.read_text(encoding="utf-8")).get("mode")
                     if comparison_path.is_file() else None)
    for run_dir, rows, result in updates:
        for score_path, row in rows:
            atomic_write_text(score_path, json.dumps(row, ensure_ascii=False, indent=2) + "\n")
        _write_comparison(run_dir, result)
    modes = {result["mode"] for _, _, result in updates}
    for mode in sorted(modes, key=lambda mode: mode == selected_mode):
        report_case(case_id, output_root, mode)
    return [result for _, _, result in updates]


async def run_case(case_id: str, output_root: Path, engines: str = "real",
                   arms: tuple[str, ...] | None = None, settings=None) -> dict[str, Any]:
    from .settings import Settings

    if engines not in {"real", "mock"}:
        raise ValueError("engines must be real or mock")
    case = load_case(case_id)
    settings = settings or Settings()
    arms = _selected_arms(settings, arms)
    output_root = Path(output_root).expanduser().resolve()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    case_dir = output_root / case_id / run_id
    case_dir.mkdir(parents=True, exist_ok=False)
    commands = _real_commands(case, output_root, case_dir, settings, arms)
    rows = []
    for arm in arms:
        arm_dir = case_dir / arm
        arm_dir.mkdir(parents=True, exist_ok=True)
        started = time.monotonic()
        try:
            run = await (_run_labhq(case, arm_dir, engines, settings) if arm == "labhq" else
                         _run_baseline(case, arm, arm_dir, engines, commands[arm], settings))
        except Exception as exc:
            run = {"engine": arm, "mode": engines, "status": "failed", "pi_interventions": 0,
                   "cost_usd": None, "cost_known": False, "usage": {},
                   "duration_s": round(time.monotonic() - started, 3),
                   "error": f"{type(exc).__name__}: {exc}"}
        run["mode"] = engines
        if arm == "labhq":
            run["staff_model"] = dict(settings.bench.staff_model)
        else:
            definition = settings.bench.arms[arm]
            run.update(model=definition.model, effort=definition.effort, cli_engine=definition.engine)
        (arm_dir / "run.json").write_text(json.dumps(run, ensure_ascii=False, indent=2) + "\n",
                                           encoding="utf-8")
        row = await _score(case, arm_dir, run)
        row["run_id"] = run_id
        atomic_write_text(arm_dir / "score.json", json.dumps(row, ensure_ascii=False, indent=2) + "\n")
        rows.append(row)
    result = {"schema_version": 2, "case_id": case_id, "run_id": run_id, "title": case["title"],
              "budget_usd": float(case["budget_usd"]), "mode": engines, "rows": rows}
    _write_comparison(case_dir, result)
    report_case(case_id, output_root, engines)
    return result


async def run_test_agent(output_root: Path, engines: str = "real", settings=None,
                          arms: tuple[str, ...] | None = None) -> dict[str, Any]:
    from .settings import Settings

    settings = settings or Settings()
    arms = _selected_arms(settings, arms)
    output_root = Path(output_root).expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    cases = []
    for case in load_cases():
        result = await run_case(case["id"], output_root, engines=engines, settings=settings, arms=arms)
        passed = all(row["status"] == "done" and row["artifact_exists"] and row["checks_passed"] and
                     row["within_budget"] is not False and not row.get("unscripted_approvals")
                     for row in result["rows"])
        cases.append({"case_id": case["id"], "run_id": result["run_id"], "passed": passed})
    summary = {"schema_version": 1, "mode": engines, "passed": sum(c["passed"] for c in cases),
               "failed": sum(not c["passed"] for c in cases), "cases": cases}
    (output_root / "test-agent-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
                                                          encoding="utf-8")
    lines = ["# Bench test agent", "", "| case | 결과 |", "|---|---|"]
    lines += [f"| {case['case_id']} | {'PASS' if case['passed'] else 'FAIL'} |" for case in cases]
    (output_root / "test-agent-summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return summary


def run_cli(args) -> None:
    from .settings import Settings

    output = Path(args.output).expanduser() if getattr(args, "output", None) else _default_output()
    if args.bench_cmd == "list":
        for case in load_cases():
            print(f"{case['id']:<28} ${float(case['budget_usd']):.2f}  {case['title']}")
        return
    if args.bench_cmd == "report":
        print(_comparison_markdown(report_case(args.case_id, output, args.engines)), end="")
        return
    if args.bench_cmd == "rescore":
        results = asyncio.run(rescore_case(args.case_id, output, args.run_id, args.all))
        for result in results:
            print(f"rescored: {result['run_id']}")
            print(_comparison_markdown(result), end="")
        return
    settings = Settings.load(args.config)
    settings.bench.staff_model = _staff_mapping(settings, getattr(args, "staff_model", None))
    selection = getattr(args, "arms", None)
    if selection is None:
        selection = getattr(args, "arm", None)
    arms = _selected_arms(settings, tuple(selection.split(",")) if selection is not None else None)
    if args.bench_cmd == "run":
        case = load_case(args.case_id)
        if args.dry_run:
            print_dry_run(case, output, settings, arms)
            return
        asyncio.run(run_case(args.case_id, output, engines=args.engines, arms=arms, settings=settings))
        print(_comparison_markdown(report_case(args.case_id, output, args.engines)), end="")
        return
    summary = asyncio.run(run_test_agent(output, engines=args.engines, settings=settings, arms=arms))
    print(f"bench test agent: PASS {summary['passed']} / FAIL {summary['failed']}")
    if summary["failed"]:
        raise SystemExit(1)
