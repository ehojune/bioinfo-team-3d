"""PR policy regressions: fake git and static workflow conditions, no processes/API."""
import re
from pathlib import Path

import pytest
import yaml

from scripts import ci_skip as cs


ROOT = Path(__file__).resolve().parents[1]


def test_reported_readme_title_case_is_already_fixed_with_fake_git(monkeypatch):
    before, after = "# labhq\n", "# labhq\nMore prose.\n"
    def git(*args):
        if args[0] == "diff":
            return ":100644 100644 old new M\0README.md\0"
        assert args[0] == "show"
        return {"base:README.md": before, "head:README.md": after}[args[1]]
    monkeypatch.setattr(cs, "git", git)
    assert cs.document_change("README.md", before, after)
    assert cs.can_skip("head", "base")


@pytest.mark.parametrize("prefix", ["Run ", "Please run ", "다음을 실행하세요 ", "For example, use "])
@pytest.mark.parametrize("command", ["python demo.py", "git status", "custom-command argument"])
def test_explanatory_prefix_cannot_hide_an_unfenced_command(prefix, command):
    before, after = prefix + command + " old.", prefix + command + " new."
    assert not cs.document_change("README.md", before, after)
    assert not cs.document_change("README.md", "Old prose.", after)
    assert not cs.document_change("README.md", before, "New prose.")


def test_unchanged_unfenced_command_does_not_hide_unrelated_prose_edit():
    before = "# labhq\n\nRun python demo.py.\n\nOld prose.\n\nUse `python stable.py`.\n"
    assert cs.document_change("README.md", before, before.replace("Old prose.", "New prose."))


def condition_matches(condition, context):
    """Evaluate only the conjunction/equality syntax used by this job, never run a workflow."""
    def value(expression):
        if expression.startswith("'"):
            return expression[1:-1]
        result = context
        for part in expression.split("."):
            result = result.get(part) if isinstance(result, dict) else None
        return result
    result = True
    for term in condition.split("&&"):
        match = re.fullmatch(r"\s*([\w.]+)\s*(==|!=)\s*('[^']*'|[\w.]+)\s*", term)
        assert match, "unsupported producer condition syntax"
        left, operator, right = match.groups()
        equal = value(left) == value(right)
        result = result and (equal if operator == "==" else not equal)
    return result


@pytest.mark.parametrize("case,expected", [
    ("same-repository", True), ("fork", False), ("wrong-id", False),
    ("missing-repository", False), ("missing-id", False), ("documents", False), ("push", False),
])
def test_only_full_same_repository_pr_can_produce_reuse_manifest(case, expected):
    workflow = yaml.safe_load((ROOT / cs.WORKFLOW).read_text(encoding="utf-8"))
    github = {"event_name": "pull_request", "repository": cs.REPOSITORY,
              "event": {"pull_request": {"base": {"repo": {"id": 123}},
                                         "head": {"repo": {"id": 123, "full_name": cs.REPOSITORY}}}}}
    context = {"github": github, "needs": {"changes": {"outputs": {"skip": "false"}}}}
    head = github["event"]["pull_request"]["head"]
    if case == "fork":
        head["repo"] = {"id": 999, "full_name": "someone/fork"}
    elif case == "wrong-id":
        head["repo"]["id"] = 999
    elif case == "missing-repository":
        head["repo"] = None
    elif case == "missing-id":
        del head["repo"]["id"]
    elif case == "documents":
        context["needs"]["changes"]["outputs"]["skip"] = "true"
    elif case == "push":
        github["event_name"] = "push"
    assert condition_matches(workflow["jobs"]["validation-manifest"]["if"], context) is expected
    # Fork exclusion is confined to the receipt producer, never the required tests.
    for name in cs.GATE_IDS:
        assert workflow["jobs"][name]["if"] == "needs.changes.outputs.skip != 'true'"
    assert "if" not in workflow["jobs"]["readme"]
