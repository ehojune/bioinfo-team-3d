#!/usr/bin/env python3
"""Fresh install, two real gateway/runner mock requests, retained receipts (#923)."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import clean_install_check as install
from scripts import repeat_fake_fixture as fixture


def compare(receipts: list[dict]) -> dict:
    fixture.require(len(receipts) == 2, "two receipts required")
    first, second = receipts
    for receipt in receipts:
        c = receipt["contract"]
        fixture.require(c["status"] == "done" and c["cost_known"] is True and c["cost_usd"] == 0, "invalid terminal/cost")
        fixture.require(c["task_cost_known"] is True and c["task_cost_usd"] == 0, "invalid task cost")
        fixture.require(c["mock_marked"] is True and c["evidence_linked"] is True, "missing evidence")
        fixture.require(c["external_connections"] == c["child_commands"] == 0, "offline boundary")
        fixture.require(0 < receipt["elapsed_s"] <= c["timeout_s"] == fixture.TIMEOUT_S, "time cap")
        fixture.require(receipt["process_exit"] == 0 and receipt["remaining_children"] == [], "child cleanup")
    fixture.require(first["contract"] == second["contract"], "contracts differ")
    for key in ("request_id", "artifact_identity"):
        fixture.require(first[key] != second[key], f"reused {key}")
    fixture.require(set(first["task_ids"]).isdisjoint(second["task_ids"]), "reused task_ids")
    return {"schema_version": 1, "pass": True, "same_contract": first["contract"],
            "allowed_to_differ": ["created_at", "elapsed_s", "request_id", "task_ids", "workdir", "bundle", "artifact_identity"],
            "fresh_request_task_artifact": True, "receipts": ["run-1/receipt.json", "run-2/receipt.json"]}


def command(args: list[str], cwd: Path, env: dict[str, str], logs: Path, name: str, timeout=600, input_text=None):
    """Retain stdout and stderr separately, including failures; commands have deadlines."""
    with (logs / f"{name}.stdout.log").open(mode="wb") as out, (logs / f"{name}.stderr.log").open(mode="wb") as err:
        done = subprocess.run(args, cwd=cwd, env=env, input=input_text.encode() if input_text else None,
                              stdout=out, stderr=err, timeout=timeout, check=False)
    fixture.require(done.returncode == 0, f"{name} failed (exit {done.returncode}); see separate logs")


def rehearse(source: Path, output: Path) -> dict:
    fixture.require(not output.exists(), "output must be a new directory")
    output.mkdir(parents=True)
    logs = output / "logs"
    logs.mkdir()
    installation = output / "installation"
    env = os.environ.copy()
    # Reuse #922's documented installer, including its own one-request install smoke and cleanup.
    command([sys.executable, str(source / "scripts/clean_install_check.py"), "--source", str(source),
             "--work-dir", str(installation), "--receipt", str(output / "installation.json")],
            source, env, logs, "clean-install", timeout=1200)
    checkout = installation / "bioinfo-team-3d"
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=checkout, text=True).strip()
    fixture.require(commit == subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip(),
                    "clone HEAD differs")
    env = install._isolated_env(output, checkout)
    python = install._venv_python(checkout)
    fixture.require((checkout / "config/labhq.yaml").is_file(), "init did not write config")
    run_env = env.copy()
    for key in list(run_env):
        if key.endswith(("_API_KEY", "_TOKEN")) or key in {"GH_TOKEN", "GITHUB_TOKEN", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"}:
            run_env.pop(key, None)
    receipts = []
    for n in (1, 2):
        root = output / f"run-{n}"
        root.mkdir()
        child = [str(python), str(checkout / "scripts/repeat_fake_check.py"), "--child", str(root),
                 "--config", str(checkout / "config/labhq.yaml")]
        with (logs / f"run-{n}.stdout.log").open(mode="wb") as out, (logs / f"run-{n}.stderr.log").open(mode="wb") as err:
            proc = subprocess.Popen(child, cwd=checkout, env=run_env, stdout=out, stderr=err)
            timed_out = False
            try:
                code = proc.wait(timeout=fixture.TIMEOUT_S + 60)
            except subprocess.TimeoutExpired:
                timed_out = True
                proc.kill()
                code = proc.wait(timeout=10)
        remaining = install._venv_python_processes(checkout, {})
        path = root / "receipt.json"
        try:
            receipt = json.loads(path.read_text(encoding="utf-8"))
            fixture.require(isinstance(receipt, dict), "receipt is not an object")
        except (OSError, ValueError):
            receipt = {"error": "receipt missing or malformed"}
        receipt.update(process_exit=code, remaining_children=remaining, process_timeout=timed_out)
        fixture.write_json(path, receipt)
        fixture.require(code == 0 and not remaining, f"run-{n} failed or left children; see receipt/logs")
        receipts.append(receipt)
    comparison = compare(receipts)
    comparison["installation_commit"] = commit
    fixture.write_json(output / "comparison.json", comparison)
    return comparison


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path)
    parser.add_argument("--child", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--config", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    output_existed = args.output is not None and args.output.exists()
    child_receipt_existed = args.child is not None and (args.child / "receipt.json").exists()
    try:
        if args.child:
            fixture.require(not child_receipt_existed, "child receipt already exists")
            receipt = asyncio.run(fixture.exercise(args.child.resolve(), args.config.resolve()))
            fixture.write_json(args.child / "receipt.json", receipt)
        else:
            fixture.require(args.output is not None, "--output required")
            rehearse(args.source.resolve(), args.output.resolve())
    except Exception as exc:
        failure = {"schema_version": 1, "pass": False, "error": str(exc)}
        if args.child and args.child.is_dir() and not child_receipt_existed:
            fixture.write_json(args.child / "receipt.json", failure)
        elif args.output and args.output.is_dir() and not output_existed:
            fixture.write_json(args.output / "comparison.json", failure)
        print(f"FAIL repeat fake: {exc}", file=sys.stderr)
        return 1
    print("PASS repeat fake")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
