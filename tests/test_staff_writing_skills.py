import re
import xml.etree.ElementTree as ET
from pathlib import Path

import yaml

from labhq.adapters import get_adapter
from labhq.adapters.base import RunContext
from labhq.korean_qc import check_document, check_preservation
from labhq.adapters.read_only import read_only_profile
from labhq.models import AgentSpec, Engine, Task
from labhq.plugin_checks import read_plugin_manifest, skill_file
from labhq.runner.workspace import TaskWorkspace
from labhq.settings import Settings
from labhq.staff_skills import install_required_skills


ROOT = Path(__file__).parents[1]
SKILLS = ROOT / "labhq" / "skills"
TOKEN_COLORS = {
    "#FFFFFF", "#F8FAFC", "#0F172A", "#475569", "#CBD5E1",
    "#F59E0B", "#D97706", "#6366F1", "#16A34A", "#7C3AED",
    "#FEF3C7", "#EEF2FF", "#ECFDF5", "#F5F3FF",
}


def agent(name: str) -> dict:
    return yaml.safe_load((ROOT / "agents" / "core" / f"{name}.yaml").read_text(encoding="utf-8"))


async def _emit(_kind, _data):
    pass


def test_bundled_writing_skills_have_their_contract_and_license_files():
    humanizer = SKILLS / "humanizer"
    graphical = SKILLS / "graphical-abstract"
    for path in (
        humanizer / "SKILL.md", humanizer / "LICENSE", humanizer / "NOTICE",
        humanizer / "references" / "vocabulary-patterns.md", humanizer / "examples" / "before-1.md",
        graphical / "SKILL.md", graphical / "templates" / "graphical-abstract.svg",
    ):
        assert path.is_file(), path
    entry = (humanizer / "SKILL.md").read_text(encoding="utf-8")
    vocabulary = (humanizer / "references" / "vocabulary-patterns.md").read_text(encoding="utf-8")
    notice = (humanizer / "NOTICE").read_text(encoding="utf-8")
    license_text = (humanizer / "LICENSE").read_text(encoding="utf-8")
    assert 'version: "1.6.1-local"' in entry and "41가지" in entry
    assert "패턴 41" in vocabulary and all(word in vocabulary for word in ("비유의", "유의차", "미보정"))
    assert "DaleSeo/korean-skills" in notice and "1.6.0" in notice and "1.6.1-local" in notice and "패턴 41" in notice
    assert "MIT License" in license_text and "Copyright (c) 2026 Dale Seo" in license_text
    graphical_text = (graphical / "SKILL.md").read_text(encoding="utf-8")
    for required in ("질문", "자료", "방법", "핵심 결과", "결론", "<title>", "<desc>",
                     "두 방향", "세 관점", "잘림", "겹침", "PNG", "캡처"):
        assert required in graphical_text


def test_staff_require_the_writing_and_figure_skills_and_short_messages_have_inline_rules():
    for name in ("cso", "analyst", "editor"):
        required = set(agent(name).get("required_skills", []))
        assert {"humanizer", "graphical-abstract"} <= required, name
    for name in ("biologist", "qc_reviewer"):
        assert "humanizer" in agent(name).get("required_skills", []), name
    short_rules = agent("chief_of_staff")["system_prompt"]
    for expression in ("되어진다", "가지고 있다", "에 있어서", "비유의", "유의차", "미보정"):
        assert expression in short_rules
    pi_language = (ROOT / "labhq" / "pi_language.py").read_text(encoding="utf-8")
    assert "비유의" in pi_language and "보정 전" in pi_language


def test_bundled_required_skill_is_copied_for_claude_and_codex(tmp_path):
    task = Task(id="t", request_id="r", agent_id="writer", prompt="write")
    for engine in (Engine.claude_code, Engine.codex):
        staff = AgentSpec(id="writer", name="writer", role="writer", engine=engine,
                          allow_skills=engine == Engine.claude_code,
                          required_skills=["humanizer"])
        workspace = TaskWorkspace(tmp_path / engine.value, task, staff)
        assert install_required_skills(workspace, staff) is None
        for base in (".claude/skills", ".agents/skills"):
            assert (workspace.dir / base / "humanizer" / "SKILL.md").is_file()

    forced_mock = staff.model_copy(update={"engine": Engine.mock})
    assert read_only_profile(forced_mock).required_skills == []


def test_bundled_skill_reaches_each_engine_loader(tmp_path):
    task = Task(id="t", request_id="r", agent_id="writer", prompt="write")
    claude = AgentSpec(id="writer", name="writer", role="writer", engine=Engine.claude_code,
                       allow_skills=True, required_skills=["humanizer"])
    claude_workspace = TaskWorkspace(tmp_path / "claude", task, claude)
    assert install_required_skills(claude_workspace, claude) is None
    claude_ctx = RunContext(task=task, agent=claude, workdir=claude_workspace.dir, settings=Settings(),
                            mcp_servers=[], env={}, emit=_emit, prompt="write")
    claude_adapter = get_adapter(Engine.claude_code, claude_ctx.settings)
    assert claude_adapter.preflight_error(claude_ctx, {}) is None
    claude_adapter.prepare(claude_ctx)
    command = claude_adapter.build_command(claude_ctx)
    plugin_dirs = [Path(command[i + 1]) for i, arg in enumerate(command) if arg == "--plugin-dir"]
    assert SKILLS.parent in plugin_dirs
    assert read_plugin_manifest(SKILLS.parent)["name"] == "labhq-bundled-skills"
    assert skill_file(SKILLS.parent, "humanizer").samefile(SKILLS / "humanizer" / "SKILL.md")

    codex = AgentSpec(id="writer", name="writer", role="writer", engine=Engine.codex,
                      required_skills=["humanizer"])
    codex_workspace = TaskWorkspace(tmp_path / "codex", task, codex)
    assert install_required_skills(codex_workspace, codex) is None
    codex_ctx = RunContext(task=task, agent=codex, workdir=codex_workspace.dir, settings=Settings(),
                           mcp_servers=[], env={}, emit=_emit, prompt="write")
    codex_command = get_adapter(Engine.codex, codex_ctx.settings).build_command(codex_ctx)
    assert codex_command[codex_command.index("-C") + 1] == str(codex_workspace.dir)
    assert (codex_workspace.dir / ".agents" / "skills" / "humanizer" / "SKILL.md").is_file()


def test_read_only_profile_replaces_bundled_skills_with_prompt_rules(tmp_path):
    staff = AgentSpec(id="writer", name="writer", role="writer", engine=Engine.claude_code,
                      system_prompt="원래 역할", allow_skills=True,
                      required_skills=["humanizer", "graphical-abstract"])
    profile = read_only_profile(staff)
    assert profile.required_skills == [] and not profile.allow_skills
    for text in ("되어진다", "가지고 있다", "에 있어서", "비유의", "통계적으로 유의하지 않음",
                 "유의차", "통계적으로 유의한 차이", "미보정", "보정 전", "쉼표"):
        assert text in profile.system_prompt
    for text in ("질문", "자료", "방법", "핵심 결과", "결론", "<title>", "<desc>", "PNG", "캡처", "잘림", "겹침"):
        assert text in profile.system_prompt

    task = Task(id="t", request_id="r", agent_id="writer", prompt="review")
    ctx = RunContext(task=task, agent=profile, workdir=tmp_path, settings=Settings(), mcp_servers=[],
                     env={}, emit=_emit, prompt="review", read_only=True)
    adapter = get_adapter(Engine.claude_code, ctx.settings)
    adapter.prepare(ctx)
    command = adapter.build_command(ctx)
    assert "--plugin-dir" not in command and "--disable-slash-commands" in command
    assert command[command.index("--tools") + 1] == "Read,Glob,Grep,Write,Edit"


def test_korean_qc_warns_outside_quotes_and_passes_quoted_text():
    warned = check_document("# 보고서\n\n두 비교는 비유의였다.")
    assert warned["status"] == "WARN" and warned["must_zero"]["줄임 전문어"] == 1
    assert check_document('# 보고서\n\n원문은 "비유의"라고 적었다.')["status"] == "PASS"


def test_preservation_rejects_changed_numbers_and_only_locks_linked_headings(tmp_path):
    changed_number = check_preservation("# 결과\n\n값은 12.5%다.", "# 결과\n\n값은 13.5%다.")
    assert not changed_number["accepted"] and any(row["check"] == "numbers" for row in changed_number["fails"])

    target = tmp_path / "target.md"
    target.write_text("# 고정 제목\n\n본문입니다.\n", encoding="utf-8")
    (tmp_path / "index.md").write_text("[결과](target.md#고정-제목)\n", encoding="utf-8")
    linked = check_preservation(target.read_text(encoding="utf-8"), "# 바꾼 제목\n\n본문입니다.\n",
                                repo=tmp_path, document_path=target)
    assert not linked["accepted"] and any(row["check"] == "headings" for row in linked["fails"])
    (tmp_path / "index.md").write_text("링크 없음\n", encoding="utf-8")
    unlinked = check_preservation(target.read_text(encoding="utf-8"), "# 바꾼 제목\n\n본문입니다.\n",
                                  repo=tmp_path, document_path=target)
    assert unlinked["accepted"]


def test_graphical_abstract_example_is_accessible_text_only_and_uses_tokens():
    path = ROOT / "docs" / "media" / "graphical-abstract-example.svg"
    raw = path.read_text(encoding="utf-8")
    xml = ET.fromstring(raw)
    ns = "{http://www.w3.org/2000/svg}"
    assert xml.find(f"{ns}title") is not None and xml.find(f"{ns}desc") is not None
    assert xml.findall(f".//{ns}text")
    assert set(re.findall(r"#[0-9A-Fa-f]{6}", raw)) <= TOKEN_COLORS
    for element in xml.iter():
        own_text = element.text or ""
        if re.search(r"[가-힣]", own_text):
            assert element.tag in {f"{ns}title", f"{ns}desc", f"{ns}text"}
    assert "graphical-abstract-example.svg" in (ROOT / "docs" / "manual.md").read_text(encoding="utf-8")
