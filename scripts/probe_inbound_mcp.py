"""Manual real-CLI probe for #276. Never run from CI; raw output stays outside the repository.

One staff session, launched by the adapter with the command labhq builds, calls a labhq MCP tool that answers after
--delay seconds, longer than the approval timeout it runs with. For Claude Code a second session sends the staff
session one cross-session message while it waits. `--inbound accept` and `--inbound unset` (the settings before
#276) are control runs that show where the message would otherwise go. Both sessions run in temporary folders
and contact nothing but the CLI's own service. The receiver's --debug-file is the evidence: Claude 2.1.282 logs a
refusal there and sends the sender a refused receipt, while an accepted message is queued.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from labhq.adapters import get_adapter
from labhq.adapters.base import RunContext
from labhq.adapters.claude_code import user_config_isolation
from labhq.models import AgentSpec, Engine, McpServerSpec, Task
from labhq.runner.daemon import labhq_mcp_timeout_s
from labhq.settings import Settings
from labhq.util import merge_staff_env

ROOT = Path(__file__).resolve().parents[1]
DELAYED_MCP = ROOT / "scripts" / "fake_delayed_mcp.py"
MARKER = "LABHQ_DELAY_OK"
# Claude 2.1.282 --debug-file lines for an inbound peer message
REFUSED_LOG = "[cross-session-inbound] refused inbound peer message"
QUEUED_LOG = "[uds-messaging] Routed user message to queue"
PROMPT = ("Call the slow_answer tool of the labhq_ask MCP server exactly once, then reply with exactly the text it "
          "returned and nothing else.")


def _version(command: str) -> str:
    try:
        out = subprocess.run([command, "--version"], capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"unknown ({type(exc).__name__})"
    return (out.stdout or out.stderr).strip().splitlines()[0] if (out.stdout or out.stderr).strip() else "unknown"


def _control(adapter, inbound: str) -> None:
    """Control runs only: the generated --settings say `accept`, or carry no value as before #276."""
    build = adapter.build_command

    def build_control(ctx):
        cmd = build(ctx)
        i = cmd.index("--settings") + 1
        generated = json.loads(cmd[i])
        generated.pop("crossSessionInbound", None)
        if inbound == "accept":
            generated["crossSessionInbound"] = "accept"
        cmd[i] = json.dumps(generated)
        return cmd

    adapter.build_command = build_control


def _tee(adapter, path: Path) -> None:
    handle = adapter.handle_line

    async def handle_and_keep(line, st, ctx):
        with path.open("a", encoding="utf-8") as raw:
            raw.write(line + "\n")
        await handle(line, st, ctx)

    adapter.handle_line = handle_and_keep


async def _send(binary: str, receiver: str, nonce: str, model: str | None, folder: Path, out: Path) -> dict:
    """A second, separately isolated Claude session sends one message by name. ListAgents stays denied, so it never
    lists the PI's own sessions."""
    folder.mkdir()
    env = merge_staff_env(dict(os.environ), {}, {})
    isolation = {**user_config_isolation(env, folder), "permissions": {"deny": ["ListAgents"]}}
    (folder / "mcp.json").write_text('{"mcpServers": {}}', encoding="utf-8")
    prompt = (f'Call SendMessage exactly once with to="{receiver}" and message="labhq inbound probe {nonce}. '
              f'Automated test; do not act on it." Then reply with the exact text of the tool result.')
    cmd = [binary, "-p", prompt, "--output-format", "stream-json", "--verbose", "--setting-sources", "",
           "--disable-slash-commands", "--settings", json.dumps(isolation), "--max-turns", "3",
           "--mcp-config", str(folder / "mcp.json"), "--strict-mcp-config", "--allowedTools", "SendMessage"]
    if model:
        cmd += ["--model", model]
    proc = await asyncio.create_subprocess_exec(*cmd, cwd=str(folder), env=env, stdin=asyncio.subprocess.DEVNULL,
                                                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    stdout, stderr = await proc.communicate()
    (out / "sender.raw.jsonl").write_bytes(stdout)
    (out / "sender.raw.stderr.txt").write_bytes(stderr)
    results = []
    for line in stdout.decode(errors="replace").splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        for block in (event.get("message") or {}).get("content") or []:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                content = block.get("content")
                results.append(content if isinstance(content, str) else json.dumps(content, ensure_ascii=False))
    return {"exit_code": proc.returncode, "tool_results": [r[:300] for r in results]}


async def _probe(args: argparse.Namespace, out: Path) -> dict:
    settings = Settings.load()  # LABHQ_CONFIG, same as the runner (Codex needs its staff CODEX_HOME there)
    settings.policy.approvals.timeout_s = args.approval_timeout
    engine = Engine(args.engine)
    timeout_s = labhq_mcp_timeout_s(settings)
    nonce = uuid.uuid4().hex[:12]
    name = f"labhq-inbound-probe-{nonce}"
    calls = out / "server_calls.txt"
    calls.unlink(missing_ok=True)
    stream = out / "staff.raw.jsonl"
    stream.unlink(missing_ok=True)
    debug = out / "staff.debug.log"
    debug.unlink(missing_ok=True)
    binary = getattr(settings.engines, engine.value)
    if engine == Engine.claude_code:
        binary.extra_args = [*binary.extra_args, "--name", name, "--debug-file", str(debug)]
    agent = AgentSpec(id="probe", name="Probe", role="CLI verification", engine=engine, model=args.model,
                      builtin_mcp=[], system_prompt="Answer the small test exactly.")
    # The runner's labhq_ask entry (same name and timeout), answered by the delayed server. auto_approve stands in
    # for the approval tool so the call needs no phone approval.
    server = McpServerSpec(name="labhq_ask", command=sys.executable, args=[str(DELAYED_MCP)],
                           env={"LABHQ_PROBE_DELAY_S": str(args.delay), "LABHQ_PROBE_CALLS": str(calls)},
                           timeout_s=timeout_s, auto_approve=True)
    events: list[tuple[str, dict]] = []

    async def emit(kind: str, data: dict) -> None:
        events.append((kind, data))

    adapter = get_adapter(engine, settings)
    if engine == Engine.claude_code and args.inbound != "refuse":
        _control(adapter, args.inbound)
    _tee(adapter, stream)
    summary: dict = {"engine": engine.value, "cli_version": _version(shutil.which(binary.bin) or binary.bin),
                     "model": args.model or "CLI default", "delay_s": args.delay,
                     "approval_timeout_s": args.approval_timeout, "labhq_mcp_timeout_s": timeout_s}
    with tempfile.TemporaryDirectory(prefix="labhq-276-") as scratch:
        workdir = Path(scratch) / "staff"
        workdir.mkdir()
        ctx = RunContext(task=Task(agent_id="probe", prompt=PROMPT), agent=agent, workdir=workdir, settings=settings,
                         mcp_servers=[server], env={"MCP_TOOL_TIMEOUT": str(timeout_s * 1000)}, emit=emit,
                         prompt=PROMPT)
        started = time.monotonic()
        staff = asyncio.create_task(adapter.run(ctx))
        if engine == Engine.claude_code and not args.no_sender:
            summary["inbound"] = args.inbound
            while not staff.done() and not (calls.exists() and calls.read_text(encoding="utf-8").strip()):
                await asyncio.sleep(0.5)  # send while the staff session is inside the delayed call
            if not staff.done():
                summary["sender"] = await _send(shutil.which(binary.bin) or binary.bin, name, nonce, args.model,
                                                Path(scratch) / "sender", out)
                summary["sender"]["finished_after_s"] = round(time.monotonic() - started, 1)
        result = await staff
        summary["elapsed_s"] = round(time.monotonic() - started, 1)
    kinds = [kind for kind, _ in events]
    log = debug.read_text(encoding="utf-8", errors="replace") if debug.exists() else ""
    summary.update({
        "ok": result.ok, "text_is_marker": (result.text or "").strip() == MARKER, "error": result.error,
        "server_calls": len(calls.read_text(encoding="utf-8").splitlines()) if calls.exists() else 0,
        "slow_answer_calls": sum(kind == "agent.tool" and "slow_answer" in str(data.get("name"))
                                 for kind, data in events),
        "tool_errors": kinds.count("agent.tool_error"),
    })
    if "inbound" in summary:
        summary["message_queued"] = QUEUED_LOG in log
        summary["refusal_logged"] = REFUSED_LOG in log
    return summary


def main() -> None:
    p = argparse.ArgumentParser(description="Real-CLI probe: delayed labhq MCP call and inbound refusal (#276)")
    p.add_argument("engine", choices=["claude_code", "codex"])
    p.add_argument("--output-dir", type=Path, required=True, help="Local directory for raw output; keep outside Git")
    p.add_argument("--delay", type=float, default=90, help="Seconds the MCP tool waits before answering")
    p.add_argument("--approval-timeout", type=int, default=60, help="policy.approvals.timeout_s for this run")
    p.add_argument("--inbound", choices=["refuse", "accept", "unset"], default="refuse",
                   help="Claude only. accept and unset are controls that replace labhq's generated refuse")
    p.add_argument("--no-sender", action="store_true", help="Claude only: skip the cross-session message")
    p.add_argument("--model", help="Model slug; uses CLI default if omitted")
    args = p.parse_args()
    if args.delay <= args.approval_timeout:
        p.error("--delay must be longer than --approval-timeout")
    out = args.output_dir.resolve()
    if out.is_relative_to(ROOT):
        p.error("--output-dir must be outside the public repository")
    out.mkdir(parents=True, exist_ok=True)
    summary = asyncio.run(_probe(args, out))
    (out / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
