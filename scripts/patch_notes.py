"""패치노트 규칙을 기계로 확인하고, PR 커밋의 표 행을 뽑는다.

규칙 (PI 결정 2026-10-01):
- 커밋마다 patch_notes/README.md에 한 줄. 패치노트만 고친 커밋은 제외(자기 해시를 담을 수 없음).
- main 커밋 3개 안에 README.md를 한 번은 갱신. main은 PR마다 커밋 하나(스쿼시)라 PR 단위로 센다.

  python scripts/patch_notes.py check --base origin/main [--head HEAD]
  python scripts/patch_notes.py rows --base origin/main --pr 49
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

NOTES = "patch_notes/README.md"
REPO_URL = "https://github.com/ehojune/bioinfo-team-3d"
README_EVERY = 3
KST = timezone(timedelta(hours=9))


def git(*args: str, cwd: Path | None = None) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True,
                          encoding="utf-8").stdout.strip()


def pr_commits(base: str, head: str, cwd: Path | None = None) -> list[str]:
    out = git("rev-list", "--reverse", "--parents", f"{base}..{head}", cwd=cwd)
    commits = []
    for line in out.splitlines():
        sha, *parents = line.split()
        if len(parents) > 1 and not (changed_files(sha, cwd) - {"STATUS.md", NOTES}):
            continue  # A sync merge or bookkeeping-only resolution needs no separate note.
        commits.append(sha)
    return commits


def changed_files(sha: str, cwd: Path | None = None) -> set[str]:
    out = git("diff-tree", "--cc", "--no-commit-id", "--name-only", "-r", "--root", sha, cwd=cwd)
    return set(out.split("\n")) if out else set()


def check(base: str, head: str, cwd: Path | None = None) -> list[str]:
    """Problems as Korean messages; empty means the PR follows both rules."""
    problems = []
    commits = pr_commits(base, head, cwd)
    try:
        notes = git("show", f"{head}:{NOTES}", cwd=cwd)
    except subprocess.CalledProcessError:
        notes = ""
    for sha in commits:
        files = changed_files(sha, cwd)
        if files and files <= {NOTES}:
            continue
        if sha[:7] not in notes:
            subject = git("log", "-1", "--format=%s", sha, cwd=cwd)
            problems.append(f"패치노트에 커밋 {sha[:7]}({subject})이 없습니다. {NOTES}에 한 줄 적어 주세요.")
    # The final diff, not the union of commits: a README edit reverted later is not a refresh.
    touched = set(git("diff", "--name-only", f"{base}...{head}", cwd=cwd).split()) if commits else set()
    last = git("log", "-1", "--format=%H", base, "--", "README.md", cwd=cwd)
    behind = int(git("rev-list", "--count", "--first-parent", f"{last}..{base}", cwd=cwd)) if last else README_EVERY
    if commits and "README.md" not in touched and behind + 1 >= README_EVERY:
        problems.append(f"main에 README.md를 안 고친 커밋이 {behind}개 쌓였습니다. 이 PR에서 README를 갱신해 주세요 "
                        f"(커밋 {README_EVERY}개마다 한 번).")
    return problems


def rows(base: str, head: str, pr: int | None, cwd: Path | None = None) -> str:
    """Table rows, newest first, for the commits a patch-note entry still has to describe."""
    out = []
    for sha in reversed(pr_commits(base, head, cwd)):
        files = changed_files(sha, cwd)
        if files and files <= {NOTES}:
            continue
        when = datetime.fromtimestamp(int(git("log", "-1", "--format=%ct", sha, cwd=cwd)), KST)  # 3.10 rejects "Z"
        link = f"{REPO_URL}/pull/{pr}/commits/{sha[:7]}" if pr else f"{REPO_URL}/commit/{sha[:7]}"
        subject = git("log", "-1", "--format=%s", sha, cwd=cwd)
        out.append(f"| {when:%H:%M} | [`{sha[:7]}`]({link}) | **{subject}** |  <!-- {when:%Y-%m-%d} -->")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")  # a cp949 console cannot print the check marks
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("check", "rows"):
        p = sub.add_parser(name)
        p.add_argument("--base", default="origin/main")
        p.add_argument("--head", default="HEAD")
        if name == "rows":
            p.add_argument("--pr", type=int)
    a = ap.parse_args(argv)
    if a.cmd == "rows":
        print(rows(a.base, a.head, a.pr))
        return 0
    problems = check(a.base, a.head)
    for p in problems:
        print(f"✗ {p}")
    if not problems:
        print("✓ 패치노트와 README 갱신 규칙을 지켰습니다")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
