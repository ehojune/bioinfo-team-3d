"""Non-blocking data–tool note for a step's inputs (#1352): reuse of the vocabulary and input_fit.yaml only."""
import gzip
import hashlib
import json
import struct
from pathlib import Path

import pytest

from labhq.research import input_fit, input_fit_hint
from labhq.research.input_fit_hint import TITLE, hint_lines, step_note, step_operation
from labhq.vocab import current

FASTQ = b"@r1\nACGT\n+\nIIII\n@r2\nTTGA\n+\nIIII\n"


def bgzf(payload: bytes) -> bytes:
    return gzip.compress(payload)


def write(folder: Path, name: str, body: bytes) -> Path:
    path = folder / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    return path


def lines_for(tmp_path, files, **kwargs):
    return hint_lines([write(tmp_path, name, body) for name, body in files], base=tmp_path, **kwargs)


def test_representative_formats_name_header_confirmation_kind_and_stored_edam(tmp_path):
    lines = lines_for(tmp_path, [
        ("reads_R1.fastq.gz", gzip.compress(FASTQ)),
        ("aln.bam", bgzf(b"BAM\x01" + struct.pack("<i", 0))),
        ("calls.vcf.gz", gzip.compress(b"##fileformat=VCFv4.2\n#CHROM\tPOS\n")),
        ("cells.h5ad", b"\x89HDF\r\n\x1a\n" + b"\0" * 32),
        ("obj.rds", gzip.compress(b"X\n\0\0\0\3")),
    ])
    text = "\n".join(lines)
    assert lines[0] == TITLE and len(lines) <= input_fit_hint.MAX_LINES
    assert "reads_R1.fastq.gz: FASTQ (헤더 확인), 데이터 종류 sequence_reads, EDAM format_1930" in text
    assert "aln.bam: BAM (헤더 확인), 데이터 종류 alignment, EDAM format_2572" in text
    assert "calls.vcf.gz: VCF (헤더 확인), 데이터 종류 variants" in text
    # h5ad has no stored EDAM id and no single data kind: neither is made up.
    h5ad = next(line for line in lines if "cells.h5ad" in line)
    assert "헤더 확인" in h5ad and "EDAM" not in h5ad and "데이터 종류" not in h5ad
    rds = next(line for line in lines if "obj.rds" in line)
    stored = current().edam_id("rds")  # whatever the vocabulary stores, nothing else
    assert "R 객체" in rds and (f"EDAM {stored}" in rds if stored else "EDAM" not in rds)


def test_an_extension_guess_is_marked_as_a_guess(tmp_path):
    lines = lines_for(tmp_path, [("empty.fastq", b"")])
    assert any("empty.fastq: FASTQ (확장자 추정)" in line for line in lines)


def test_a_header_that_disagrees_with_the_extension_says_both(tmp_path):
    lines = lines_for(tmp_path, [("misnamed.fastq", bgzf(b"BAM\x01\0\0\0\0"))])
    assert any("BAM (헤더 확인)" in line and "확장자는 FASTQ" in line for line in lines)


def test_fastq_into_de_gets_only_a_preprocessing_note_from_the_relation_table(tmp_path):
    lines = lines_for(tmp_path, [("reads.fastq.gz", gzip.compress(FASTQ))], operation="de_analysis")
    note = next(line for line in lines if "de_analysis" in line)
    fit = input_fit.relation_table(current())["operations"]["de_analysis"]["fit"]
    assert "sequence_reads가 바로 맞지 않습니다" in note and ", ".join(fit) in note and "선행 처리" in note
    for tool in ("salmon", "STAR", "kallisto", "featureCounts", "DESeq2", "설치"):
        assert tool not in "\n".join(lines)
    assert lines[-1].startswith("- 참고일 뿐 실행을 막지 않습니다")


def test_a_fitting_input_says_so(tmp_path):
    lines = lines_for(tmp_path, [("aln.bam", bgzf(b"BAM\x01\0\0\0\0"))], operation="variant_calling")
    assert any("variant_calling)에 맞는 입력 종류" in line for line in lines)


@pytest.mark.parametrize("files", [[], [("notes.txt", b"hello")], [("unknown.xyz", b"\0\1")]])
def test_nothing_recognized_leaves_the_note_out(tmp_path, files):
    assert lines_for(tmp_path, files) == []


def test_many_files_stay_within_ten_lines(tmp_path):
    lines = lines_for(tmp_path, [(f"s{i}.fastq.gz", gzip.compress(FASTQ)) for i in range(20)],
                      operation="de_analysis")
    assert len(lines) <= 10 and any("외 14개 파일은 생략" in line for line in lines)


def test_errors_unknown_operation_and_broken_table_never_raise(tmp_path, monkeypatch):
    files = [("reads.fastq.gz", gzip.compress(FASTQ))]
    assert not any("작업(" in line for line in lines_for(tmp_path, files, operation="no_such_operation"))
    monkeypatch.setattr(input_fit_hint, "relation_table", lambda vocab: None)  # a broken input_fit.yaml
    assert lines_for(tmp_path, files, operation="de_analysis")[0] == TITLE
    monkeypatch.setattr(input_fit_hint, "current", lambda: None)  # no vocabulary
    assert lines_for(tmp_path, files) == []
    monkeypatch.setattr(input_fit_hint, "hint_lines", lambda *a, **k: 1 / 0)
    assert step_note(tmp_path) == ""
    assert step_note(tmp_path / "missing") == ""


def test_operation_comes_from_a_stated_key_or_the_agent_mapping_only():
    assert step_operation("data_steward", {}) == "data_retrieval"
    assert step_operation("analyst", {"input_fit_operation": "de_analysis"}) == "de_analysis"
    assert step_operation("analyst", {"input_fit_operation": "made_up"}) is None
    assert step_operation("analyst", {}) is None


@pytest.mark.asyncio
async def test_a_runner_step_gets_the_note_in_its_prompt_without_extra_calls_or_cards(tmp_path, monkeypatch):
    from labhq.models import Engine, Task
    from tests.test_private_paths import _capture_runner, _runner_settings
    from tests.test_tool_card_explain import _system_commands

    settings = _runner_settings(tmp_path, None)
    assert settings.semantics in (None, "off", False)  # works with semantics off (v1.0 default)
    root = Path(settings.runner.workspace_root)
    root.mkdir(exist_ok=True)
    folder = root / "source-fetch"
    body = gzip.compress(FASTQ)
    write(folder, "outputs/reads.fastq.gz", body)
    (folder / "manifest.json").write_text(json.dumps({
        "task": {"id": "t-fetch", "request_id": "r", "meta": {"step_id": "fetch"}},
        "runs": {"t-fetch": {"ok": True}}}), encoding="utf-8")
    artifact = {"task_id": "t-fetch", "workdir": str(folder), "outputs": ["outputs/reads.fastq.gz"],
                "output_sha256": {"outputs/reads.fastq.gz": hashlib.sha256(body).hexdigest()}}
    _system_commands(monkeypatch)
    monkeypatch.setattr("labhq.runner.workspace.TaskWorkspace.link_inputs",
                        lambda self, links: {key: "mock legacy link unavailable" for key in links})
    runner, seen = _capture_runner(settings, monkeypatch, Engine.claude_code)
    approvals = []
    monkeypatch.setattr(runner, "request_approval", lambda *a, **k: approvals.append(a), raising=False)
    task = Task(id="down", agent_id="worker", request_id="r", prompt="count reads",
                meta={"kind": "step", "step_id": "de", "upstream_steps": {"fetch": str(folder)},
                      "upstream_dirs": [str(folder)], "upstream_artifacts": {"fetch": artifact},
                      "input_fit_operation": "de_analysis"})
    result = await runner.run_task(task)
    assert result.ok, result.error
    prompt = seen["ctx"].prompt
    assert TITLE in prompt and "fetch/reads.fastq.gz: FASTQ (헤더 확인)" in prompt
    assert "de_analysis)에는 sequence_reads가 바로 맞지 않습니다" in prompt
    assert not approvals
