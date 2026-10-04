"""패치노트 규칙을 기계로 확인하고, PR 커밋의 YAML 행을 뽑는다.

규칙 (PI 결정 2026-10-01, 2026-10-02):
- 커밋마다 patch_notes/entries/<branch>.yaml에 한 줄. 기록만 고친 커밋은 제외한다.
- STATUS는 docs/status/<시각>-<branch>.md에 쓴다.
- 생성 파일 STATUS.md와 patch_notes/README.md는 PR에서 직접 고치지 않는다.
- main 커밋 3개 안에 README.md나 docs/manual.md를 한 번은 갱신한다. 생성 목차만 갱신한 main 커밋은 세지 않는다.
  README는 처음 써 보는 사람용이고 기능 설명은 매뉴얼에 있어서, 둘 중 어느 쪽을 고쳐도 센다(#298).

  python scripts/patch_notes.py check --base origin/main [--head HEAD]
  python scripts/patch_notes.py rows --base origin/main --pr 49
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

ENTRIES = "patch_notes/entries"
GENERATED = {"STATUS.md", "patch_notes/README.md"}
README_EVERY = 3
DOCS = ("README.md", "docs/manual.md")  # either one counts as the periodic documentation refresh
KST = timezone(timedelta(hours=9))


def git(*args: str, cwd: Path | None = None) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True,
                          encoding="utf-8").stdout.strip()


def note_path(path: str) -> bool:
    return path.startswith("patch_notes/") or path.startswith("docs/status/")


def only_note_paths(files: set[str]) -> bool:
    return bool(files) and all(note_path(path) for path in files)


def pr_commits(base: str, head: str, cwd: Path | None = None) -> list[str]:
    out = git("rev-list", "--reverse", "--parents", f"{base}..{head}", cwd=cwd)
    commits = []
    for line in out.splitlines():
        sha, *parents = line.split()
        files = merge_own_changes(sha, parents, cwd) if len(parents) > 1 else changed_files(sha, cwd)
        if only_note_paths(files) or (len(parents) > 1 and not files):
            continue
        commits.append(sha)
    return commits


def merge_own_changes(sha: str, parents: list[str], cwd: Path | None = None) -> set[str]:
    """Files a merge changed beyond git's automatic merge (conflict resolutions, extra edits)."""
    if len(parents) != 2:
        return changed_files(sha, cwd)
    run = subprocess.run(["git", "merge-tree", "--write-tree", "--no-messages", *parents], cwd=cwd,
                         capture_output=True, text=True, encoding="utf-8")
    if run.returncode not in (0, 1) or not run.stdout.split():
        return changed_files(sha, cwd)
    out = git("diff", "--name-only", run.stdout.split()[0], sha, cwd=cwd)
    return set(out.splitlines()) if out else set()


def changed_files(sha: str, cwd: Path | None = None) -> set[str]:
    out = git("diff-tree", "--cc", "--no-commit-id", "--name-only", "-r", "--root", sha, cwd=cwd)
    return set(out.splitlines()) if out else set()


def changed_from_parent(sha: str, cwd: Path | None = None) -> set[str]:
    parents = git("rev-list", "--parents", "-n", "1", sha, cwd=cwd).split()[1:]
    if not parents:
        return changed_files(sha, cwd)
    out = git("diff", "--name-only", parents[0], sha, cwd=cwd)
    return set(out.splitlines()) if out else set()


def tree_files(rev: str, prefix: str, cwd: Path | None = None) -> list[str]:
    out = git("ls-tree", "-r", "--name-only", rev, "--", prefix, cwd=cwd)
    return out.splitlines() if out else []


def entry_shas(rev: str, cwd: Path | None = None) -> tuple[set[str], list[str]]:
    shas: set[str] = set()
    problems = []
    for path in tree_files(rev, ENTRIES, cwd):
        if not path.endswith(".yaml"):
            continue
        try:
            data = yaml.safe_load(git("show", f"{rev}:{path}", cwd=cwd))
            rows = data.get("rows") if isinstance(data, dict) else None
            if not isinstance(rows, list):
                raise ValueError("rows가 목록이 아님")
            for row in rows:
                sha = row.get("sha") if isinstance(row, dict) else None
                if isinstance(sha, str):
                    shas.add(sha)
                elif isinstance(sha, int) and not isinstance(sha, bool):
                    # An all-digit short sha is a YAML integer: say so instead of reporting the commit missing.
                    problems.append(f"{path}: sha {sha}가 숫자로 읽힙니다. 숫자로만 된 sha는 따옴표로 감싸세요"
                                    f" (sha: '{sha}')")
        except (subprocess.CalledProcessError, ValueError, yaml.YAMLError) as exc:
            problems.append(f"{path}을 읽을 수 없습니다: {exc}")
    return shas, problems


def counted_main_commits(last_readme: str, base: str, cwd: Path | None = None) -> int:
    if not last_readme:
        return README_EVERY
    out = git("rev-list", "--first-parent", "--reverse", f"{last_readme}..{base}", cwd=cwd)
    count = 0
    for sha in out.splitlines():
        files = changed_from_parent(sha, cwd)
        if files and files <= GENERATED:
            continue
        count += 1
    return count


def check(base: str, head: str, cwd: Path | None = None) -> list[str]:
    """Problems as Korean messages; empty means the PR follows both rules."""
    problems = []
    commits = pr_commits(base, head, cwd)
    shas, load_problems = entry_shas(head, cwd)
    problems.extend(load_problems)
    for sha in commits:
        if sha[:7] not in shas:
            subject = git("log", "-1", "--format=%s", sha, cwd=cwd)
            problems.append(f"패치노트에 커밋 {sha[:7]}({subject})이 없습니다. {ENTRIES}/<branch>.yaml에 한 줄 적어 주세요.")

    touched = set(git("diff", "--name-only", f"{base}...{head}", cwd=cwd).splitlines())
    # 이 전환 PR은 base에 entries가 없으므로 생성 파일을 처음 만들 수 있다. 병합 뒤부터는 항상 막는다.
    if tree_files(base, ENTRIES, cwd) and touched & GENERATED:
        names = ", ".join(sorted(touched & GENERATED))
        problems.append(f"생성 파일({names})을 직접 바꾸지 말고 entries·docs/status에 쓰세요.")

    last = git("log", "-1", "--format=%H", base, "--", *DOCS, cwd=cwd)
    behind = counted_main_commits(last, base, cwd)
    if commits and not touched & set(DOCS) and behind + 1 >= README_EVERY:
        problems.append(f"main에 문서({'·'.join(DOCS)})를 안 고친 커밋이 {behind}개 쌓였습니다. "
                        f"이 PR에서 README나 매뉴얼을 갱신해 주세요 (커밋 {README_EVERY}개마다 한 번).")
    return problems


def rows(base: str, head: str, pr: int | None, cwd: Path | None = None) -> str:
    """YAML rows, newest first, for commits an entry file still has to describe."""
    out = []
    for sha in reversed(pr_commits(base, head, cwd)):
        when = datetime.fromtimestamp(int(git("log", "-1", "--format=%ct", sha, cwd=cwd)), KST)
        subject = git("log", "-1", "--format=%s", sha, cwd=cwd)
        out.append({"sha": sha[:7], "at": f"{when:%Y-%m-%d %H:%M}", "text": subject})
    return yaml.safe_dump(out, allow_unicode=True, sort_keys=False, width=100000).rstrip() if out else ""


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("check", "rows"):
        parser = sub.add_parser(name)
        parser.add_argument("--base", default="origin/main")
        parser.add_argument("--head", default="HEAD")
        if name == "rows":
            parser.add_argument("--pr", type=int)
    args = ap.parse_args(argv)
    if args.cmd == "rows":
        print(rows(args.base, args.head, args.pr))
        return 0
    problems = check(args.base, args.head)
    for problem in problems:
        print(f"✗ {problem}")
    if not problems:
        print("✓ 패치노트와 문서 갱신 규칙을 지켰습니다")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
