import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from labhq import reproduction_receipt as rr


def python_tools():
    return {"python": sys.executable, "r": None, "bash": None}


def test_compile_is_not_execution_and_does_not_inherit_operator_input(tmp_path, monkeypatch):
    monkeypatch.setenv("LABHQ_INPUT_DATA", "operator-value-never-pass-to-checker")
    source = 'import os\nraise RuntimeError(os.environ["LABHQ_INPUT_DATA"])\n'
    receipt = rr.build_receipt({"code/a/check.py": source}, tools=python_tools(), temp_root=tmp_path)
    row = receipt["files"][0]
    assert row["syntax"]["status"] == "pass"
    assert row["execution"]["status"] == "blocked_input"
    assert row["missing_inputs"] == ["LABHQ_INPUT_DATA"]
    assert row["cwd"] == "code/a" and row["command"][-1] == "check.py"
    assert row["sha256"] == hashlib.sha256(source.encode()).hexdigest()
    assert receipt["lock_status"] == "missing"
    assert "operator-value" not in rr.receipt_json(receipt)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("source,status", [("raise RuntimeError('not run')\n", "pass"), ("if :\n", "fail")])
def test_python_syntax_result_is_separate_from_default_not_rerun(tmp_path, source, status):
    receipt = rr.build_receipt({"run.py": source}, tools=python_tools(), temp_root=tmp_path)
    assert receipt["files"][0]["syntax"]["status"] == status
    assert receipt["files"][0]["execution"]["status"] == "not_rerun"


@pytest.mark.parametrize("name", ["code/a/run.py", "code/a/run.R", "code/a/run.sh"])
def test_missing_interpreter_is_unavailable_and_not_a_syntax_pass(tmp_path, name):
    receipt = rr.build_receipt({name: "example"}, tools={}, temp_root=tmp_path)
    assert rr.receipt_summary(receipt)["syntax"] == "unavailable"
    assert receipt["files"][0]["tool"]["version"] is None
    assert receipt["files"][0]["execution"]["status"] == "not_rerun"


@pytest.mark.parametrize("language,name,expected", [
    ("r", "run.R", ["--vanilla", "-e", rr._R_PARSE, "run.R"]),
    ("bash", "run.sh", ["--noprofile", "--norc", "-n", "run.sh"]),
])
def test_r_and_shell_parser_commands_are_isolated_and_diagnostics_never_escape(tmp_path, monkeypatch, language, name, expected):
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0 if command[-1] == "--version" else 1,
            b"tool 4.5.1 C:/Users/operator/SECRET", b"invalid source with secret-value")

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setenv("BASH_ENV", "operator-startup")
    receipt = rr.build_receipt({name: "invalid syntax"}, tools={language: "parser"}, temp_root=tmp_path)
    assert calls[-1][0] == ["parser", *expected]
    assert calls[-1][1]["cwd"].is_relative_to(tmp_path)
    for _, kwargs in calls:
        assert "BASH_ENV" not in kwargs["env"] and "GITHUB_TOKEN" not in kwargs["env"]
    row = receipt["files"][0]
    assert row["syntax"]["status"] == "fail" and row["tool"]["version"] == "4.5.1"
    assert "secret-value" not in rr.receipt_json(receipt) and "C:/Users" not in rr.receipt_json(receipt)


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash unavailable; the unavailable state is tested separately")
@pytest.mark.parametrize("source,status", [("echo 'syntax only'\n", "pass"), ("if then\n", "fail")])
def test_real_bash_syntax_without_running_the_script(tmp_path, source, status):
    receipt = rr.build_receipt({"run.sh": source}, tools={"bash": shutil.which("bash")}, temp_root=tmp_path)
    assert receipt["files"][0]["syntax"]["status"] == status
    assert receipt["files"][0]["execution"]["status"] == "not_rerun"


@pytest.mark.skipif(shutil.which("Rscript") is None, reason="Rscript unavailable; command and missing-tool paths are tested")
@pytest.mark.parametrize("source,status", [("stop('not evaluated')\n", "pass"), ("if (\n", "fail")])
def test_real_r_parse_without_running_the_script(tmp_path, source, status):
    receipt = rr.build_receipt({"run.R": source}, tools={"r": shutil.which("Rscript")}, temp_root=tmp_path)
    assert receipt["files"][0]["syntax"]["status"] == status
    assert receipt["files"][0]["execution"]["status"] == "not_rerun"


def test_code_lock_hashes_missing_lock_and_private_metadata(tmp_path):
    source = 'x = "C:/Users/operator/private/input.tsv"\n'
    receipt = rr.build_receipt({"code/a/run.py": source, "run/renv.lock": "{}\n"},
        required_locks=["run/missing/uv.lock"], tools=python_tools(), temp_root=tmp_path,
        outputs={"a": ["outputs/result.tsv", "C:/Users/operator/private.tsv", "../outside.tsv"]})
    assert receipt["locks"][0]["sha256"] == hashlib.sha256(b"{}\n").hexdigest()
    assert receipt["locks"][1] == {"path": "run/missing/uv.lock", "sha256": None, "status": "missing"}
    assert receipt["lock_status"] == "missing"
    assert receipt["files"][0]["expected_outputs"] == ["outputs/result.tsv"]
    assert "operator" not in rr.receipt_json(receipt)


def test_private_source_path_is_redacted_and_never_staged(tmp_path):
    receipt = rr.build_receipt({"C:/Users/operator/run.py": "x=1"}, tools=python_tools(), temp_root=tmp_path)
    assert receipt["files"][0]["syntax"]["status"] == "blocked_path"
    assert "operator" not in rr.receipt_json(receipt)


@pytest.mark.parametrize("source,result", [
    ('from pathlib import Path\nimport os\nPath("result.txt").write_text(os.environ["LABHQ_INPUT_DATA"])\n', "pass"),
    ('raise RuntimeError("fixture-private-value")\n', "fail"),
    ('x = 1\n', "missing_output"),
    ('if :\n', "blocked_syntax"),
])
def test_explicit_small_fixture_execution_is_separate_and_checks_expected_outputs(tmp_path, source, result):
    receipt = rr.build_receipt({"code/a/run.py": source, "uv.lock": "version = 1\n"},
        tools=python_tools(), temp_root=tmp_path, rerun=True, inputs={"LABHQ_INPUT_DATA": "fixture-private-value"},
        outputs={"a": ["result.txt"]})
    row = receipt["files"][0]
    assert row["execution"]["status"] == result
    assert "fixture-private-value" not in rr.receipt_json(receipt)
    assert row["syntax"]["status"] == ("fail" if result == "blocked_syntax" else "pass")


def test_parser_timeout_remains_visible(tmp_path, monkeypatch):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("private-command", 1, stderr=b"private diagnostic")
    monkeypatch.setattr(subprocess, "run", timeout)
    receipt = rr.build_receipt({"run.py": "x=1"}, tools=python_tools(), temp_root=tmp_path)
    assert rr.receipt_summary(receipt)["syntax"] == "timeout"
    assert "private" not in rr.receipt_json(receipt)


def test_variable_names_and_declared_outputs_are_value_free():
    assert rr.input_variables('import os\nx=os.getenv("DATA")\ny=os.environ.get("OPTION")', "python") == ["DATA", "OPTION"]
    assert rr.input_variables('x <- Sys.getenv("LABHQ_INPUT_DATA")', "r") == ["LABHQ_INPUT_DATA"]
    assert rr.input_variables('local=1\necho "$local ${LABHQ_INPUT_DATA:?set input}"', "bash") == ["LABHQ_INPUT_DATA"]
    assert rr.expected_outputs({"plan": {"steps": [{"id": "a", "outputs": ["run.py", "uv.lock", "result.tsv"]}]}}) == {"a": ["result.tsv"]}


def test_missing_or_malformed_receipts_never_become_execution_pass():
    for value in (None, {}, {"schema_version": 1, "files": [{"syntax": "pass"}]}):
        summary = rr.receipt_summary(value)
        assert summary["execution"] == "not_rerun" and not summary["present"]


def test_non_text_source_is_not_silently_reported_as_checked(tmp_path):
    receipt = rr.build_receipt({"run.py": b"\xff"}, tools={}, temp_root=tmp_path)
    summary = rr.receipt_summary(receipt)
    assert summary["syntax"] == "not_checked" and summary["execution"] == "not_rerun"
    assert summary["omitted_files"] == ["run.py"]


def test_bundle_collection_opens_only_reported_code_and_locks(tmp_path, monkeypatch):
    from labhq.result_storage import collect_reproduction_receipt
    from labhq.settings import Settings
    settings = Settings()
    settings.runner.workspace_root = str(tmp_path / "workspace")
    root = tmp_path / "workspace/requests/r"
    files = {"steps/a/outputs/scripts/run.py": "raise RuntimeError('not run')\n",
             "steps/a/outputs/env/renv.lock": "{}\n",
             "steps/a/outputs/table.tsv": "must never be opened\n"}
    root.mkdir(parents=True)
    for name, body in files.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    (root / "MANIFEST.tsv").write_text("relative_path\tstatus\n" + "".join(f"{name}\tcopied\n" for name in files), encoding="utf-8")
    read = Path.read_text
    def guarded(path, *args, **kwargs):
        assert path.suffix != ".tsv", "data must not be opened"
        return read(path, *args, **kwargs)
    monkeypatch.setattr(Path, "read_text", guarded)
    receipt = collect_reproduction_receipt({"id": "r"}, settings)
    assert [row["path"] for row in receipt["files"]] == ["steps/a/outputs/scripts/run.py"]
    assert receipt["locks"][0]["path"] == "steps/a/outputs/env/renv.lock"
    assert receipt["files"][0]["execution"]["status"] == "not_rerun"


def test_report_front_exposes_failed_checks_and_execution_status(tmp_path):
    from labhq.report_contract import build_report_package, render_report_front
    receipt = rr.build_receipt({"bad.py": "if :", "run.R": "x<-1"}, tools=python_tools(), temp_root=tmp_path)
    package = build_report_package({"text": "fixture", "report": "# Result", "reproduction_receipt": receipt})
    text = render_report_front(package)
    assert "문법 검사: fail" in text and "실행: not_rerun" in text
    assert "문법 실패 1" in text and "도구 없음 1" in text


def test_portfolio_receipt_hashes_final_cleaned_sources_and_preserves_failed_python_receipt(tmp_path):
    from labhq.portfolio import EditionCheckError, build_portfolio_files
    from labhq.result_storage import draft_documents
    from tests.test_portfolio_edition import portfolio_fixture
    settings, request, bundle, assets, images, decisions, tasks = portfolio_fixture(tmp_path)
    assets["steps/analysis/outputs/scripts/analyze.py"] = 'from pathlib import Path\nPath("C:/Users/operator/private.csv").read_text()\n'
    assets["steps/analysis/outputs/scripts/check.R"] = 'x <- read.csv("C:/Users/operator/private.csv")\n'
    assets["steps/analysis/outputs/scripts/check.sh"] = 'cat "/home/operator/private.csv"\n'
    assets["steps/env/outputs/env/renv.lock"] = '{"R":{"Version":"4.4.0"}}\n'
    docs = draft_documents(request, settings, assets=assets)
    assert "REPRODUCTION_RECEIPT.json" in docs
    files = build_portfolio_files(request, settings, assets, images, bundle=bundle,
                                 run_documents={"run/reproduce.md": docs["REPRODUCE.md"],
                                                "run/task_record.md": docs["TASK_RECORD.md"]})
    receipt = json.loads(files[rr.RECEIPT_PATH])
    assert len(receipt["files"]) == 3
    for row in receipt["files"]:
        assert row["sha256"] == hashlib.sha256(files[row["path"]].encode()).hexdigest()
        assert row["input_variables"]
        assert row["execution"]["status"] == "blocked_input"
    assert files["run/reproduce.md"].count("## 재현 검증") == 1
    assert "../run/reproduction_receipt.json" in files["report/report.md"]
    assert "문법 검사:" in files["README.md"]
    assert "실행: blocked_input" in files["README.md"]
    assert "operator" not in files[rr.RECEIPT_PATH]
    assert receipt["locks"][0]["path"] == "run/environment/renv.lock"
    assets["steps/analysis/outputs/scripts/analyze.py"] = "if :\n"
    with pytest.raises(EditionCheckError) as error:
        build_portfolio_files(request, settings, assets, images, bundle=bundle)
    assert rr.receipt_summary(error.value.reproduction_receipt)["syntax"] == "fail"


async def test_bench_manifest_and_comparison_consume_receipt_without_equating_content_with_rerun(tmp_path, monkeypatch):
    from labhq import bench
    from labhq.settings import Settings
    receipt = rr.build_receipt({"bad.py": "if :"}, tools=python_tools(), temp_root=tmp_path)

    async def arm(case, arm_dir, *args):
        (arm_dir / "answer.md").write_text(case["mock_answer"], encoding="utf-8")
        return {"engine": "labhq", "status": "done", "duration_s": 0, "cost_usd": 0,
                "cost_known": True, "reproduction_receipt": receipt}

    monkeypatch.setattr(bench, "_run_labhq", arm)
    result = await bench.run_case("public-protein-qc", tmp_path, engines="mock", settings=Settings(), arms=["labhq"])
    row = result["rows"][0]
    assert row["checks_passed"] is True
    assert row["reproduction_verification"]["syntax"] == "fail"
    assert row["reproduction_verification"]["execution"] == "not_rerun"
    arm_dir = tmp_path / "public-protein-qc" / result["run_id"] / "labhq"
    run = json.loads((arm_dir / "run.json").read_text(encoding="utf-8"))
    assert run["reproduction_receipt"] == receipt
    assert run["reproduction_verification"]["execution"] == "not_rerun"
    assert json.loads((arm_dir / "reproduction_receipt.json").read_text(encoding="utf-8")) == receipt
    assert "문법 검사: fail" in (arm_dir.parent / "comparison.md").read_text(encoding="utf-8")
    manifest_before = (arm_dir / "run.json").read_bytes()
    score = json.loads((arm_dir / "score.json").read_text(encoding="utf-8"))
    score["reproduction_verification"] = {"syntax": "pass", "execution": "pass"}
    (arm_dir / "score.json").write_text(json.dumps(score), encoding="utf-8")
    rescored = await bench.rescore_case("public-protein-qc", tmp_path, run_id=result["run_id"])
    assert rescored[0]["rows"][0]["reproduction_verification"]["syntax"] == "fail"
    assert rescored[0]["rows"][0]["reproduction_verification"]["execution"] == "not_rerun"
    assert (arm_dir / "run.json").read_bytes() == manifest_before


async def test_failed_storage_keeps_the_receipt_without_publishing(tmp_path, monkeypatch):
    from labhq.result_storage import ResultStorage
    from tests.test_result_storage import Hub, fake_github
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token-for-tests")
    calls = []
    hub = Hub(tmp_path)
    service = ResultStorage(hub, fake_github(calls))
    await service.select("req_test", {"mode": "new", "repo": "fixture", "visibility": "private"})
    hub.requests["req_test"].update(status="done", pi_reviewed_at=1, report="# Fixture\n\nfixture only")
    monkeypatch.setattr("labhq.result_storage.edition_assets", lambda *args: (
        {"steps/a/outputs/scripts/bad.py": "if :\n"}, {}))
    try:
        await service.finish("req_test")
        saved = hub.requests["req_test"]["result_storage"]
        assert saved["status"] == "failed"
        assert saved["reproduction_receipt"]["syntax"] == "fail"
        assert hub.requests["req_test"]["reproduction_receipt"]["files"][0]["syntax"]["status"] == "fail"
        assert not any(method in {"POST", "PUT", "PATCH"} for method, _, _ in calls)
    finally:
        await service.close()


# ----- #771: scripts sharing one step folder are judged by what each run wrote -----

class FakeInterpreter:
    """A fake `subprocess.run`: `--version` and the compile check pass, and running a script does what `actions` says
    for its name in the step folder (`cwd`). No interpreter or process starts."""

    def __init__(self, actions):
        self.actions, self.runs = actions, []

    def __call__(self, command, *, cwd, **kwargs):
        if command[-1] == "--version":
            return subprocess.CompletedProcess(command, 0, b"Python 3.12.0", b"")
        if "-c" in command:
            return subprocess.CompletedProcess(command, 0, b"", b"")
        script = command[-1]
        self.runs.append((script, Path(cwd)))
        return subprocess.CompletedProcess(command, self.actions.get(script, lambda folder: 0)(Path(cwd)), b"", b"")


def write_result(folder):
    (folder / "result.txt").write_text("table", encoding="utf-8")
    return 0


def statuses(receipt):
    return {row["path"].rsplit("/", 1)[-1]: row["execution"]["status"] for row in receipt["files"]}


@pytest.mark.parametrize("helper", ["aa_helper.py", "zz_helper.py"])
def test_a_script_that_writes_no_declared_output_never_passes_on_files_an_earlier_script_left(tmp_path, monkeypatch, helper):
    fake = FakeInterpreter({"make.py": write_result})
    monkeypatch.setattr(subprocess, "run", fake)
    receipt = rr.build_receipt({"code/a/make.py": "make()\n", f"code/a/{helper}": "def util(): pass\n"},
                               tools=python_tools(), temp_root=tmp_path, rerun=True, outputs={"a": ["result.txt"]})
    assert [script for script, _ in fake.runs] == sorted(["make.py", helper])
    # The same verdicts whether the helper sorts before or after the script that writes the output.
    assert statuses(receipt) == {"make.py": "pass", helper: "missing_output"}
    assert rr.receipt_summary(receipt)["execution"] == "missing_output"
    assert {row["execution"]["output_check"] for row in receipt["files"]} == {"declared_files_written_by_this_run"}


def test_two_scripts_that_each_write_the_same_bytes_both_pass(tmp_path, monkeypatch):
    monkeypatch.setattr(subprocess, "run", FakeInterpreter({"one.py": write_result, "two.py": write_result}))
    receipt = rr.build_receipt({"code/a/one.py": "w()\n", "code/a/two.py": "w()\n"}, tools=python_tools(),
                               temp_root=tmp_path, rerun=True, outputs={"a": ["result.txt"]})
    assert statuses(receipt) == {"one.py": "pass", "two.py": "pass"}
    assert rr.receipt_summary(receipt)["execution"] == "pass"


def test_a_later_script_still_reads_intermediates_and_sibling_sources_in_the_shared_step_folder(tmp_path, monkeypatch):
    def prepare(folder):
        (folder / "work" / "raw.tsv").parent.mkdir()
        (folder / "work" / "raw.tsv").write_text("x\t1\n", encoding="utf-8")  # not a declared output
        return 0

    def analyze(folder):
        if not (folder / "work" / "raw.tsv").is_file() or not (folder / "lib" / "util.py").is_file():
            return 1
        return write_result(folder)

    fake = FakeInterpreter({"01_prepare.py": prepare, "02_analyze.py": analyze})
    monkeypatch.setattr(subprocess, "run", fake)
    receipt = rr.build_receipt({"code/a/01_prepare.py": "p()\n", "code/a/02_analyze.py": "import lib.util\n",
                                "code/a/lib/util.py": "def u(): pass\n", "code/b/other.py": "x = 1\n"},
                               tools=python_tools(), temp_root=tmp_path, rerun=True,
                               outputs={"a": ["result.txt"], "b": ["other.txt"]})
    assert statuses(receipt) == {"01_prepare.py": "missing_output", "02_analyze.py": "pass",
                                 "util.py": "missing_output", "other.py": "missing_output"}
    folders = {script: folder for script, folder in fake.runs}
    assert folders["01_prepare.py"] == folders["02_analyze.py"] and folders["01_prepare.py"].name == "a"
    assert all(folder.is_relative_to(tmp_path) for folder in folders.values())


def test_a_link_an_earlier_script_left_is_not_touched_or_counted(tmp_path, monkeypatch):
    outside = tmp_path / "outside.txt"
    outside.write_text("not in the receipt folder", encoding="utf-8")
    os.utime(outside, ns=(10**18, 10**18))

    def link(folder):
        os.symlink(outside, folder / "result.txt")
        return 0

    probe = tmp_path / "probe"
    try:
        os.symlink(outside, probe)
    except (OSError, NotImplementedError):
        pytest.skip("this account cannot make symbolic links")
    probe.unlink()
    monkeypatch.setattr(subprocess, "run", FakeInterpreter({"a_link.py": link}))
    receipt = rr.build_receipt({"code/a/a_link.py": "l()\n", "code/a/b_idle.py": "x = 1\n"}, tools=python_tools(),
                               temp_root=tmp_path, rerun=True, outputs={"a": ["result.txt"]})
    assert statuses(receipt) == {"a_link.py": "missing_output", "b_idle.py": "missing_output"}
    assert outside.stat().st_mtime_ns == 10**18


def directory_link(target, link):
    """A symbolic link to a folder, or a junction on Windows where this account cannot make symbolic links."""
    try:
        os.symlink(target, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        if os.name != "nt":
            pytest.skip("this account cannot make directory links")
        import _winapi
        _winapi.CreateJunction(str(target), str(link))


@pytest.mark.parametrize("alias", ["hard_link", "linked_parent"])
def test_an_output_that_aliases_a_file_outside_the_step_folder_is_neither_touched_nor_counted(tmp_path, monkeypatch, alias):
    """PR #781 review: resetting the time of a hard link or of a file under a linked folder changed the original outside."""
    outside = tmp_path / "outside"
    outside.mkdir()
    original = outside / "result.txt"
    original.write_text("explicit input, not a receipt output", encoding="utf-8")
    os.utime(original, ns=(10**18, 10**18))
    declared = "result.txt" if alias == "hard_link" else "out/result.txt"

    def make_alias(folder):
        if alias == "hard_link":
            os.link(original, folder / "result.txt")
        else:
            directory_link(outside, folder / "out")
        return 0

    monkeypatch.setattr(subprocess, "run", FakeInterpreter({"a_alias.py": make_alias}))
    receipt = rr.build_receipt({"code/a/a_alias.py": "a()\n", "code/a/b_idle.py": "x = 1\n"}, tools=python_tools(),
                               temp_root=tmp_path, rerun=True, outputs={"a": [declared]})
    assert original.stat().st_mtime_ns == 10**18
    assert statuses(receipt) == {"a_alias.py": "missing_output", "b_idle.py": "missing_output"}
