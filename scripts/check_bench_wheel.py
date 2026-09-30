"""Check packaged resources and bench execution from a non-editable wheel install.

Usage: python scripts/check_bench_wheel.py path/to/labhq.whl
No dependencies are installed; the caller's existing runtime supplies them.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile


def check_wheel(wheel: Path) -> None:
    expected = {"inco-kras-g12c", "plastome-structure", "geo-gastric-summary",
                "public-protein-qc", "public-penguins-qc"}
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        required = {f"labhq/bench_data/cases/{case}.yaml" for case in expected}
        required |= {"labhq/bench_data/checks/contains_terms.py"}
        required |= {f"labhq/bench_data/references/{name}" for name in
                     ("kras-g12c.md", "geo-gastric.md", "plastome.tsv", "penguins.tsv", "protein-manifest.tsv")}
        missing = required - names
        if missing:
            raise ValueError(f"wheel lacks benchmark data: {sorted(missing)}")
    with tempfile.TemporaryDirectory(prefix="labhq-wheel-") as raw:
        work = Path(raw)
        site = work / "site"
        subprocess.run([sys.executable, "-m", "pip", "install", "--no-deps", "--no-compile",
                        "--no-index", "--no-cache-dir",
                        "--target", str(site), str(wheel.resolve())], check=True, cwd=work)
        env = {**os.environ, "PYTHONPATH": str(site), "PYTHONDONTWRITEBYTECODE": "1",
               "PYTHONIOENCODING": "utf-8", "LABHQ_STATE_DIR": str(work / "state")}
        probe = """
import asyncio
from pathlib import Path
from labhq import bench
from labhq.cli import main
assert Path(bench.__file__).is_relative_to(Path('site').resolve())
cases = bench.load_cases()
assert len(cases) == 5
main(['bench', 'list'])
for case in cases:
    assert bench._prompt(case)
    arm = Path(case['id'])
    arm.mkdir()
    (arm / 'answer.md').write_text(case['mock_answer'], encoding='utf-8')
    row = asyncio.run(bench._score(case, arm, {'engine': 'wheel', 'status': 'done'}))
    assert row['checks_passed'], row['check_output']
print('wheel: 5 cases, references and installed checkers PASS')
"""
        subprocess.run([sys.executable, "-c", probe], check=True, cwd=work, env=env)


if __name__ == "__main__":
    check_wheel(Path(sys.argv[1]))
