"""Public inventory follows staff configuration and rejects README drift."""
import subprocess
import sys

import pytest
import yaml

from scripts.integrations import END, ROOT, START, main, render


def staff(root, filename, **settings):
    core = root / "agents" / "core"
    core.mkdir(parents=True, exist_ok=True)
    (core / filename).write_text(yaml.safe_dump(settings), encoding="utf-8")


def readme(root):
    path = root / "README.md"
    path.write_text(f"before\n{START}\n{END}\nafter\n", encoding="utf-8")
    return path


def test_repository_readme_passes_cli_check():
    result = subprocess.run([sys.executable, str(ROOT / "scripts" / "integrations.py"), "--check"],
                            cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, result.stderr
    content = render(ROOT / "agents")
    assert "PubMed" in content and "bioRxiv / medRxiv" in content
    assert "bioinfo-agent (`bioinfo`)" in content and "Paper2Agent" in content
    assert "labhq_ask" not in content and "AlphaGenome" not in content


def test_temporary_inventory_deduplicates_and_uses_public_provenance(tmp_path, monkeypatch):
    # Plugin env, endpoint headers and shell paths are never read or copied into README.
    monkeypatch.setenv("BIOINFO_AGENT_DIR", "private-directory")
    for name in ("one", "two"):
        staff(tmp_path, f"{name}.yaml", id=name, engine="claude_code",
              builtin_mcp=["approval", "hpc"], plugin_dirs=["${BIOINFO_AGENT_DIR}"],
              allow_skills=True, required_skills=["bioinfo:bioinfo-analyze"],
              mcp=[{"name": "pubmed", "type": "http", "url": "https://pubmed.mcp.claude.com/mcp",
                    "headers": {"Authorization": "private-value"}}])
    staff(tmp_path, "web.yaml", id="web", engine="codex", builtin_mcp=[], tools=["WebSearch"])
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
    assert "message=2&color=5B5BD6" in content  # Unique built-in servers, not staff count.
    assert "message=1&color=007EC6" in content
    assert all(secret not in content for secret in ("private-value", "private-directory", "ignored"))


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
    assert text.startswith(f"before\n{START}\n") and text.endswith(f"{END}\nafter\n")
    assert "Codex 웹 검색" in text
    assert main([*args, "--write"]) == 0
    assert path.read_text(encoding="utf-8") == text


def test_unknown_connections_do_not_publish_private_fields(tmp_path):
    staff(tmp_path, "custom.yaml", id="custom", engine="claude_code", builtin_mcp=[],
          plugin_dirs=["/private/plugins/tool"], mcp=[{
              "name": "custom_mcp", "type": "http", "url": "https://private.invalid/mcp?key=private-value",
              "command": "/private/python", "env": {"ACCOUNT": "private-account"}}])
    content = render(tmp_path / "agents")
    assert "custom_mcp" in content and "plugin (custom:1)" in content
    assert "private" not in content


@pytest.mark.parametrize("markers", ["no markers", f"{END}\n{START}", f"{START}{START}{END}"])
def test_bad_markers_fail_without_writing(tmp_path, markers):
    staff(tmp_path, "one.yaml", id="one")
    path = tmp_path / "README.md"
    path.write_text(markers, encoding="utf-8")
    assert main(["--root", str(tmp_path), "--write"]) == 1
    assert path.read_text(encoding="utf-8") == markers


def test_removed_configuration_removes_badges_and_rows(tmp_path):
    staff(tmp_path, "one.yaml", id="one", engine="codex", builtin_mcp=[], tools=["WebFetch"])
    path = tmp_path / "agents" / "core" / "one.yaml"
    assert "Codex 웹 검색" in render(tmp_path / "agents")
    path.unlink()
    content = render(tmp_path / "agents")
    assert "Codex 웹 검색" not in content and "message=0" in content
