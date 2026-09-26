"""Manual real-CLI capture. Never run from CI; raw output stays outside the repository."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from labhq.adapters import get_adapter
from labhq.adapters.base import RunContext
from labhq.models import AgentSpec, Engine, Task
from labhq.settings import Settings
from scripts.redact_stream import Redactor, redact_file


async def _emit(_kind: str, _data: dict) -> None:
    pass


def main() -> None:
    p = argparse.ArgumentParser(description="Manually capture one real engine stream in a temporary workspace")
    p.add_argument("engine", choices=["claude_code", "codex", "gemini", "antigravity"])
    p.add_argument("--model", help="Model slug; uses CLI default if omitted")
    p.add_argument("--output-dir", type=Path, required=True, help="Local directory for raw stdout/stderr; keep outside Git")
    p.add_argument("--redact", action="store_true", help="Also write sanitized .jsonl and .stderr.txt")
    p.add_argument("--timeout", type=int, default=120, help="Seconds, passed through to the adapter")
    p.add_argument("--prompt", default="Reply with exactly LABHQ_P1_OK", help="Task prompt")
    p.add_argument("--name", help="Output file stem (default: engine)")
    args = p.parse_args()
    settings = Settings()
    settings.runner.task_timeout_s = args.timeout
    agent = AgentSpec(id="probe", name="Probe", role="CLI verification", engine=Engine(args.engine),
                      model=args.model, builtin_mcp=[], system_prompt="Answer the small test exactly.")
    task = Task(agent_id="probe", prompt=args.prompt)
    out = args.output_dir.resolve()
    if out.is_relative_to(Path(__file__).resolve().parents[1]):
        p.error("--output-dir must be outside the public repository")
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="labhq-probe-") as scratch:
        workdir = Path(scratch)
        ctx = RunContext(task=task, agent=agent, workdir=workdir, settings=settings,
                         mcp_servers=[], env={}, emit=_emit, prompt=task.prompt)
        adapter = get_adapter(agent.engine, settings)
        adapter.prepare(ctx)
        cmd = adapter.build_command(ctx)
        payload = adapter.stdin_payload(ctx)
        proc = subprocess.run(cmd, cwd=workdir, env={**os.environ, **adapter.engine_env()},
                              input=payload, stdin=subprocess.DEVNULL if payload is None else None,
                              capture_output=True, timeout=args.timeout + 5, check=False)
        stem = args.name or args.engine
        raw = out / (stem + ".raw.jsonl")
        raw_err = out / (stem + ".raw.stderr.txt")
        raw.write_bytes(proc.stdout)
        raw_err.write_bytes(proc.stderr)
        if args.redact:
            red = Redactor(home=str(Path.home()), tmp=tempfile.gettempdir(), workdir=str(workdir),
                           username=os.environ.get("USERNAME") or os.environ.get("USER") or "")
            redact_file(raw, out / (stem + ".jsonl"), red)
            redact_file(raw_err, out / (stem + ".stderr.txt"), red)
        print(json.dumps({"exit_code": proc.returncode, "stdout_bytes": len(proc.stdout),
                          "stderr_bytes": len(proc.stderr), "raw": str(raw)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
