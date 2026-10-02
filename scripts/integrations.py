"""Generate the public README badges and inventory from repository files (no local config reads).

python scripts/integrations.py --write
python scripts/integrations.py --check
"""
from __future__ import annotations

import argparse
import ast
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

import yaml

ROOT = Path(__file__).resolve().parents[1]
START = "<!-- integrations:start -->"
END = "<!-- integrations:end -->"
BADGES_START = "<!-- badges:start -->"
BADGES_END = "<!-- badges:end -->"
KINDS = ("내장 MCP", "외부 MCP", "Claude Code plugin", "skill", "엔진 기능")
LIFE_SCIENCES = "https://www.anthropic.com/news/healthcare-life-sciences"
# README anchors for targets without an official page (test_badge_links_resolve checks them).
TOOLS_ANCHOR = "#연결된-도구"
HPC_ANCHOR = "#8-설정-포인트"
CLI_ANCHOR = "#bioinfo-agent-연결하기"


# simple-icons slugs checked to draw a logo on shields.io (badge SVG has <image>, 2026-10-01).
# openai, biorxiv, medrxiv and ncbi drew nothing, so Codex and bioRxiv badges carry no logo.
# Generation stays offline: re-check a slug by hand before adding it here.
LOGOS = frozenset({"claude", "googlegemini", "modelcontextprotocol", "pubmed", "python"})


@dataclass(frozen=True)
class Look:
    """One README-top badge: one concrete target, never a category count."""
    role: str
    url: str
    logo: str = ""
    color: str = "555555"
    label: str = ""  # badge label when the inventory row name carries extra markup

    def __post_init__(self) -> None:
        if self.logo and self.logo not in LOGOS:
            raise ValueError("unverified shields.io logo")


# Descriptions/provenance only: membership always comes from staff configuration.
EXTERNAL = {
    "https://pubmed.mcp.claude.com/mcp": (
        "PubMed", "생의학 논문 검색", LIFE_SCIENCES,
        Look("MCP · 논문 검색", "https://pubmed.ncbi.nlm.nih.gov/", "pubmed", "007EC6")),
    "https://hcls.mcp.claude.com/biorxiv/mcp": (
        "bioRxiv / medRxiv", "preprint 검색", LIFE_SCIENCES,
        Look("MCP · preprint 검색", "https://www.biorxiv.org/", "", "007EC6")),
}
BUILTIN = {
    "approval": ("labhq_approval", "PI 승인 요청"),
    "hpc": ("labhq_hpc", "HPC 제출·감시 (scheduler가 none이 아닐 때)"),
}
ENGINES = {"claude_code": "Claude Code", "codex": "Codex", "gemini": "Gemini CLI",
           "antigravity": "Antigravity", "cli": "자체 CLI", "mock": "mock"}
# The role text (staff count, models) is filled from staff YAML. mock is not a real engine: no badge.
ENGINE_LOOKS = {
    "claude_code": Look("", "https://code.claude.com/docs/en/overview", "claude", "D97757"),
    "codex": Look("", "https://github.com/openai/codex", "", "10A37F"),
    "gemini": Look("", "https://github.com/google-gemini/gemini-cli", "googlegemini", "8E75B2"),
    "antigravity": Look("", "https://antigravity.google/", "", "4285F4"),
    "cli": Look("", CLI_ANCHOR, "", "6E6E6E"),
}
BUILTIN_LOOK = Look("", TOOLS_ANCHOR, "modelcontextprotocol", "5B5BD6", "labhq MCP")
SCHEDULER_LOOK = Look("HPC scheduler", HPC_ANCHOR, "", "2F6F9F")
PLUGIN_COLOR, SKILL_COLOR = "8A2BE2", "228B22"


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
    look: Look | None = None  # None: no badge of its own (unnamed plugin, plugin skill, engine feature)
    models: set[str] = field(default_factory=set)


def collect(agents_dir: Path) -> list[Integration]:
    rows: dict[tuple[str, str, str], Integration] = {}

    def add(kind: str, name: str, description: str, staff: str, staff_source: str, *sources: str,
            look: Look | None = None) -> Integration:
        key = (kind, name, description)
        row = rows.setdefault(key, Integration(kind, name, description, look=look))
        row.staff[staff] = staff_source
        row.sources.add(link("직원 설정", "agents/core/"))
        row.sources.update(sources)
        return row

    for path in sorted((agents_dir / "core").glob("*.yaml")):
        if path.name.startswith("_"):
            continue  # Same template exclusion as Registry.load().
        spec = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        staff = identifier(spec["id"])
        identifier(path.stem)
        source = link(staff, f"agents/core/{quote(path.name)}")
        engine = identifier(spec.get("engine", "claude_code"))
        tools = spec.get("tools", [])
        row = add("엔진 기능", ENGINES.get(engine, engine), "직원 실행 엔진", staff, source,
                  look=None if engine == "mock" else ENGINE_LOOKS.get(engine, Look("", TOOLS_ANCHOR)))
        if spec.get("model") is not None:
            row.models.add(identifier(spec["model"]))

        for builtin in spec.get("builtin_mcp", ["approval"]):
            # Do not advertise an unimplemented name as a wired-in server.
            if builtin in BUILTIN:
                name, description = BUILTIN[builtin]
                add("내장 MCP", name, description, staff, source,
                    link("runner", "labhq/runner/daemon.py"), look=BUILTIN_LOOK)
        if engine != "antigravity":  # the runner wires labhq_ask into every other staff session
            add("내장 MCP", "labhq_ask", "막히면 CSO·시설팀·동료·PI에게 묻고 같은 세션으로 이어 가기 (CSO 먼저, 위험한 것만 PI)",
                staff, source, link("runner", "labhq/runner/daemon.py"), look=BUILTIN_LOOK)

        for server in spec.get("mcp", []):
            name = identifier(server["name"])
            public = EXTERNAL.get(server.get("url"))
            if public:
                title, description, provenance, look = public
                add("외부 MCP", title, description, staff, source,
                    link("Claude for Life Sciences", provenance), link("MCP", server["url"]), look=look)
            else:
                # Unknown endpoints/commands/env/headers may contain credentials or local paths.
                add("외부 MCP", name, "직원 설정의 MCP 서버", staff, source,
                    look=Look("MCP", TOOLS_ANCHOR, "", "007EC6"))

        if engine == "claude_code" and spec.get("plugin_dirs"):
            skills = [identifier(s) for s in spec.get("required_skills", [])]
            plugins = sorted({s.split(":", 1)[0] for s in skills})
            # BIOINFO_AGENT_DIR is a portable configured reference, never expanded/read.
            if "${BIOINFO_AGENT_DIR}" in spec["plugin_dirs"]:
                add("Claude Code plugin", "bioinfo-agent (`bioinfo`)",
                    "bioinfo plugin 로드 (plugin_dirs)", staff, source,
                    look=Look("Claude Code plugin", "https://github.com/ehojune/bioinfo-agent", "",
                              PLUGIN_COLOR, "bioinfo-agent"))
                plugins = [p for p in plugins if p != "bioinfo"]
            for plugin in plugins:
                add("Claude Code plugin", plugin, "필수 skill의 plugin (plugin_dirs)", staff, source,
                    look=Look("Claude Code plugin", TOOLS_ANCHOR, "", PLUGIN_COLOR))
            known_count = len(plugins) + ("${BIOINFO_AGENT_DIR}" in spec["plugin_dirs"])
            for index in range(max(0, len(spec["plugin_dirs"]) - known_count)):
                # Unnamed plugin directories stay in the table only: a badge would name nothing.
                add("Claude Code plugin", f"plugin ({staff}:{index + 1})",
                    "직원 설정의 plugin_dirs (경로 비공개)", staff, source)
            for skill in skills:
                if spec.get("allow_skills"):
                    # plugin:skill is shown by its plugin's badge; a bare skill gets its own.
                    add("skill", skill, "실행 전 존재 확인 (required_skills)", staff, source,
                        look=None if ":" in skill else Look("Claude Code skill", TOOLS_ANCHOR, "", SKILL_COLOR))

        # The recruiter declares the skill in its prompt/tools; paper2agent.py invokes it.
        if (engine == "claude_code" and "Skill" in tools
                and re.search(r"\bpaper2agent\b", spec.get("system_prompt", ""), re.IGNORECASE)):
            add("skill", "Paper2Agent", "논문·코드를 파견직으로 변환 (setup-paper2agent 필요)", staff,
                source, link("채용 코드", "labhq/recruit/paper2agent.py"),
                link("Paper2Agent", "https://github.com/jmiao24/Paper2Agent"),
                look=Look("skill · 파견직 채용", "https://github.com/jmiao24/Paper2Agent", "", SKILL_COLOR))

        if engine == "codex" and {"WebSearch", "WebFetch"}.intersection(tools):
            # A Codex feature, not a separate target: the Codex badge covers it.
            add("엔진 기능", "Codex 웹 검색", 'WebSearch / WebFetch → web_search="live"', staff,
                source, link("adapter", "labhq/adapters/codex.py"))

    return sorted(rows.values(), key=lambda r: (KINDS.index(r.kind), r.name, r.description))


def render(agents_dir: Path) -> str:
    lines = ["| 종류 | 이름 | 무엇 | 쓰는 직원 | 출처 |", "|---|---|---|---|---|"]
    for row in collect(agents_dir):
        staff = ", ".join(row.staff[s] for s in sorted(row.staff))
        lines.append(f"| {row.kind} | {row.name} | {row.description} | {staff} | "
                     + " · ".join(sorted(row.sources)) + " |")
    return "\n".join(lines) + "\n"


def shield(label: str, message: str, look: Look) -> str:
    query = f"label={quote(label, safe='')}&message={quote(message, safe='')}&color={look.color}"
    if look.logo:
        query += f"&logo={look.logo}"
    return f"[![{label}: {message}](https://img.shields.io/static/v1?{query})]({look.url})"


def schedulers(root: Path) -> list[str]:
    """Supported schedulers from HpcSettings.scheduler's Literal, read with ast (no import)."""
    path = root / "labhq" / "settings.py"
    if not path.is_file():
        return []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ClassDef) and node.name == "HpcSettings":
            for item in node.body:
                if (isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name)
                        and item.target.id == "scheduler" and isinstance(item.annotation, ast.Subscript)
                        and getattr(item.annotation.value, "id", None) == "Literal"):
                    values = item.annotation.slice
                    values = values.elts if isinstance(values, ast.Tuple) else [values]
                    names = [identifier(v.value) for v in values if isinstance(v, ast.Constant)]
                    return [n for n in names if n not in ("mock", "none")]  # not real schedulers
    raise ValueError("HpcSettings.scheduler Literal is missing")


def license_name(path: Path) -> str | None:
    """Short name of a license file written from an official text, or None when it is not one we know."""
    head = path.read_text(encoding="utf-8")[:400]
    if "GNU GENERAL PUBLIC LICENSE" in head and "Version 3" in head:
        return "GPL-3.0"
    if "Attribution-ShareAlike 4.0 International" in head:
        return "CC BY-SA 4.0"
    return None


def repository_facts(root: Path) -> list[str]:
    """CI, Python and license badges from pyproject.toml, .github/workflows and LICENSE."""
    pyproject = root / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8") if pyproject.is_file() else ""
    facts = []
    repo = re.search(r'^Repository\s*=\s*"https://github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)"\s*$',
                     text, re.MULTILINE)
    if repo:
        actions = f"https://github.com/{repo.group(1)}/actions/workflows"
        workflows = root / ".github" / "workflows"
        for path in sorted([*workflows.glob("*.yml"), *workflows.glob("*.yaml")]):
            spec = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            triggers = spec.get("on", spec.get(True, ()))  # YAML 1.1 reads a bare `on` key as True
            triggers = {triggers} if isinstance(triggers, str) else set(triggers or ())
            if not triggers & {"push", "pull_request"}:
                continue  # manual-only workflows (pr-gate) have no meaningful branch status
            name = spec.get("name")
            if not (isinstance(name, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 _.()+-]{0,59}", name)):
                name = identifier(path.stem)
            file = quote(identifier(path.name))
            facts.append(f"[![{name}: GitHub Actions]({actions}/{file}/badge.svg)]({actions}/{file})")
    python = re.search(r'^requires-python\s*=\s*"([^"]*)"\s*$', text, re.MULTILINE)
    if python:
        minimum = re.fullmatch(r">=\s*(\d+(?:\.\d+)*)", python.group(1).strip())
        if not minimum:
            raise ValueError("requires-python is not a plain minimum version")
        python_look = Look("", "https://www.python.org/", "python", "3776AB")
        facts.append(shield("Python", f"{minimum.group(1)}+", python_look))
    # Code under LICENSE, docs and data under LICENSE-CC-BY-SA-4.0.txt (#252); named from the official texts.
    docs = root / "LICENSE-CC-BY-SA-4.0.txt"
    for path, scope in ((root / "LICENSE", "코드" if docs.is_file() else "라이선스"), (docs, "문서·데이터")):
        name = license_name(path) if path.is_file() else None
        if name:
            facts.append(shield(scope, name, Look("", path.name, "", "555555")))
        elif path.name == "LICENSE" and path.is_file() and repo:  # another license: GitHub names it
            facts.append(f"[![license](https://img.shields.io/github/license/{repo.group(1)})](LICENSE)")
    if (root / "patch_notes" / "README.md").is_file():
        facts.append(shield("패치노트", "changelog", Look("", "patch_notes/README.md", "", "5B5BD6")))
    return facts


def badges(root: Path) -> str:
    rows = collect(root / "agents")
    targets = []
    engines = [r for r in rows if r.kind == "엔진 기능" and r.look]
    for row in sorted(engines, key=lambda r: (-len(r.staff), r.name)):
        models = f" · {'/'.join(sorted(row.models))}" if row.models else ""
        targets.append(shield(row.name, f"직원 {len(row.staff)}명{models}", row.look))
    targets += [shield(name.upper(), SCHEDULER_LOOK.role, SCHEDULER_LOOK) for name in schedulers(root)]
    builtin = sorted(r.name.removeprefix("labhq_") for r in rows if r.kind == "내장 MCP")
    if builtin:
        targets.append(shield(BUILTIN_LOOK.label, " · ".join(builtin), BUILTIN_LOOK))
    for row in rows:
        if row.kind in ("외부 MCP", "Claude Code plugin", "skill") and row.look:
            targets.append(shield(row.look.label or row.name, row.look.role, row.look))
    blocks = [list(dict.fromkeys(targets)), repository_facts(root)]
    return "\n\n".join("\n".join(block) for block in blocks if block) + "\n"


def marker_span(readme: str, start: str, end: str) -> tuple[int, int]:
    """Where one generated block sits, start marker through end marker."""
    if readme.count(start) != 1 or readme.count(end) != 1:
        raise ValueError("README needs exactly one marker pair")
    first, last = readme.index(start), readme.index(end)
    if last < first:
        raise ValueError("README markers are out of order")
    return first, last + len(end)


def replace_block(readme: str, start: str, end: str, content: str) -> str:
    first, last = marker_span(readme, start, end)
    return readme[:first] + start + "\n" + content + end + readme[last:]


def update_readme(readme: str, badge_block: str, inventory: str) -> str:
    # Each block is replaced whole, so a pair nested in or crossing the other would be erased (#153).
    (_, first_end), (second_start, _) = sorted([marker_span(readme, BADGES_START, BADGES_END),
                                                marker_span(readme, START, END)])
    if first_end > second_start:
        raise ValueError("README marker blocks overlap")
    readme = replace_block(readme, BADGES_START, BADGES_END, badge_block)
    return replace_block(readme, START, END, inventory)


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
        expected = update_readme(current, badges(args.root), render(args.root / "agents"))
        if args.write:
            path.write_text(expected, encoding="utf-8")
            return 0
        if current == expected:
            return 0
        print("README integrations are stale; run scripts/integrations.py --write", file=sys.stderr)
        return 1
    except (OSError, ValueError, KeyError, TypeError, AttributeError, SyntaxError, yaml.YAMLError):
        # Avoid echoing raw YAML/errors, which could include local paths or secrets.
        print("Cannot generate integrations: check staff YAML, repository files and README markers",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
