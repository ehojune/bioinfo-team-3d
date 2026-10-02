"""PR별 기록 파일에서 패치노트와 STATUS 목차를 만든다.

  python scripts/notes_index.py --write
  python scripts/notes_index.py --check
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path

import yaml

REPO_URL = "https://github.com/ehojune/bioinfo-team-3d"
GENERATED_NOTICE = "<!-- 자동 생성 — 직접 고치지 말 것, entries·docs/status에 쓴다 -->"
PATCH_HEADER = f"""# 패치노트

{GENERATED_NOTICE}

이 저장소의 변경 이력입니다. 최신순.

날짜·시간은 커밋 시각(KST)입니다. 커밋 해시를 누르면 실제 변경 내용을 볼 수 있습니다.
PR로 들어온 변경은 스쿼시 머지라 개별 커밋이 main에 남지 않기 때문에, 해시는 그 PR 안의
커밋으로 연결됩니다.

초기 PR(#1–#11)은 머지 커밋으로 들어와 개별 커밋이 main에도 남아 있지만, 링크는 똑같이 PR 안의 커밋으로 걸었습니다. PR 없이 main에 바로 올린 커밋은 `/commit/` 주소로 연결됩니다.
"""
STATUS_HEADER = f"""# STATUS — labhq

{GENERATED_NOTICE}

최신 항목이 맨 위. 단계를 끝낼 때마다 PR 본문과 같은 내용을 여기에 추가합니다 (형식: `.github/pull_request_template.md`).
"""
ENTRY_NAME = re.compile(r"^(?!_)[^/]+\.yaml$")
STATUS_NAME = re.compile(r"^\d{4}-\d{2}-\d{2}-\d{4}-.+\.md$")
SHA7 = re.compile(r"^[0-9a-f]{7}$")


def _load_entry(path: Path) -> list[dict[str, object]]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or set(data) != {"pr", "rows"}:
        raise ValueError(f"{path}: pr과 rows만 있어야 합니다")
    default_pr = data["pr"]
    if default_pr is not None and not isinstance(default_pr, int):
        raise ValueError(f"{path}: pr은 정수 또는 null이어야 합니다")
    if not isinstance(data["rows"], list):
        raise ValueError(f"{path}: rows는 목록이어야 합니다")

    loaded = []
    for index, raw in enumerate(data["rows"]):
        if not isinstance(raw, dict):
            raise ValueError(f"{path}: rows[{index}]가 객체가 아닙니다")
        allowed = {"sha", "at", "text"} | ({"pr"} if path.name == "_legacy.yaml" else set())
        if set(raw) - allowed or not {"sha", "at", "text"} <= set(raw):
            raise ValueError(f"{path}: rows[{index}] 필드가 잘못됐습니다")
        sha, at, text = raw["sha"], raw["at"], raw["text"]
        if not isinstance(sha, str) or not SHA7.fullmatch(sha):
            raise ValueError(f"{path}: rows[{index}].sha는 7자리 소문자 해시여야 합니다")
        if not isinstance(at, str):
            raise ValueError(f"{path}: rows[{index}].at은 문자열이어야 합니다")
        try:
            stamp = datetime.strptime(at, "%Y-%m-%d %H:%M")
        except ValueError as exc:
            raise ValueError(f"{path}: rows[{index}].at 형식은 YYYY-MM-DD HH:MM입니다") from exc
        if not isinstance(text, str) or not text or "\n" in text:
            raise ValueError(f"{path}: rows[{index}].text는 한 줄이어야 합니다")
        pr = raw.get("pr", default_pr)
        if pr is not None and not isinstance(pr, int):
            raise ValueError(f"{path}: rows[{index}].pr은 정수 또는 null이어야 합니다")
        loaded.append({"sha": sha, "at": at, "stamp": stamp, "text": text, "pr": pr})
    return loaded


def patch_rows(root: Path) -> list[dict[str, object]]:
    directory = root / "patch_notes" / "entries"
    rows: list[dict[str, object]] = []
    for path in sorted(directory.glob("*.yaml")):
        if path.name != "_legacy.yaml" and not ENTRY_NAME.fullmatch(path.name):
            raise ValueError(f"{path}: entries 파일 이름이 잘못됐습니다")
        rows.extend(_load_entry(path))
    return sorted(rows, key=lambda row: row["stamp"], reverse=True)


def render_patch_notes(root: Path) -> str:
    lines = [PATCH_HEADER.rstrip()]
    current_date = None
    for row in patch_rows(root):
        date, time = str(row["at"]).split()
        if date != current_date:
            lines.extend(["", f"## {date}", "", "| 시간 | 커밋 | 주요 변경사항 |", "|---|---|---|"])
            current_date = date
        sha = str(row["sha"])
        pr = row["pr"]
        link = f"{REPO_URL}/pull/{pr}/commits/{sha}" if pr is not None else f"{REPO_URL}/commit/{sha}"
        lines.append(f"| {time} | [`{sha}`]({link}) | {row['text']} |")
    return "\n".join(lines) + "\n"


def render_status(root: Path) -> str:
    directory = root / "docs" / "status"
    current = []
    legacy = None
    for path in sorted(directory.glob("*.md"), reverse=True):
        if path.name == "_legacy.md":
            legacy = path.read_text(encoding="utf-8").strip()
        elif STATUS_NAME.fullmatch(path.name):
            current.append(path.read_text(encoding="utf-8").strip())
        else:
            raise ValueError(f"{path}: status 파일 이름은 YYYY-MM-DD-HHMM-branch.md여야 합니다")
    sections = [section for section in current if section]
    if legacy:
        sections.append(legacy)
    return STATUS_HEADER.rstrip() + "\n\n" + "\n\n".join(sections) + "\n"


def expected(root: Path) -> dict[Path, str]:
    return {
        root / "patch_notes" / "README.md": render_patch_notes(root),
        root / "STATUS.md": render_status(root),
    }


def write(root: Path) -> bool:
    changed = False
    for path, content in expected(root).items():
        old = path.read_text(encoding="utf-8") if path.exists() else None
        if old != content:
            path.write_text(content, encoding="utf-8", newline="\n")
            changed = True
            print(f"갱신: {path.relative_to(root)}")
    return changed


def check(root: Path) -> bool:
    clean = True
    for path, content in expected(root).items():
        if not path.exists() or path.read_text(encoding="utf-8") != content:
            print(f"✗ {path.relative_to(root)}가 생성 결과와 다릅니다. python scripts/notes_index.py --write")
            clean = False
    if clean:
        print("✓ 패치노트·STATUS 목차가 최신입니다")
    return clean


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    try:
        if args.write:
            write(root)
            return 0
        return 0 if check(root) else 1
    except (OSError, ValueError, yaml.YAMLError) as exc:
        print(f"✗ {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
