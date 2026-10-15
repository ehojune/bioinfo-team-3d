import re
import subprocess
from collections import defaultdict
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
CASE_DIR = REPO_ROOT / "labhq" / "bench_data" / "cases"
IDENTIFIER_TYPES = {"string", "string_list"}


def _answer_identifiers(case_dir=CASE_DIR):
    answers = defaultdict(set)
    for case_path in sorted(case_dir.glob("*.yaml")):
        case = yaml.safe_load(case_path.read_text(encoding="utf-8"))
        case_name = case.get("id", case_path.stem)
        for field_name, spec in case.get("result_fields", {}).items():
            if spec.get("type") not in IDENTIFIER_TYPES or "equals" not in spec:
                continue
            values = spec["equals"]
            if isinstance(values, str):
                values = [values]
            for value in values:
                if isinstance(value, str) and value:
                    answers[value].add(f"{case_name}.{field_name}")
    assert answers, "bench result_fields.equals에서 정답 식별자를 하나도 수집하지 못했습니다"
    return answers


def _tracked_scan_paths(repo_root=REPO_ROOT):
    output = subprocess.run(
        ["git", "ls-files", "-z", "--", "labhq", "agents", "config"],
        cwd=repo_root,
        check=True,
        capture_output=True,
    ).stdout
    paths = []
    for raw_path in output.split(b"\0"):
        if not raw_path:
            continue
        relative = Path(raw_path.decode("utf-8"))
        parts = relative.parts
        if parts[0] == "labhq":
            if relative.suffix != ".py" or "bench_data" in parts or relative.name.startswith("bench"):
                continue
        paths.append(relative)
    return paths


def _identifier_pattern(identifier):
    return re.compile(rf"(?<![A-Za-z0-9_]){re.escape(identifier)}(?![A-Za-z0-9_])")


def _find_leaks(repo_root, relative_paths, answers):
    patterns = {identifier: _identifier_pattern(identifier) for identifier in answers}
    leaks = []
    for relative in relative_paths:
        path = repo_root / relative
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for identifier, pattern in patterns.items():
                if pattern.search(line):
                    cases = ", ".join(sorted(answers[identifier]))
                    leaks.append(f"{relative.as_posix()}:{line_number}: {identifier} ({cases})")
    return leaks


def test_bench_answer_identifiers_do_not_leak_into_non_bench_code():
    answers = _answer_identifiers()
    leaks = _find_leaks(REPO_ROOT, _tracked_scan_paths(), answers)
    assert not leaks, "벤치 정답 식별자가 비벤치 코드에 있습니다:\n" + "\n".join(leaks)


def test_leak_check_reports_file_line_and_source_case_from_a_temporary_copy(tmp_path):
    answers = _answer_identifiers()
    identifier = "GSE79973"
    relative = Path("labhq") / "answer_leak_probe.py"
    copied_root = tmp_path / "repo-copy"
    copied_path = copied_root / relative
    copied_path.parent.mkdir(parents=True)
    copied_path.write_text(f"safe = True\nanswer = '{identifier}'\n", encoding="utf-8")

    leaks = _find_leaks(copied_root, [relative], answers)

    assert leaks == ["labhq/answer_leak_probe.py:2: GSE79973 (geo-gastric-summary.dataset_id)"]


def test_general_example_wording_is_not_treated_as_an_answer_identifier(tmp_path):
    example = "de novo plastome assembly"
    assert example in (REPO_ROOT / "labhq" / "vocab" / "output_types.yaml").read_text(encoding="utf-8")
    relative = Path("labhq") / "general_example.py"
    copied_root = tmp_path / "repo-copy"
    copied_path = copied_root / relative
    copied_path.parent.mkdir(parents=True)
    copied_path.write_text(f'example = "{example}"\n', encoding="utf-8")

    assert _find_leaks(copied_root, [relative], _answer_identifiers()) == []
