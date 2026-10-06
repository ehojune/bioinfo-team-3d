"""Render docs/vocabulary.md: topics, topic checklists, the other vocabulary keys and the public resource list,
generated from the files labhq reads, so the tables never drift from them (PI request 2026-10-07).

    python scripts/vocab_tables.py --write   # regenerate docs/vocabulary.md
    python scripts/vocab_tables.py --check   # exit 1 when the committed file is stale
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
VOCAB = ROOT / "labhq" / "vocab"
OUT = ROOT / "docs" / "vocabulary.md"
BRANCH_TITLES = {"data": "데이터 종류(data)", "format": "파일 형식(format)", "operation": "작업(operation)"}


def _cell(text: object) -> str:
    return " ".join(str(text or "").split()).replace("|", "\\|")


def _load_yaml(name: str) -> dict:
    return yaml.safe_load((VOCAB / name).read_text(encoding="utf-8")) or {}


def render() -> str:
    terms = _load_yaml("output_types.yaml").get("terms") or {}
    checklists = _load_yaml("topic_checklists.yaml")
    edam = (_load_yaml("edam_subset.yaml").get("terms") or {}) if (VOCAB / "edam_subset.yaml").exists() else {}
    with (VOCAB / "public_resources.tsv").open(encoding="utf-8", newline="") as handle:
        resources = list(csv.DictReader(handle, delimiter="\t"))

    def edam_label(key: str) -> str:
        entry = edam.get(key) or {}
        return f"{entry.get('label')} ({entry.get('id')})" if entry.get("id") not in (None, "unknown") else "-"

    topics = [key for key, body in terms.items() if body.get("branch") == "topic"]
    items = sum(len(checklists.get(key) or []) for key in topics)
    lines = [
        "# 어휘·topic·점검표",
        "",
        "labhq가 계획과 산출에 붙이는 이름표를 한곳에 모은 표입니다. 찾아볼 때만 읽습니다.",
        "이 파일은 `scripts/vocab_tables.py --write`가 `labhq/vocab/`의 `output_types.yaml`·`topic_checklists.yaml`·"
        "`edam_subset.yaml`·`public_resources.tsv`에서 만듭니다. 손으로 고치지 말고 원본을 고친 뒤 다시 만드세요.",
        "",
        "| 무엇 | 수 | 쓰임 |",
        "|---|---:|---|",
        f"| topic(분야) | {len(topics)} | CSO가 계획마다 적는 분야 이름표. 점검표와 분야 규칙 pack을 고르는 열쇠 |",
        f"| 점검표 항목 | {items} | topic마다 계획이 답해야 하는 점검. 할 수 있으면 하고, 못 하면 이유를 밝힌다 |",
    ]
    for branch, title in BRANCH_TITLES.items():
        count = sum(body.get("branch") == branch for body in terms.values())
        lines.append(f"| {title} | {count} | 단계 산출에 붙이는 이름표 |")
    lines += [f"| 공개 자원 | {len(resources)} | 결과를 공개 DB와 잇는 참고 목록(목록 밖 자원도 쓴다) |", ""]

    lines += ["## topic", "", "| topic | 정의 | 점검표 항목 | EDAM |", "|---|---|---:|---|"]
    for key in topics:
        lines.append(f"| `{key}` | {_cell(terms[key].get('definition'))} | {len(checklists.get(key) or [])} | "
                     f"{_cell(edam_label(key))} |")
    lines += ["", "## 점검표", "",
              "계획은 항목마다 `step:<단계>`(그 단계에서 함), `assumption: <이유>`(못 함, 이유와 함께), "
              "`not_applicable: <이유>`(해당 없음) 중 하나로 답합니다. 근거 문헌은 "
              "[topic 점검표 근거](reference/topic_checklists_sources.md)에 있습니다.", ""]
    for key in topics:
        rows = checklists.get(key) or []
        if not rows:
            continue
        lines += [f"### {key}", "", "| id | 점검 | 이유 |", "|---|---|---|"]
        lines += [f"| `{row.get('id')}` | {_cell(row.get('check'))} | {_cell(row.get('why'))} |" for row in rows]
        lines.append("")
    for branch, title in BRANCH_TITLES.items():
        lines += [f"## {title}", ""]
        if branch == "format":
            lines += ["| key | 정의 | 확장자 | EDAM |", "|---|---|---|---|"]
        else:
            lines += ["| key | 정의 | EDAM |", "|---|---|---|"]
        for key, body in terms.items():
            if body.get("branch") != branch:
                continue
            extra = f" {_cell(', '.join(body.get('extensions') or []))} |" if branch == "format" else ""
            lines.append(f"| `{key}` | {_cell(body.get('definition'))} |{extra} {_cell(edam_label(key))} |")
        lines.append("")
    lines += ["## 공개 자원", "", "| 결과 | 자원 | 쓰임 |", "|---|---|---|"]
    lines += [f"| {_cell(r.get('result'))} | {_cell(r.get('resource'))} | {_cell(r.get('use'))} |" for r in resources]
    lines += ["", "EDAM 이름과 id는 CC BY-SA 4.0입니다([NOTICE](../labhq/vocab/NOTICE.md))."]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    args = parser.parse_args()
    text = render()
    if args.write:
        OUT.write_text(text, encoding="utf-8", newline="\n")
        print(f"wrote {OUT.relative_to(ROOT)}")
        return 0
    current = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
    if current.replace("\r\n", "\n") != text:
        print("docs/vocabulary.md is stale: run python scripts/vocab_tables.py --write", file=sys.stderr)
        return 1
    print("docs/vocabulary.md is up to date")
    return 0


if __name__ == "__main__":
    sys.exit(main())
