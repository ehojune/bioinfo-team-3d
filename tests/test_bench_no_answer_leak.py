import re
import subprocess
import unicodedata
from collections import defaultdict
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
CASE_DIR = REPO_ROOT / "labhq" / "bench_data" / "cases"
IDENTIFIER_TYPES = {"string", "string_list"}
ALLOWED_OCCURRENCES = {
    # (relative path, identifier): "왜 런타임 prompt의 벤치 정답 누출이 아닌지"
}
SEPARATOR_CHARS = r"[-_.\s]"
SEPARATOR_RE = re.compile(rf"{SEPARATOR_CHARS}+")


def _identifier_forms(value):
    normalized = unicodedata.normalize("NFKC", value).casefold()
    compact = SEPARATOR_RE.sub("", normalized)
    return normalized, compact if len(compact) >= 4 else None


def _answer_identifiers(case_dir=CASE_DIR):
    answers = defaultdict(set)
    canonical_identifiers = {}
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
                    normalized, _ = _identifier_forms(value)
                    canonical = canonical_identifiers.setdefault(normalized, value)
                    answers[canonical].add(f"{case_name}.{field_name}")
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
        if "tests" in parts:
            continue
        if parts[0] == "labhq" and "bench_data" in parts:
            continue
        if parts[0] == "labhq" and relative.name.startswith("bench") and relative.suffix == ".py":
            continue
        if parts[:3] == ("labhq", "web", "vendor"):
            continue
        if b"\0" in (repo_root / relative).read_bytes():
            continue
        paths.append(relative)
    return paths


def _identifier_patterns(identifier):
    normalized, compact = _identifier_forms(identifier)
    patterns = [re.compile(rf"(?<![A-Za-z0-9]){re.escape(normalized)}(?![A-Za-z0-9])")]
    if compact is not None:
        flexible = f"{SEPARATOR_CHARS}*".join(re.escape(character) for character in compact)
        patterns.append(re.compile(rf"(?<![A-Za-z0-9]){flexible}(?![A-Za-z0-9])"))
    return patterns


def _find_leaks(repo_root, relative_paths, answers):
    patterns = {identifier: _identifier_patterns(identifier) for identifier in answers}
    leaks = []
    for relative in relative_paths:
        path = repo_root / relative
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            normalized_line, _ = _identifier_forms(line)
            for identifier, identifier_patterns in patterns.items():
                if any(pattern.search(normalized_line) for pattern in identifier_patterns):
                    if (relative.as_posix(), identifier) in ALLOWED_OCCURRENCES:
                        continue
                    cases = ", ".join(sorted(answers[identifier]))
                    leaks.append(f"{relative.as_posix()}:{line_number}: {identifier} ({cases})")
    return leaks


def test_bench_answer_identifiers_do_not_leak_into_non_bench_runtime_files():
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


def test_leak_check_covers_a_research_pack_yaml_in_a_temporary_copy(tmp_path):
    answers = _answer_identifiers()
    identifier = "GKN1"
    relative = Path("labhq") / "research" / "packs" / "answer_leak_probe.yaml"
    copied_root = tmp_path / "repo-copy"
    copied_path = copied_root / relative
    copied_path.parent.mkdir(parents=True)
    copied_path.write_text(f"reviewer_questions:\n  - Check {identifier}\n", encoding="utf-8")

    leaks = _find_leaks(copied_root, [relative], answers)

    assert leaks == [f"{relative.as_posix()}:2: GKN1 (geo-gastric-summary.down_gene)"]


def test_general_example_wording_is_not_treated_as_an_answer_identifier(tmp_path):
    example = "de novo plastome assembly"
    assert example in (REPO_ROOT / "labhq" / "vocab" / "output_types.yaml").read_text(encoding="utf-8")
    relative = Path("labhq") / "general_example.py"
    copied_root = tmp_path / "repo-copy"
    copied_path = copied_root / relative
    copied_path.parent.mkdir(parents=True)
    copied_path.write_text(f'example = "{example}"\n', encoding="utf-8")

    assert _find_leaks(copied_root, [relative], _answer_identifiers()) == []


def test_leak_check_normalizes_case_separators_and_fullwidth_characters(tmp_path):
    relative = Path("labhq") / "answer_variants.py"
    copied_root = tmp_path / "repo-copy"
    copied_path = copied_root / relative
    copied_path.parent.mkdir(parents=True)
    copied_path.write_text(
        "genes = ['gkn1', 'COL1-A1']\nstructure = '６ｏｉｍ'\n",
        encoding="utf-8",
    )

    leaks = _find_leaks(copied_root, [relative], _answer_identifiers())

    assert leaks == [
        "labhq/answer_variants.py:1: GKN1 (geo-gastric-summary.down_gene)",
        "labhq/answer_variants.py:1: COL1A1 (geo-gastric-summary.up_gene)",
        "labhq/answer_variants.py:2: 6OIM (inco-kras-g12c.pdb_id)",
    ]


def test_leak_check_does_not_match_identifiers_inside_general_words(tmp_path):
    relative = Path("labhq") / "general_words.py"
    copied_root = tmp_path / "repo-copy"
    copied_path = copied_root / relative
    copied_path.parent.mkdir(parents=True)
    copied_path.write_text(
        "labels = ['megkn1like', 'precol1a1post', 'x6oimy']\n",
        encoding="utf-8",
    )

    assert _find_leaks(copied_root, [relative], _answer_identifiers()) == []


def test_leak_check_finds_identifiers_next_to_korean_and_parentheses(tmp_path):
    relative = Path("labhq") / "korean_answer_variants.py"
    copied_root = tmp_path / "repo-copy"
    copied_path = copied_root / relative
    copied_path.parent.mkdir(parents=True)
    copied_path.write_text(
        "prompt = 'GKN1을 확인하고 (COL1A1과) 6OIM은구조를 비교한다'\n",
        encoding="utf-8",
    )

    leaks = _find_leaks(copied_root, [relative], _answer_identifiers())

    assert leaks == [
        "labhq/korean_answer_variants.py:1: GKN1 (geo-gastric-summary.down_gene)",
        "labhq/korean_answer_variants.py:1: COL1A1 (geo-gastric-summary.up_gene)",
        "labhq/korean_answer_variants.py:1: 6OIM (inco-kras-g12c.pdb_id)",
    ]


def test_short_identifiers_do_not_use_separator_removed_matching(tmp_path):
    relative = Path("labhq") / "short_identifier.py"
    copied_root = tmp_path / "repo-copy"
    copied_path = copied_root / relative
    copied_path.parent.mkdir(parents=True)
    copied_path.write_text("label = 'A-B-C'\n", encoding="utf-8")

    assert _find_leaks(copied_root, [relative], {"ABC": {"case.field"}}) == []
