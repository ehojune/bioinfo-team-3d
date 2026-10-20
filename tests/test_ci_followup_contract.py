"""CI-only contracts: synthetic events, Markdown and workflow data; no processes."""
import re
from pathlib import Path

import pytest
import yaml

from scripts import ci_skip as cs


ROOT = Path(__file__).resolve().parents[1]
POLICY_DOCUMENTS = ["AGENTS.md", "HANDOFF.md", "docs/manual.md", "docs/reference/ci-reuse.md",
                    "docs/reference/lead-operations.md", "docs/safety.md"]


def workflow():
    return yaml.safe_load((ROOT / cs.WORKFLOW).read_text(encoding="utf-8"))


def concurrency(event, number=793, run=100, attempt=1, name="test"):
    """Interpret the deliberately small expression contract, never execute Actions."""
    config = workflow()["concurrency"]
    assert config["cancel-in-progress"] == "${{ github.event_name == 'pull_request' }}"
    values = {
        "github.workflow": name,
        "github.event_name == 'pull_request' && format('pr-{0}', github.event.pull_request.number) || format('run-{0}-{1}', github.run_id, github.run_attempt)":
            f"pr-{number}" if event == "pull_request" else f"run-{run}-{attempt}",
    }
    group = re.sub(r"\$\{\{ (.*?) \}\}", lambda match: values[match[1]], config["group"])
    return group, event == "pull_request"


def test_only_new_heads_of_the_same_pr_and_workflow_cancel_each_other():
    group, cancel = concurrency("pull_request")
    assert cancel
    assert concurrency("pull_request", run=101)[0] == group
    assert concurrency("pull_request", number=794)[0] != group
    assert concurrency("pull_request", name="other")[0] != group
    for event in ("push", "workflow_dispatch"):
        groups = [concurrency(event, run=run, attempt=attempt) for run, attempt in ((100, 1), (101, 1), (100, 2))]
        assert all(not cancel for _, cancel in groups)
        assert len({key for key, _ in groups}) == 3  # pending runs must not replace each other either
        assert all(key != group for key, _ in groups)


@pytest.mark.parametrize("path", POLICY_DOCUMENTS)
def test_policy_document_reflow_keeps_exact_content_structure_and_examples(path):
    before = "# Guidance\n\nApproval remains mandatory. All requests retain their original limits.\n\nUse `python stable.py`.\n\n```sh\necho stable\n```\n"
    after = before.replace("mandatory. All", "mandatory.\nAll")
    assert cs.document_change(path, before, after)


@pytest.mark.parametrize("path", POLICY_DOCUMENTS)
@pytest.mark.parametrize("before,after", [
    ("Approval remains mandatory.", "Approval becomes optional."),
    ("## Limits\n\nOriginal limits remain mandatory.", "## Guidance\n\nOriginal limits remain mandatory."),
    ("First paragraph.\n\nSecond paragraph.", "First paragraph. Second paragraph."),
    ("- First item.\n- Second item.", "First item. Second item."),
    ("Use `python stable.py`.", "Use `python changed.py`."),
    ("```sh\necho stable\n```", "```sh\necho changed\n```"),
    ("[Guide](original.md)", "[Guide](changed.md)"),
    ("<!-- stable -->", "<!-- changed -->"),
    ("Old prose.", "Old prose.\n\nNew requirement."),
    ("Text with `unclosed code.", "Text with `unclosed\ncode."),
    ("Run unknowncmd stable.", "Run unknowncmd\nstable."),
    ("First sentence.  \nSecond sentence.", "First sentence.\nSecond sentence."),
    ("- First sentence.\n- Second sentence.", "- First sentence.\n\n- Second sentence."),
])
def test_policy_content_changes_and_ambiguous_reflows_still_run_full(path, before, after):
    assert not cs.document_change(path, before, after)


@pytest.mark.parametrize("path", ["README.md", "docs/reference/overview.md", "docs/status/progress.md"])
@pytest.mark.parametrize("before,after", [
    ("Approval remains mandatory.", "Approval becomes optional."),
    ("The safety gate remains mandatory.", "The safety gate becomes optional."),
    ("Redaction remains mandatory.", "Redaction becomes optional."),
    ("The timeout remains bounded.", "The timeout becomes unlimited."),
    ("승인 절차를 유지합니다.", "승인 절차를 생략합니다."),
    ("모든 검증을 유지합니다.", "일부 검증을 생략합니다."),
])
def test_renaming_a_policy_document_cannot_hide_changed_safety_prose(path, before, after):
    assert not cs.document_change(path, before, after)


def test_first_pr_event_classifies_the_entire_synthetic_checkout_without_a_previous_run(monkeypatch):
    base, head, checkout = "a" * 40, "b" * 40, "c" * 40
    before = "# Guide\n\nOld prose.\n\nUse `python stable.py`.\n"
    after = before.replace("Old prose.", "New prose.")
    event = {"action": "opened", "pull_request": {"base": {"sha": base}, "head": {"sha": head}}}
    def git(*args):
        if args[0] == "rev-list":
            return f"{checkout} {base} {head}"
        if args[0] == "diff":
            return ":100644 100644 old new M\0README.md\0"
        return {f"{base}:README.md": before, f"{checkout}:README.md": after}[args[1]]
    monkeypatch.setattr(cs, "git", git)
    monkeypatch.setenv("GITHUB_SHA", checkout)
    monkeypatch.setattr(cs, "reuse_main", lambda *_: pytest.fail("PR classification must not need a saved run"))
    assert cs.decide("pull_request", event, {"checkout_sha": checkout}) == "documents"
    after += "\nRun unknowncmd task.\n"
    assert cs.decide("pull_request", event, {"checkout_sha": checkout}) == "full"


def test_light_gate_retains_document_and_safety_consistency_checks():
    jobs = workflow()["jobs"]
    assert "if" not in jobs["readme"]
    commands = "\n".join(step.get("run", "") for step in jobs["readme"]["steps"])
    for command in ("tests/test_integrations.py", "scripts/integrations.py --check",
                    "scripts/vocab_tables.py --check", "scripts/notes_index.py --validate",
                    "scripts/check_public.sh", "tests/test_text_encoding.py",
                    "tests/test_private_paths.py::test_absent_key_means_the_default_list_and_an_empty_list_turns_it_off",
                    "tests/test_topic_checklists_precedents.py::test_every_approved_topic_has_checks_with_a_cited_basis"):
        assert command in commands
    assert "scripts/patch_notes.py check" in "\n".join(s.get("run", "") for s in jobs["patch-notes"]["steps"])
    for name in cs.GATE_IDS:
        assert jobs[name]["if"] == "needs.changes.outputs.skip != 'true'"
    assert jobs["pytest"]["strategy"]["matrix"]["python-version"] == ["3.10", "3.12"]
    assert jobs["pytest-windows"]["runs-on"] == "windows-latest"
