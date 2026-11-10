"""A short, non-blocking data–tool note for a step's input files (#1352).

The note names what each input file looks like and, when the step's operation is known, whether that kind of data
fits the operation under the local relation table (``input_fit.yaml``). It never blocks, asks, installs or calls a
model: any error, unknown file or broken table leaves the note out. Reuse only, nothing new is asserted:

- formats and EDAM ids come from the packaged vocabulary (``labhq.vocab``); a format without a stored EDAM id shows none;
- the format → data kind table below names only kinds the vocabulary already defines for those formats;
- a file's header is checked against its format's magic bytes, so an extension guess and a confirmed type differ;
- for a mismatching input the note lists the kinds the relation table accepts, never a tool.
"""

from __future__ import annotations

import gzip
import io
import os
import zlib
from pathlib import Path
from typing import Any, Iterable

from ..vocab import Vocab, current
from .input_fit import relation_table

TITLE = "[데이터-도구 적합성 참고]"
MAX_LINES = 10
MAX_FILE_LINES = 6
MAX_FILES = 200           # files looked at under inputs/, the rest are not opened
MAX_DEPTH = 4
HEAD_BYTES = 512          # bytes read from the start of a file (after one gzip layer)

# Data kinds the vocabulary's own definitions give these formats (output_types.yaml: sequence_reads "paired FASTQ
# files", alignment "read alignments to a genome", variants "germline SNV and indel calls"). Formats that can hold
# several kinds (h5ad, tsv, csv, rds) have no entry: their kind is never guessed.
FORMAT_DATA = {"fastq": "sequence_reads", "bam": "alignment", "cram": "alignment", "vcf": "variants"}
LABELS = {"fastq": "FASTQ", "fasta": "FASTA", "bam": "BAM", "cram": "CRAM", "vcf": "VCF", "h5ad": "AnnData(h5ad)"}
HDF5 = b"\x89HDF\r\n\x1a\n"


def _head(path: Path) -> bytes | None:
    """Up to HEAD_BYTES from the start, through one gzip/BGZF layer; None when the file cannot be read."""
    try:
        with open(path, "rb") as handle:
            raw = handle.read(HEAD_BYTES * 4)
    except OSError:
        return None
    if raw[:2] == b"\x1f\x8b":
        try:
            return zlib.decompressobj(16 + zlib.MAX_WBITS).decompress(raw, HEAD_BYTES)
        except zlib.error:
            try:
                return gzip.GzipFile(fileobj=io.BytesIO(raw)).read(HEAD_BYTES)
            except (OSError, EOFError):
                return b""
    return raw[:HEAD_BYTES]


def _confirmed(name: str, head: bytes | None) -> str | None:
    """The format the header proves, or None. Only formats with a fixed signature are confirmed."""
    if not head:
        return None
    if head.startswith(b"BAM\x01"):
        return "bam"
    if head.startswith(b"CRAM"):
        return "cram"
    if head.startswith(b"##fileformat=VCF"):
        return "vcf"
    if head.startswith(HDF5) and name.lower().endswith(".h5ad"):
        return "h5ad"
    lines = head.split(b"\n")
    if head.startswith(b"@") and len(lines) > 3 and lines[2].startswith(b"+"):
        return "fastq"
    if head.startswith(b">") and len(lines) > 1:
        return "fasta"
    if name.lower().endswith((".rds", ".rds.gz")) and head[:2] in (b"X\n", b"A\n", b"B\n"):
        return "rds"
    return None


def _files(root: Path) -> list[Path]:
    """Regular files under ``root`` in a stable order, at most MAX_FILES, at most MAX_DEPTH folders deep."""
    found: list[Path] = []

    def walk(folder: Path, depth: int) -> None:
        if depth > MAX_DEPTH or len(found) >= MAX_FILES:
            return
        try:
            entries = sorted(os.scandir(folder), key=lambda entry: entry.name)
        except OSError:
            return
        for entry in entries:
            if len(found) >= MAX_FILES:
                return
            try:
                if entry.is_dir():
                    walk(Path(entry.path), depth + 1)
                elif entry.is_file():
                    found.append(Path(entry.path))
            except OSError:
                continue

    walk(root, 0)
    return found


def describe(path: Path, vocab: Vocab) -> dict[str, Any] | None:
    """What one file looks like: format, whether the header confirmed it, its data kind and stored EDAM id."""
    name = path.name
    guessed = vocab.format_of(name)
    if guessed is None and not name.lower().endswith((".rds", ".rds.gz")):
        return None
    head = _head(path)
    confirmed = _confirmed(name, head)
    fmt = confirmed or guessed
    if fmt is None:
        return None
    row: dict[str, Any] = {"name": name, "format": fmt, "confirmed": confirmed is not None and confirmed == fmt,
                           "data": FORMAT_DATA.get(fmt)}
    if fmt in vocab.terms:
        edam = vocab.edam_id(fmt)
        if edam and edam.startswith("format_"):
            row["edam"] = edam
    if guessed and confirmed and guessed != confirmed:
        row["extension_says"] = guessed
    return row


def _label(fmt: str) -> str:
    return LABELS.get(fmt, "R 객체(.rds)" if fmt == "rds" else fmt)


def hint_lines(paths: Iterable[Path], *, operation: str | None = None, base: Path | None = None,
               vocab: Vocab | None = None) -> list[str]:
    """The note's lines (title first, at most MAX_LINES), or [] when nothing is recognized or anything fails."""
    try:
        vocab = vocab or current()
        if vocab is None:
            return []
        rows = []
        for path in paths:
            row = describe(Path(path), vocab)
            if row is not None:
                try:
                    row["shown"] = Path(path).relative_to(base).as_posix() if base else row["name"]
                except ValueError:
                    row["shown"] = row["name"]
                rows.append(row)
        if not rows:
            return []
        table = relation_table(vocab) if operation else None
        rule = (table or {}).get("operations", {}).get(operation) if table else None
        lines = [TITLE]
        for row in rows[:MAX_FILE_LINES]:
            how = "헤더 확인" if row["confirmed"] else "확장자 추정"
            parts = [f"{_label(row['format'])} ({how})"]
            if row.get("extension_says"):
                parts.append(f"확장자는 {_label(row['extension_says'])}")
            if row.get("data"):
                parts.append(f"데이터 종류 {row['data']}")
            if row.get("edam"):
                parts.append(f"EDAM {row['edam']}")
            lines.append(f"- {row['shown']}: " + ", ".join(parts))
        if len(rows) > MAX_FILE_LINES:
            lines.append(f"- 외 {len(rows) - MAX_FILE_LINES}개 파일은 생략")
        if rule:
            kinds = sorted({row["data"] for row in rows if row.get("data")})
            bad = [kind for kind in kinds if kind in rule["mismatch"]]
            if bad:
                fit = ", ".join(rule["fit"]) or "정해진 종류 없음"
                lines.append(f"- 이 단계 작업({operation})에는 {', '.join(bad)}가 바로 맞지 않습니다. "
                             f"맞는 입력 종류: {fit}. 선행 처리가 필요한지 확인하세요.")
            elif kinds and all(kind in rule["fit"] for kind in kinds):
                lines.append(f"- 이 단계 작업({operation})에 맞는 입력 종류입니다.")
        lines.append("- 참고일 뿐 실행을 막지 않습니다. 판단은 파일 내용을 직접 보고 하세요.")
        return lines[:MAX_LINES]
    except Exception:
        return []


def step_note(inputs_dir: Path, *, operation: str | None = None, vocab: Vocab | None = None) -> str:
    """The note for the files under a step's ``inputs/`` folder, or "" when there is nothing to say."""
    try:
        if not inputs_dir.is_dir():
            return ""
        return "\n".join(hint_lines(_files(inputs_dir), operation=operation, base=inputs_dir, vocab=vocab))
    except Exception:
        return ""


def step_operation(agent_id: str | None, meta: dict[str, Any], vocab: Vocab | None = None) -> str | None:
    """The step's operation key: one the dispatcher stated, else the relation table's agent mapping, else None."""
    try:
        vocab = vocab or current()
        table = relation_table(vocab) if vocab is not None else None
        if not table:
            return None
        stated = meta.get("input_fit_operation")
        if isinstance(stated, str) and stated in table["operations"]:
            return stated
        mapped = table["agent_operations"].get(agent_id) if isinstance(agent_id, str) else None
        return mapped if isinstance(mapped, str) else None
    except Exception:
        return None
