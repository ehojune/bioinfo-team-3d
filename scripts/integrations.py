"""Generate the public README inventory from core staff YAML (no local config reads).

python scripts/integrations.py --write
python scripts/integrations.py --check
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

import yaml

ROOT = Path(__file__).resolve().parents[1]
START = "<!-- integrations:start -->"
END = "<!-- integrations:end -->"
KINDS = ("내장 MCP", "외부 MCP", "Claude Code plugin", "skill", "엔진 기능")
LIFE_SCIENCES = "https://www.anthropic.com/news/healthcare-life-sciences"
# Descriptions/provenance only: membership always comes from staff configuration.
EXTERNAL = {
    "https://pubmed.mcp.claude.com/mcp": ("PubMed", "생의학 논문 검색", LIFE_SCIENCES),
    "https://hcls.mcp.claude.com/biorxiv/mcp": ("bioRxiv / medRxiv", "preprint 검색", LIFE_SCIENCES),
}
BUILTIN = {
    "approval": ("labhq_approval", "PI 승인 요청"),
    "hpc": ("labhq_hpc", "HPC 제출·감시 (scheduler가 none이 아닐 때)"),
}
ENGINES = {"claude_code": "Claude Code", "codex": "Codex", "gemini": "Gemini CLI",
           "antigravity": "Antigravity", "cli": "자체 CLI", "mock": "mock"}


def identifier(value: str) -> str:
    """Reject paths/markup instead of exposing arbitrary config text in public output."""
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.:-]{0,79}", value):
        raise ValueError("non-public identifier in staff configuration")
    return value


def link(label: str, target: str) -> str:
    return f"[{label}]({target})"


@dataclass
class Integration:
    kind: str
    name: str
    description: str
    staff: dict[str, str] = field(default_factory=dict)
    sources: set[str] = field(default_factory=set)


def collect(agents_dir: Path) -> list[Integration]:
    rows: dict[tuple[str, str, str], Integration] = {}

    def add(kind: str, name: str, description: str, staff: str, staff_source: str, *sources: str) -> None:
        key = (kind, name, description)
        row = rows.setdefault(key, Integration(kind, name, description))
        row.staff[staff] = staff_source
        row.sources.add(link("직원 설정", "agents/core/"))
        row.sources.update(sources)

    for path in sorted((agents_dir / "core").glob("*.yaml")):
        if path.name.startswith("_"):
            continue  # Same template exclusion as Registry.load().
        spec = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        staff = identifier(spec["id"])
        identifier(path.stem)
        source = link(staff, f"agents/core/{quote(path.name)}")
        engine = identifier(spec.get("engine", "claude_code"))
        tools = spec.get("tools", [])
        add("엔진 기능", ENGINES.get(engine, engine), "직원 실행 엔진", staff, source)

        for builtin in spec.get("builtin_mcp", ["approval"]):
            # Do not advertise an unimplemented name as a wired-in server.
            if builtin in BUILTIN:
                name, description = BUILTIN[builtin]
                add("내장 MCP", name, description, staff, source,
                    link("runner", "labhq/runner/daemon.py"))

        for server in spec.get("mcp", []):
            name = identifier(server["name"])
            public = EXTERNAL.get(server.get("url"))
            if public:
                title, description, provenance = public
                add("외부 MCP", title, description, staff, source,
                    link("Claude for Life Sciences", provenance), link("MCP", server["url"]))
            else:
                # Unknown endpoints/commands/env/headers may contain credentials or local paths.
                add("외부 MCP", name, "직원 설정의 MCP 서버", staff, source)

        if engine == "claude_code" and spec.get("plugin_dirs"):
            skills = [identifier(s) for s in spec.get("required_skills", [])]
            plugins = sorted({s.split(":", 1)[0] for s in skills})
            # BIOINFO_AGENT_DIR is a portable configured reference, never expanded/read.
            if "${BIOINFO_AGENT_DIR}" in spec["plugin_dirs"]:
                add("Claude Code plugin", "bioinfo-agent (`bioinfo`)",
                    "bioinfo plugin 로드 (plugin_dirs)", staff, source)
                plugins = [p for p in plugins if p != "bioinfo"]
            for plugin in plugins:
                add("Claude Code plugin", plugin, "필수 skill의 plugin (plugin_dirs)", staff, source)
            known_count = len(plugins) + ("${BIOINFO_AGENT_DIR}" in spec["plugin_dirs"])
            for index in range(max(0, len(spec["plugin_dirs"]) - known_count)):
                add("Claude Code plugin", f"plugin ({staff}:{index + 1})",
                    "직원 설정의 plugin_dirs (경로 비공개)", staff, source)
            for skill in skills:
                if spec.get("allow_skills"):
                    add("skill", skill, "실행 전 존재 확인 (required_skills)", staff, source)

        # The recruiter declares the skill in its prompt/tools; paper2agent.py invokes it.
        if (engine == "claude_code" and "Skill" in tools
                and re.search(r"\bpaper2agent\b", spec.get("system_prompt", ""), re.IGNORECASE)):
            add("skill", "Paper2Agent", "논문·코드를 파견직으로 변환 (setup-paper2agent 필요)", staff,
                source, link("채용 코드", "labhq/recruit/paper2agent.py"),
                link("Paper2Agent", "https://github.com/jmiao24/Paper2Agent"))

        if engine == "codex" and {"WebSearch", "WebFetch"}.intersection(tools):
            add("엔진 기능", "Codex 웹 검색", 'WebSearch / WebFetch → web_search="live"', staff,
                source, link("adapter", "labhq/adapters/codex.py"))

    return sorted(rows.values(), key=lambda r: (KINDS.index(r.kind), r.name, r.description))


def render(agents_dir: Path) -> str:
    rows = collect(agents_dir)
    counts = Counter(row.kind for row in rows)
    badges = []
    for kind, label, color in zip(KINDS, ("builtin MCP", "external MCP", "plugin", "skill", "engine"),
                                  ("5B5BD6", "007EC6", "8A2BE2", "228B22", "555555")):
        badges.append(f"![{label}: {counts[kind]}](https://img.shields.io/static/v1?"
                      f"label={quote(label)}&message={counts[kind]}&color={color})")
    lines = [" ".join(badges), "", "| 종류 | 이름 | 무엇 | 쓰는 직원 | 출처 |",
             "|---|---|---|---|---|"]
    for row in rows:
        staff = ", ".join(row.staff[s] for s in sorted(row.staff))
        lines.append(f"| {row.kind} | {row.name} | {row.description} | {staff} | "
                     + " · ".join(sorted(row.sources)) + " |")
    return "\n".join(lines) + "\n"


def update_readme(readme: str, content: str) -> str:
    if readme.count(START) != 1 or readme.count(END) != 1:
        raise ValueError("README needs exactly one integrations marker pair")
    before, tail = readme.split(START)
    if END not in tail:
        raise ValueError("integrations markers are out of order")
    _, after = tail.split(END)
    return before + START + "\n" + content + END + after


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    parser.add_argument("--root", type=Path, default=ROOT, help="repository root (also for fixtures)")
    args = parser.parse_args(argv)
    try:
        if not (args.root / "agents" / "core").is_dir():
            raise ValueError("agents/core is missing")
        path = args.root / "README.md"
        current = path.read_text(encoding="utf-8")
        expected = update_readme(current, render(args.root / "agents"))
        if args.write:
            path.write_text(expected, encoding="utf-8")
            return 0
        if current == expected:
            return 0
        print("README integrations are stale; run scripts/integrations.py --write", file=sys.stderr)
        return 1
    except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError):
        # Avoid echoing raw YAML/errors, which could include local paths or secrets.
        print("Cannot generate integrations: check staff YAML and README markers", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
