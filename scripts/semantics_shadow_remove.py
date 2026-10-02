"""Take the semantics shadow (#150 B1) out of a labhq tree, without relying on a revert.

Deletes the files the shadow owns, every line marked ``# semantics-hook`` and every block between
``# semantics-shadow: begin`` and ``# semantics-shadow: end``. A ``semantics:`` key left in a user's config
still loads (Settings ignores unknown top-level keys). PR #136's model files stay; folding them is #143's step.

``--only actions`` takes out just the action layer (#149 결정 13 A1, 결정 16 A2): its files, the lines marked
``# semantics-hook: actions`` and the ``# semantics-actions: begin``/``end`` blocks. B1 stays as it was; a config
that still says ``actions:`` then turns semantics off with one warning (an unknown key), so delete that key.

    python scripts/semantics_shadow_remove.py                          # remove from this tree
    python scripts/semantics_shadow_remove.py --check                  # remove from a temporary copy, verify it
    python scripts/semantics_shadow_remove.py --only actions [--check] # the action layer only
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ACTIONS_OWNED = [
    "labhq/research/semantics_actions.py",
    "labhq/research/semantics_actions_run.py",
    "tests/test_semantics_actions.py",
    "tests/test_semantics_actions_shadow.py",
    "tests/test_semantics_actions_run.py",
]
OWNED = [
    "labhq/research/semantics_shadow.py",
    "labhq/research/semantics_objects.py",
    "tests/semantics_shadow_lab.py",
    "tests/test_semantics_shadow_settings.py",
    "tests/test_semantics_shadow_worker.py",
    "tests/test_semantics_shadow_provenance.py",
    "tests/test_semantics_objects.py",
    "tests/test_semantics_shadow_hash.py",
    "tests/test_semantics_shadow_breaker.py",
    "tests/test_semantics_shadow_report.py",
    "tests/test_semantics_shadow_remove.py",
    *ACTIONS_OWNED,
    "scripts/semantics_shadow_remove.py",
]
HOOK = "# semantics-hook"
BEGIN, END = "# semantics-shadow: begin", "# semantics-shadow: end"
ACTIONS_HOOK = "# semantics-hook: actions"
ACTIONS_BEGIN, ACTIONS_END = "# semantics-actions: begin", "# semantics-actions: end"
COPY = ["labhq", "tests", "scripts", "agents", "config", "pyproject.toml"]
# The output type vocabulary and declarations are core (#221): they must outlive the shadow.
CHECK_TESTS = ["tests/test_e2e_mock.py", "tests/test_semantics_pilot.py", "tests/test_cso.py",
               "tests/test_output_vocab.py", "tests/test_output_types.py", "tests/test_output_types_research.py"]
ACTIONS_CHECK_TESTS = ["tests/test_semantics_shadow_settings.py", "tests/test_semantics_shadow_report.py",
                       "tests/test_semantics_shadow_breaker.py", "tests/test_semantics_pilot.py",
                       "tests/test_followup.py", "tests/test_e2e_mock.py"]
SMOKE = """
import asyncio, json, sys
from pathlib import Path
from labhq.settings import Settings
from labhq.gateway.server import Hub
from labhq.integrations.rounds import build_record
from labhq import vocab
from labhq.vocab import declare

assert vocab.load().counts()["keys"] >= 1 and declare.FIELDS, "the output type vocabulary went with the shadow"

config = Path(sys.argv[1])
config.write_text("semantics: {mode: shadow, timeout_s: 2}\\ngateway: {state_dir: '%s'}\\n" % sys.argv[2],
                  encoding="utf-8")
s = Settings.load(str(config))
assert not hasattr(s, "semantics"), "the semantics field is still there"

async def main():
    hub = Hub(s)
    assert not hasattr(hub, "semantics_shadow")
    done = [r for r in hub.requests.values() if r.get("status") == "done"]
    waiting = [r for r in hub.requests.values() if r.get("status") == "interrupted"]
    resumes = [a for a in hub.approvals.values() if a["approval"].get("kind") == "resume"]
    for r in done:
        build_record(hub, r["id"])
    print(json.dumps({"done": len(done), "interrupted": len(waiting), "resume_approvals": len(resumes)}))

asyncio.run(main())
"""
ACTIONS_SMOKE = """
import asyncio, json, sys, time
from pathlib import Path
from labhq.settings import Settings
from labhq.gateway.server import Hub
from labhq.integrations.rounds import build_record
from labhq.research import semantics_shadow as shadow

config = Path(sys.argv[1])
config.write_text("semantics: {mode: shadow, timeout_s: 2}\\ngateway: {state_dir: '%s'}\\n" % sys.argv[2],
                  encoding="utf-8")
s = Settings.load(str(config))
assert not hasattr(shadow.ShadowConfig(), "actions") and "actions" not in shadow.KEYS
assert shadow.resolve({"mode": "shadow", "actions": "shadow"}) is None, "an actions key is unknown again"

async def main():
    hub = Hub(s)
    assert hub.semantics_shadow is not None and not hasattr(hub.semantics_shadow, "after_followup")
    done = [r for r in hub.requests.values() if r.get("status") == "done"]
    waiting = [r for r in hub.requests.values() if r.get("status") == "interrupted"]
    resumes = [a for a in hub.approvals.values() if a["approval"].get("kind") == "resume"]
    for r in done:
        build_record(hub, r["id"])
    hub.requests["req_smoke1"] = {"id": "req_smoke1", "status": "done", "text": "x", "mode": "orchestrate",
                                  "created_at": time.time()}
    hub.commit_terminal("req_smoke1", "request.completed", {"ok": True})
    await asyncio.sleep(0.05)
    assert hub.semantics_shadow.drain(20)
    lines = [json.loads(x) for x in shadow.ShadowPaths(shadow.shadow_root(s)).log.read_text(encoding="utf-8").splitlines()]
    line = [l for l in lines if l.get("request_id") == "req_smoke1"][-1]
    print(json.dumps({"done": len(done), "interrupted": len(waiting), "resume_approvals": len(resumes),
                      "b1_line": line["objects"]["status"], "actions_field": "actions" in line}))

asyncio.run(main())
"""


def _scope(only: str | None) -> tuple[list[str], str, list[tuple[str, str]]]:
    if only == "actions":
        return ACTIONS_OWNED, ACTIONS_HOOK, [(ACTIONS_BEGIN, ACTIONS_END)]
    return OWNED, HOOK, [(BEGIN, END), (ACTIONS_BEGIN, ACTIONS_END)]


def remove(root: Path, only: str | None = None) -> dict:
    owned, hook, pairs = _scope(only)
    removed = [rel for rel in owned if (root / rel).exists()]
    for rel in removed:
        (root / rel).unlink()
    lines = blocks = 0
    for path in sorted((root / "labhq").rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        if hook not in text and not any(begin in text for begin, _ in pairs):
            continue
        kept, end = [], None
        for line in text.splitlines(keepends=True):
            if end is not None:
                if end in line:
                    end = None
                continue
            begin = next((pair for pair in pairs if pair[0] in line), None)
            if begin is not None:
                end, blocks = begin[1], blocks + 1
                continue
            if hook in line:
                lines += 1
                continue
            kept.append(line)
        if end is not None:
            raise SystemExit(f"{path.relative_to(root)}: block without {end}")
        path.write_text("".join(kept), encoding="utf-8", newline="")
    return {"files": removed, "hook_lines": lines, "blocks": blocks}


def check(state_dir: Path | None = None, only: str | None = None) -> dict:
    """Remove from a temporary copy, then compile, grep, load settings, read state and run core tests there."""
    with tempfile.TemporaryDirectory(prefix="labhq-semantics-remove-") as tmp:
        copy = Path(tmp) / "labhq"
        copy.mkdir()
        for name in COPY:
            source = ROOT / name
            if source.is_dir():
                shutil.copytree(source, copy / name, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", "labhq.yaml"))
            elif source.exists():
                shutil.copy2(source, copy / name)
        summary = remove(copy, only)
        env = {**_env(), "PYTHONPATH": str(copy)}
        run = [sys.executable, "-c", "import compileall, sys; sys.exit(not compileall.compile_dir('labhq', quiet=1))"]
        _ok(subprocess.run(run, cwd=copy, capture_output=True, text=True, env=env), "compile")
        words = (("semantics_actions", "semantics-hook: actions", "semantics-actions", "after_followup")
                 if only == "actions" else
                 ("semantics_shadow", "semantics_objects", "semantics-hook", "records_from_rows", "semantics_actions",
                  "after_followup"))
        left = [str(p.relative_to(copy)) for p in (copy / "labhq").rglob("*.py")
                if any(word in p.read_text(encoding="utf-8") for word in words)]
        if left:
            raise SystemExit(f"shadow references left after removal: {left}")
        if state_dir is not None:
            done = _ok(subprocess.run([sys.executable, "-c", ACTIONS_SMOKE if only == "actions" else SMOKE,
                                       str(Path(tmp) / "labhq.yaml"), state_dir.as_posix()],
                                      cwd=copy, capture_output=True, text=True, env=env, timeout=300),
                       "settings and state")
            summary["state"] = __import__("json").loads(done.stdout.strip().splitlines()[-1])
        tests = _ok(subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                                    f"--basetemp={Path(tmp) / 'pt'}",
                                    *(ACTIONS_CHECK_TESTS if only == "actions" else CHECK_TESTS)],
                                   cwd=copy, capture_output=True, text=True, env=env, timeout=1500), "pytest")
        summary["pytest"] = tests.stdout.strip().splitlines()[-1]
        return summary


def _env() -> dict:
    import os
    return {**os.environ, "PYTHONUTF8": "1"}


def _ok(done: subprocess.CompletedProcess, what: str) -> subprocess.CompletedProcess:
    if done.returncode != 0:
        raise SystemExit(f"{what} failed after removal:\n{(done.stdout + done.stderr)[-3000:]}")
    return done


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="remove from a temporary copy and verify it")
    parser.add_argument("--state-dir", help="with --check: a gateway state_dir the copy must still read")
    parser.add_argument("--only", choices=["actions"], help="take out only the action layer's shadow (A1)")
    args = parser.parse_args(argv)
    if args.check:
        print(check(Path(args.state_dir) if args.state_dir else None, args.only))
    else:
        print(remove(ROOT, args.only))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
