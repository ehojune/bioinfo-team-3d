import hashlib
import json
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest


KIT = Path(__file__).resolve().parents[1] / "tools/prereg_kit"
SESSION = "11111111-2222-4333-8444-555555555555"
PROMPT = "Read the analysis code and tables before checking the report.\n"


@pytest.fixture
def cli(tmp_path):
    copied = tmp_path / "standalone-kit"
    shutil.copytree(KIT, copied, ignore=shutil.ignore_patterns("__pycache__"))

    def invoke(*args, expected=0, error=None, script="arm2.py"):
        result = subprocess.run([sys.executable, str(copied / script), *map(str, args)],
                                cwd=tmp_path, capture_output=True, text=True, encoding="utf-8", timeout=30)
        assert result.returncode == expected, result.stderr or result.stdout
        if error:
            assert error in result.stderr
        return json.loads(result.stdout) if result.stdout else None

    return invoke


def make_bundle(root, report, artifacts):
    for name, contents in {report: "# Synthetic report\n\nObserved value: 4.\n", **artifacts}.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8")
    (root / "empty").mkdir()


def hashes(root):
    return {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob("*") if path.is_file()}


def prepare(cli, tmp_path, report="report.md", artifacts=None):
    source = tmp_path / "arm 1"
    run = tmp_path / "arm 2"
    prompt_file = tmp_path / "measurement-b.txt"
    prompt_file.write_text(PROMPT, encoding="utf-8")
    make_bundle(source, report, artifacts or {})
    prepared = cli("prepare", source, run, "--report", report, "--session", SESSION, "--audit-prompt", prompt_file)
    assert prepared["state"] == "ready" and prepared["remaining_usd"] == "10"
    return source, run


def record(cli, run, attempt, cost, result, **expect):
    receipt = run.parent / f"result-{attempt}.json"
    receipt.write_text(json.dumps(result), encoding="utf-8")
    return cli("record", run, attempt, "--cost-usd", cost, "--result", receipt, **expect)


def finding(problem, severity="P1"):
    return {"severity": severity, "location": "tables/summary.tsv:2", "problem": problem,
            "evidence_quote": "Observed value: 4.", "revision": "Compare the table and report"}


@pytest.mark.parametrize(("report", "artifacts", "path"), [
    ("report/final.md", {"code/qc.R": "x <- 4\n", "tables/summary.tsv": "cell\tcount\nA\t4\n"}, "clean"),
    ("findings.md", {"src/check.py": "value = 4\n", "exports/controls.csv": "group,value\nA,4\n"}, "repair"),
    ("pc/report.md", {"hpc/steps.log": "synthetic stage complete\n", "pc/alpha.tsv": "id\tvalue\nA\t4\n"}, "budget"),
], ids=["single-cell-layout", "mixed-sample-layout", "hpc-pc-layout"])
def test_three_offline_bundle_layouts(cli, tmp_path, report, artifacts, path):
    source, run = prepare(cli, tmp_path, report, artifacts)
    baseline = hashes(source)
    assert hashes(run / "audit") == hashes(run / "repair") == baseline
    assert all((root / "empty").is_dir() for root in (source, run / "audit", run / "repair"))
    audit = cli("next", run)
    assert (audit["attempt"], audit["kind"], audit["max_usd"]) == (1, "audit", "4")
    assert audit["prompt"].startswith(PROMPT) and report in audit["prompt"]
    assert Path(audit["working_dir"]) == run / "audit"
    cli("next", run, expected=2, error="pending")
    p1 = [] if path == "clean" else [finding("Report and table disagree"), finding("Control absent")]
    result = record(cli, run, 1, "3.80", {"issues": [finding("Formatting", "P2"), *p1]})
    assert result["audit_attempts"] == 1
    if p1:
        first = cli("next", run)
        assert first["kind"] == "repair" and first["max_usd"] == "5"
        assert list(first["p1"]) == ["P1-1", "P1-2"]
        assert [issue["problem"] for issue in first["p1"].values()] == [issue["problem"] for issue in p1]
        assert first["resume_session"] == SESSION and first["fork_session"] != SESSION
        assert Path(first["working_dir"]) == run / "repair"
        (run / "repair" / report).write_text("# Revised report\n", encoding="utf-8")
        record(cli, run, 2, "5", {"unresolved": ["P1-2"]})
        second = cli("next", run)
        assert second["max_usd"] == "1.2" and second["fork_session"] is None
        assert second["resume_session"] == first["fork_session"] and second["p1"] == {"P1-2": first["p1"]["P1-2"]}
        (run / "repair" / report).write_text("# Second revision\n", encoding="utf-8")
        record(cli, run, 3, "0", {"unresolved": ["P2"]}, expected=2, error="incoming P1 list")
        result = record(cli, run, 3, "1.200000001" if path == "budget" else "1.20",
                        {"unresolved": ["P1-2"]}, expected=int(path == "budget"))
        assert result["repair_attempts"] == 2 and result["open_p1"] == ["P1-2"]
    expected = {"clean": ("complete", "3.8"), "repair": ("repair_limit", "10"), "budget": ("cost_overrun", "10.000000001")}
    assert (result["state"], result["spent_usd"]) == expected[path]
    assert cli("status", run) == result
    assert result["result_report"] == (None if path == "budget" else str(run / "repair" / report))
    cli("next", run, expected=2, error=result["state"])
    assert hashes(source) == hashes(run / "audit") == baseline
    if p1:
        assert hashes(run / "repair")[report] != baseline[report]
    if result["result_report"]:
        Path(result["result_report"]).write_text("# Changed after recording\n", encoding="utf-8")
        cli("status", run, expected=2, error="final repair copy changed")


def test_concurrent_reservations_and_failed_audit_are_single_attempt(cli, tmp_path):
    _, run = prepare(cli, tmp_path)

    def try_reserve():
        try:
            return cli("next", run)["attempt"]
        except AssertionError as error:
            assert "pending" in str(error)
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: try_reserve(), range(2)))
    assert results.count(1) == results.count(None) == 1
    cli("record", run, 2, "--cost-usd", "0", "--failed", expected=2, error="pending call")
    result = cli("record", run, 1, "--cost-usd", "0.4", "--failed", expected=1)
    assert result["state"] == "audit_failed" and result["spent_usd"] == "0.4"
    cli("next", run, expected=2, error="audit_failed")


@pytest.mark.parametrize("fork_created", [True, False])
def test_failed_first_repair_counts_and_next_uses_same_lineage(cli, tmp_path, fork_created):
    _, run = prepare(cli, tmp_path)
    cli("next", run)
    record(cli, run, 1, "4", {"issues": [finding("Mismatch in table")]})
    first = cli("next", run)
    flags = ["--fork-created"] if fork_created else []
    failure = cli("record", run, 2, "--cost-usd", "5", "--failed", *flags)
    assert failure["state"] == "ready" and failure["open_p1"] == ["P1-1"]
    second = cli("next", run)
    assert second["max_usd"] == "1"
    assert second["resume_session"] == (first["fork_session"] if fork_created else SESSION)
    assert second["fork_session"] == (None if fork_created else first["fork_session"])
    final = record(cli, run, 3, "1", {"unresolved": []})
    assert final["state"] == "complete" and final["remaining_usd"] == "0"
    assert cli("status", run) == final
    Path(final["result_report"]).write_text("# Changed after recording\n", encoding="utf-8")
    cli("status", run, expected=2, error="final repair copy changed")


@pytest.mark.parametrize("changed", ["source", "audit", "repair"])
def test_changed_baseline_is_rejected_and_paid_audit_is_recorded(cli, tmp_path, changed):
    source, run = prepare(cli, tmp_path)
    cli("next", run)
    root = source if changed == "source" else run / changed
    (root / "late-file.txt").write_text("unexpected", encoding="utf-8")
    result = record(cli, run, 1, "2", {"issues": []}, expected=1)
    assert result["state"] == "integrity_failed" and result["spent_usd"] == "2"
    assert cli("status", run)["spent_usd"] == "2"
    cli("next", run, expected=2, error="integrity_failed")


def test_stage_ceiling_is_enforced_even_below_bundle_ceiling(cli, tmp_path):
    _, run = prepare(cli, tmp_path)
    cli("next", run)
    result = record(cli, run, 1, "4.01", {"issues": [finding("Mismatch")]}, expected=1)
    assert result["state"] == "cost_overrun" and result["spent_usd"] == "4.01"
    cli("next", run, expected=2, error="cost_overrun")


def test_prepare_refuses_destination_within_original(cli, tmp_path):
    source = tmp_path / "original"
    make_bundle(source, "report.md", {})
    output = source / "run"
    cli("prepare", source, output, "--report", "report.md", "--session", SESSION,
        "--audit-prompt", source / "report.md", expected=2, error="outside the original")
    assert not output.exists()


def test_blind_preserves_commands_and_redacts_arm_markers(cli, tmp_path):
    source = tmp_path / "synthetic-arm2.md"
    report = (
        "# Commands\n\n"
        "From `<work>` run ``printf '`date`'`` or `cd arm_2`.\n\n"
        "```sh compare=arm-2\n"
        "python -I scripts/check.py --root '<work>' \\\n"
        "    --arm 2  --label 'synthetic-cohort-z'\n"
        "\n\n"
        "\tpython -I scripts/summary.py --arch arm64\n"
        "```\n\n"
        "> ~~~~sh\n"
        ">   echo '비교군 ②'\n"
        ">   # ~~~\n"
        "> ~~~~~\n\n"
        "    python -I scripts/table.py\n"
        "\tpython -I scripts/count.py --lane=B\n"
    )
    source.write_text(report, encoding="utf-8")
    out, mapping = tmp_path / "blinded", tmp_path / "private.json"
    result = cli("blind", out, mapping, source, "--redact", "synthetic-cohort-z", script="kit.py")
    [export] = out.iterdir()
    assert export.read_text(encoding="utf-8") == (
        "Commands\n\n"
        "From `<work>` run ``printf '`date`'`` or `cd [redacted]`.\n\n"
        "```sh compare=[redacted]\n"
        "python -I scripts/check.py --root '<work>' \\\n"
        "    --arm [redacted]  --label '[redacted]'\n"
        "\n\n"
        "\tpython -I scripts/summary.py --arch arm64\n"
        "```\n\n"
        "> ~~~~sh\n"
        ">   echo '[redacted]'\n"
        ">   # ~~~\n"
        "> ~~~~~\n\n"
        "    python -I scripts/table.py\n"
        "\tpython -I scripts/count.py --lane=[redacted]\n"
    )
    assert source.read_text(encoding="utf-8") == report
    assert export.suffix == ".txt" and len(export.stem) == 16
    assert json.loads(mapping.read_bytes()) == {export.stem: str(source.resolve())}
    assert result == {"reports": 1, "mapping_sha256": hashlib.sha256(mapping.read_bytes()).hexdigest()}


def test_blind_keeps_hidden_markup_and_links_out_of_export(cli, tmp_path):
    source = tmp_path / "synthetic-report.md"
    source.write_text(
        "---\nworkflow: private-frontmatter\n---\n"
        "<!--\n```sh\necho private-comment\n```\n-->\n"
        "<pre>\n```sh\necho private-pre\n```\n</pre>\n"
        "<script>private-script</script>\n"
        "<style>private-style</style>\n"
        "<span title='`private-attribute`'>Visible</span>\n"
        "See [the `<work>` placeholder](private-link) and ![`private-image`](private-image.png).\n"
        "[[claim: private-claim]]\n"
        "[ref]: private-reference\n"
        "Visit <https://private-angle.invalid/> or https://private-bare.invalid/.\n\n"
        "`curl 'https://private-inline.invalid/a;private-inline-tail=synthetic'`\n\n"
        "```sh\n"
        "  curl \"https://private-code.invalid/a;private-code-tail=synthetic\"\n"
        "  curl https://private-bare-code.invalid/a;echo done\n"
        "  curl https://private-pipe.invalid/a|head\n"
        "  curl https://private-shell.invalid/a&&echo done\n"
        "  curl https://private-query.invalid/a?public=1&private-query-tail=synthetic &\n"
        "  curl https://private-escaped.invalid/a\\;private-escaped-tail=synthetic\n"
        "```\n",
        encoding="utf-8",
    )
    out, mapping = tmp_path / "blinded", tmp_path / "mapping.json"
    cli("blind", out, out / "map.json", source, script="kit.py", expected=2, error="outside output")
    assert not out.exists()
    cli("blind", out, mapping, source, script="kit.py")
    [export] = out.iterdir()
    result = export.read_text(encoding="utf-8")
    assert "private-" not in result
    assert "Visible\nSee the `<work>` placeholder and ." in result
    assert "`curl '[redacted]'`" in result
    assert (
        '```sh\n  curl "[redacted]"\n'
        "  curl [redacted];echo done\n"
        "  curl [redacted]|head\n"
        "  curl [redacted]&&echo done\n"
        "  curl [redacted] &\n"
        "  curl [redacted]\n```"
    ) in result


def test_blind_preserves_fence_nested_in_list(cli, tmp_path):
    source = tmp_path / "synthetic-list.md"
    fence = (
        "    ```sh\n"
        "    printf \"<work>\" \\\n"
        "      --arm 2\n"
        "    ```\n"
    )
    source.write_text(
        f"- Run:\n{fence}\n- Finish.\n\n<!-- private-comment -->\n", encoding="utf-8",
    )
    out, mapping = tmp_path / "blinded", tmp_path / "mapping.json"
    cli("blind", out, mapping, source, script="kit.py")
    [export] = out.iterdir()
    assert export.read_text(encoding="utf-8") == f"Run:\n{fence.replace('--arm 2', '--arm [redacted]')}\nFinish.\n"


def test_blind_preserves_indented_code_in_quote(cli, tmp_path):
    source = tmp_path / "synthetic-quote.md"
    source.write_text(
        'Intro\n\n>     printf "<work>"\n>\n>     --arm 2\n>\n> Summary.\n', encoding="utf-8",
    )
    out, mapping = tmp_path / "blinded", tmp_path / "mapping.json"
    cli("blind", out, mapping, source, script="kit.py")
    [export] = out.iterdir()
    assert export.read_text(encoding="utf-8") == (
        'Intro\n\n>     printf "<work>"\n>\n>     --arm [redacted]\n\nSummary.\n'
    )
