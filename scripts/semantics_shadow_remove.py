"""Take the semantics shadow (#150 B1) out of a labhq tree, without relying on a revert.

Deletes the files the shadow owns, every line marked ``# semantics-hook`` and every block between
``# semantics-shadow: begin`` and ``# semantics-shadow: end``. A ``semantics:`` key left in a user's config
still loads (Settings ignores unknown top-level keys). PR #136's model files stay; folding them is #143's step.

    python scripts/semantics_shadow_remove.py            # remove from this tree
    python scripts/semantics_shadow_remove.py --check    # remove from a temporary copy and verify it there
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
    "tests/test_semantics_actions.py",
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
COPY = ["labhq", "tests", "scripts", "agents", "config", "pyproject.toml"]
# The output type vocabulary and declarations are core (#221): they must outlive the shadow.
CHECK_TESTS = ["tests/test_e2e_mock.py", "tests/test_semantics_pilot.py", "tests/test_cso.py",
               "tests/test_output_vocab.py", "tests/test_output_types.py", "tests/test_output_types_research.py"]
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


def remove(root: Path) -> dict:
    removed = [rel for rel in OWNED if (root / rel).exists()]
    for rel in removed:
        (root / rel).unlink()
    lines = blocks = 0
    for path in sorted((root / "labhq").rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        if HOOK not in text and BEGIN not in text:
            continue
        kept, inside = [], False
        for line in text.splitlines(keepends=True):
            if BEGIN in line:
                inside, blocks = True, blocks + 1
                continue
            if inside:
                inside = END not in line
                continue
            if HOOK in line:
                lines += 1
                continue
            kept.append(line)
        if inside:
            raise SystemExit(f"{path.relative_to(root)}: {BEGIN} without {END}")
        path.write_text("".join(kept), encoding="utf-8", newline="")
    return {"files": removed, "hook_lines": lines, "blocks": blocks}


def check(state_dir: Path | None = None) -> dict:
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
        summary = remove(copy)
        env = {**_env(), "PYTHONPATH": str(copy)}
        run = [sys.executable, "-c", "import compileall, sys; sys.exit(not compileall.compile_dir('labhq', quiet=1))"]
        _ok(subprocess.run(run, cwd=copy, capture_output=True, text=True, env=env), "compile")
        left = [str(p.relative_to(copy)) for p in (copy / "labhq").rglob("*.py")
                if any(word in p.read_text(encoding="utf-8")
                       for word in ("semantics_shadow", "semantics_objects", "semantics-hook", "records_from_rows"))]
        if left:
            raise SystemExit(f"shadow references left after removal: {left}")
        if state_dir is not None:
            done = _ok(subprocess.run([sys.executable, "-c", SMOKE, str(Path(tmp) / "labhq.yaml"),
                                       state_dir.as_posix()], cwd=copy, capture_output=True, text=True, env=env),
                       "settings and state")
            summary["state"] = __import__("json").loads(done.stdout.strip().splitlines()[-1])
        tests = _ok(subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                                    f"--basetemp={Path(tmp) / 'pt'}", *CHECK_TESTS],
                                   cwd=copy, capture_output=True, text=True, env=env, timeout=900), "pytest")
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
    args = parser.parse_args(argv)
    if args.check:
        print(check(Path(args.state_dir) if args.state_dir else None))
    else:
        print(remove(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
