"""Public inventory and README-top badges follow repository configuration and reject README drift."""
import re
import subprocess
import sys

import pytest
import yaml

from scripts import integrations
from scripts.integrations import BADGES_END, BADGES_START, END, ROOT, START, Look, badges, main, render


def staff(root, filename, **settings):
    core = root / "agents" / "core"
    core.mkdir(parents=True, exist_ok=True)
    (core / filename).write_text(yaml.safe_dump(settings), encoding="utf-8")


def readme(root):
    path = root / "README.md"
    path.write_text(f"# title\n{BADGES_START}\n{BADGES_END}\nbefore\n{START}\n{END}\nafter\n",
                    encoding="utf-8")
    return path


def block(text, start, end):
    return text.split(start, 1)[1].split(end, 1)[0]


def labels(content):
    """Badge alt texts, i.e. '<target>: <role>' per badge."""
    return re.findall(r"\[!\[([^\]]+)\]\(", content)


def test_repository_readme_passes_cli_check():
    result = subprocess.run([sys.executable, str(ROOT / "scripts" / "integrations.py"), "--check"],
                            cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, result.stderr
    content = render(ROOT / "agents")
    assert "PubMed" in content and "bioRxiv / medRxiv" in content
    assert "bioinfo-agent (`bioinfo`)" in content and "Paper2Agent" in content
    assert "labhq_ask" in content and "AlphaGenome" not in content  # labhq_ask is wired by the runner (#39)
    assert "img.shields.io" not in content  # count badges are gone from §2; targets live at the top


def test_repository_badges_name_exact_targets_at_readme_top():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    title, rest = text.split("\n", 1)
    assert title.startswith("# labhq") and rest.lstrip("\n").startswith(BADGES_START)
    top = labels(badges(ROOT))
    names = [label.split(": ", 1)[0] for label in top]
    assert names[:2] == ["Claude Code", "Codex"]  # engines first, most staff first
    for target in ("SGE", "PBS", "SLURM", "labhq MCP", "PubMed", "bioRxiv / medRxiv", "bioinfo-agent",
                   "Paper2Agent", "test", "Python"):
        assert names.count(target) == 1, target
    assert names.index("SGE") < names.index("PBS") < names.index("SLURM") < names.index("labhq MCP")
    # No staff uses Gemini/Antigravity, mock/none are not schedulers,
    # and plugin skills / engine features are not separate targets.
    for absent in ("Gemini", "Antigravity", "MOCK", "mock", "NONE",
                   "Codex 웹 검색", "bioinfo:bioinfo-analyze", "PR gate"):
        assert all(absent not in label for label in top), absent
    assert "Codex: 직원 3명 · gpt-6-astra/gpt-6-luna/gpt-6.1-sol" in top
    assert "labhq MCP: approval · ask · hpc" in top and "Python: 3.10+" in top
    assert "코드: GPL-3.0" in top and "문서·데이터: CC BY-SA 4.0" in top  # #252: one badge per license file
    generated = block(text, BADGES_START, BADGES_END)
    assert str(ROOT) not in text and not re.search(r"(?<![A-Za-z])[A-Za-z]:[\\/]|/Users/|/home/", generated)


def test_badge_anchors_point_at_readme_headings():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    # GitHub heading ids: lowercase, punctuation dropped, spaces to hyphens.
    slugs = {"#" + re.sub(r"[^\w\- ]", "", h.strip().lower()).replace(" ", "-")
             for h in re.findall(r"^#{1,6} (.+)$", text, re.MULTILINE)}
    used = set(re.findall(r"\]\((#[^)]+)\)", block(text, BADGES_START, BADGES_END)))
    anchors = {integrations.TOOLS_ANCHOR, integrations.HPC_ANCHOR, integrations.CLI_ANCHOR}
    assert used and used <= anchors <= slugs


def test_temporary_inventory_deduplicates_and_uses_public_provenance(tmp_path, monkeypatch):
    # Plugin env, endpoint headers and shell paths are never read or copied into README.
    monkeypatch.setenv("BIOINFO_AGENT_DIR", "private-directory")
    for name in ("one", "two"):
        staff(tmp_path, f"{name}.yaml", id=name, engine="claude_code", model="opus",
              builtin_mcp=["approval", "hpc"], plugin_dirs=["${BIOINFO_AGENT_DIR}"],
              allow_skills=True, required_skills=["bioinfo:bioinfo-analyze"],
              mcp=[{"name": "pubmed", "type": "http", "url": "https://pubmed.mcp.claude.com/mcp",
                    "headers": {"Authorization": "private-value"}}])
    staff(tmp_path, "web.yaml", id="web", engine="codex", model="gpt-6-luna", builtin_mcp=[],
          tools=["WebSearch"])
    staff(tmp_path, "recruiter.yaml", id="recruiter", engine="claude_code", builtin_mcp=[],
          tools=["Skill"], system_prompt="Use the paper2agent skill.")
    staff(tmp_path, "_ignored.yaml", id="ignored", builtin_mcp=["approval"])
    contract = tmp_path / "agents" / "contract"
    contract.mkdir()
    (contract / "example.yaml.example").write_text("not valid YAML: [", encoding="utf-8")
    content = render(tmp_path / "agents")
    assert content.count("| 외부 MCP | PubMed |") == 1
    assert "[one](agents/core/one.yaml), [two](agents/core/two.yaml)" in content
    assert "Claude for Life Sciences" in content and "anthropic.com/news/healthcare-life-sciences" in content
    assert "labhq_hpc" in content and "scheduler가 none이 아닐 때" in content
    assert "bioinfo:bioinfo-analyze" in content and "Paper2Agent" in content
    assert "Codex 웹 검색" in content
    top = badges(tmp_path)
    assert labels(top) == [
        "Claude Code: 직원 3명 · opus",  # staff count, not row count; recruiter has no model
        "Codex: 직원 1명 · gpt-6-luna",
        "labhq MCP: approval · ask · hpc",  # unique built-in servers in one badge, not per staff
        "PubMed: MCP · 논문 검색",
        "bioinfo-agent: Claude Code plugin",
        "Paper2Agent: skill · 파견직 채용",
    ]
    assert "](https://pubmed.ncbi.nlm.nih.gov/)" in top and "logo=pubmed" in top
    assert "](https://github.com/ehojune/bioinfo-agent)" in top
    assert "](https://github.com/jmiao24/Paper2Agent)" in top
    for secret in ("private-value", "private-directory", "ignored", "BIOINFO_AGENT_DIR", str(tmp_path)):
        assert secret not in content + top, secret
    assert "pubmed.mcp.claude.com" in content and "pubmed.mcp.claude.com" not in top  # badge → official page


def test_config_change_fails_check_and_write_preserves_surrounding_text(tmp_path):
    staff(tmp_path, "staff.yaml", id="staff", engine="codex", builtin_mcp=[], tools=[])
    path = readme(tmp_path)
    args = ["--root", str(tmp_path)]
    assert main([*args, "--write"]) == 0
    original = path.read_bytes()
    assert main([*args, "--check"]) == 0
    staff(tmp_path, "staff.yaml", id="staff", engine="codex", builtin_mcp=[], tools=["WebSearch"])
    assert main([*args, "--check"]) == 1
    result = subprocess.run([sys.executable, str(ROOT / "scripts" / "integrations.py"),
                             *args, "--check"], capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 1
    assert path.read_bytes() == original  # Check is read-only.
    assert main([*args, "--write"]) == 0
    assert main([*args, "--check"]) == 0
    text = path.read_text(encoding="utf-8")
    assert text.startswith(f"# title\n{BADGES_START}\n") and text.endswith(f"{END}\nafter\n")
    assert f"{BADGES_END}\nbefore\n{START}\n" in text
    assert "Codex 웹 검색" in text
    assert main([*args, "--write"]) == 0
    assert path.read_text(encoding="utf-8") == text
    # A badge-only change (another Codex staff) also makes --check fail.
    staff(tmp_path, "second.yaml", id="second", engine="codex", builtin_mcp=[], tools=["WebSearch"])
    assert main([*args, "--check"]) == 1
    assert main([*args, "--write"]) == 0
    assert "Codex: 직원 2명" in block(path.read_text(encoding="utf-8"), BADGES_START, BADGES_END)


def test_unknown_connections_do_not_publish_private_fields(tmp_path):
    staff(tmp_path, "custom.yaml", id="custom", engine="claude_code", builtin_mcp=[],
          plugin_dirs=["/private/plugins/tool"], mcp=[{
              "name": "custom_mcp", "type": "http", "url": "https://private.invalid/mcp?key=private-value",
              "command": "/private/python", "env": {"ACCOUNT": "private-account"}}])
    content = render(tmp_path / "agents")
    top = badges(tmp_path)
    assert "custom_mcp" in content and "plugin (custom:1)" in content
    assert "custom_mcp: MCP" in labels(top)  # named server, linked to the README table
    assert "](#연결된-도구)" in top
    assert "plugin (custom" not in top  # an unnamed plugin directory gets no badge
    assert "private" not in content + top


@pytest.mark.parametrize("field, value", [("model", "/private/models/opus"), ("model", "opus\n[x](y)"),
                                          ("engine", "C:/private/engine")])
def test_non_identifier_staff_values_fail_without_writing(tmp_path, field, value):
    staff(tmp_path, "one.yaml", id="one", **{field: value})
    path = readme(tmp_path)
    original = path.read_bytes()
    assert main(["--root", str(tmp_path), "--write"]) == 1
    assert path.read_bytes() == original


@pytest.mark.parametrize("markers", [
    "no markers", f"{END}\n{START}", f"{START}{START}{END}",
    f"{START}\n{END}",  # badge markers missing
    f"{BADGES_END}\n{BADGES_START}\n{START}\n{END}",
    f"{BADGES_START}\n{BADGES_START}\n{BADGES_END}\n{START}\n{END}",
    f"{START}\n{BADGES_START}\n{END}\n{BADGES_END}",  # overlapping pairs
    f"{BADGES_START}\n{START}\n{BADGES_END}\n{END}",
    f"{START}\n{BADGES_START}\n{BADGES_END}\n{END}",  # badges nested in integrations (#153)
    f"{BADGES_START}\n{START}\n{END}\n{BADGES_END}",  # integrations nested in badges
])
def test_bad_markers_fail_without_writing(tmp_path, markers):
    staff(tmp_path, "one.yaml", id="one")
    path = tmp_path / "README.md"
    path.write_text(markers, encoding="utf-8")
    assert main(["--root", str(tmp_path), "--write"]) == 1
    assert path.read_text(encoding="utf-8") == markers
    assert main(["--root", str(tmp_path), "--check"]) == 1


def test_removed_configuration_removes_badges_and_rows(tmp_path):
    staff(tmp_path, "one.yaml", id="one", engine="codex", builtin_mcp=[], tools=["WebFetch"])
    staff(tmp_path, "two.yaml", id="two", engine="gemini", builtin_mcp=[])
    staff(tmp_path, "fake.yaml", id="fake", engine="mock", builtin_mcp=[])
    path = tmp_path / "agents" / "core" / "one.yaml"
    assert "Codex 웹 검색" in render(tmp_path / "agents")
    top = badges(tmp_path)
    assert "Codex: 직원 1명" in labels(top) and "logo=googlegemini" in top
    assert all("mock" not in label for label in labels(top))  # mock engine is not a real target
    path.unlink()
    (tmp_path / "agents" / "core" / "two.yaml").unlink()
    content = render(tmp_path / "agents")
    top = badges(tmp_path)
    assert "Codex 웹 검색" not in content and "Codex" not in top and "Gemini" not in top
    assert "labhq MCP: ask" in labels(top)  # mock staff still gets the runner's labhq_ask


def hpc_settings(root, literal):
    package = root / "labhq"
    package.mkdir(exist_ok=True)
    (package / "settings.py").write_text(
        "from typing import Literal\n\n\nclass HpcSettings:\n"
        f"    scheduler: {literal} = 'sge'\n", encoding="utf-8")


def test_schedulers_come_from_settings_literal(tmp_path):
    staff(tmp_path, "one.yaml", id="one", builtin_mcp=[])
    assert all("scheduler" not in label for label in labels(badges(tmp_path)))  # no settings.py
    hpc_settings(tmp_path, 'Literal["sge", "slurm", "mock", "none"]')
    top = labels(badges(tmp_path))
    assert "SGE: HPC scheduler" in top and "SLURM: HPC scheduler" in top
    assert not any(label.startswith(("MOCK", "NONE")) for label in top)
    hpc_settings(tmp_path, 'Literal["pbs"]')
    assert [label for label in labels(badges(tmp_path)) if "scheduler" in label] == ["PBS: HPC scheduler"]
    hpc_settings(tmp_path, "str")  # the support list must stay readable from code, not silently empty
    path = readme(tmp_path)
    assert main(["--root", str(tmp_path), "--write"]) == 1
    hpc_settings(tmp_path, 'Literal["../private"]')
    assert main(["--root", str(tmp_path), "--write"]) == 1
    assert path.read_text(encoding="utf-8") == f"# title\n{BADGES_START}\n{BADGES_END}\nbefore\n{START}\n{END}\nafter\n"


def test_repository_facts_follow_pyproject_workflows_and_license(tmp_path):
    staff(tmp_path, "one.yaml", id="one", builtin_mcp=[])
    assert badges(tmp_path).count("[![") == 2  # engine + labhq MCP; no repository facts without files
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nrequires-python = ">=3.11"\n\n[project.urls]\n'
        'Repository = "https://github.com/owner/repo"\n', encoding="utf-8")
    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "ci.yml").write_text("name: ci\non: [push, pull_request]\njobs: {}\n", encoding="utf-8")
    (workflows / "manual.yml").write_text("name: manual\non:\n  workflow_dispatch:\njobs: {}\n", encoding="utf-8")
    top = badges(tmp_path)
    assert "[![ci: GitHub Actions](https://github.com/owner/repo/actions/workflows/ci.yml/badge.svg)]" in top
    assert "manual" not in top  # manual-only workflows have no branch status
    assert "Python: 3.11+" in labels(top) and "license" not in top and "패치노트" not in top
    (tmp_path / "LICENSE").write_text("MIT License\n", encoding="utf-8")
    (tmp_path / "patch_notes").mkdir()
    (tmp_path / "patch_notes" / "README.md").write_text("notes\n", encoding="utf-8")
    top = badges(tmp_path)
    assert "[![license](https://img.shields.io/github/license/owner/repo)](LICENSE)" in top
    assert "패치노트: changelog" in labels(top)
    targets, facts = top.rstrip("\n").split("\n\n")  # tools first, repository facts in a second row
    assert "Python" in facts and "Python" not in targets
    (tmp_path / "pyproject.toml").write_text('[project]\nrequires-python = "~=3.10"\n', encoding="utf-8")
    path = readme(tmp_path)
    assert main(["--root", str(tmp_path), "--write"]) == 1  # not a plain minimum: refuse to guess


def test_logos_are_limited_to_verified_slugs():
    assert Look("x", "#x", "python").logo == "python"
    with pytest.raises(ValueError):
        Look("x", "#x", "openai")  # draws nothing on shields.io
