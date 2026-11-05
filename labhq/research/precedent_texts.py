"""Full texts the precedent search stored, handed to execution steps as read-only inputs (#951).

The literature scout that runs before planning stores each paper it read as a text file with a hash receipt
(``fulltext_receipt``). Execution steps are new tasks with their own folders and never saw those files, so a step
looked the papers up again, failed, and the report said "0 sources read" while the files sat on disk (attempts 13
and 14). The runner copies the verified files into the step's ``inputs/precedent/``; this module chooses which
receipts qualify and words what the step and the report are told.
"""
from __future__ import annotations

import re
from typing import Any

MAX_TEXTS = 4  # the precedent schema holds at most four papers
DIR = "precedent"  # inputs/precedent/: a plan step may not use this id (the runner refuses it then)
_SHA = re.compile(r"[a-f0-9]{64}")


def rows(record: Any) -> list[dict[str, str]]:
    """The stored texts of an ``analysis_precedents`` record that carry a path and a hash, unique by hash."""
    if not isinstance(record, dict) or record.get("status") != "ok":
        return []
    found: list[dict[str, str]] = []
    for paper in record.get("papers") or []:
        receipt = paper.get("fulltext_receipt") if isinstance(paper, dict) else None
        if not isinstance(receipt, dict):
            continue
        path, digest = receipt.get("text_file"), receipt.get("text_sha256")
        if not isinstance(path, str) or not path or not isinstance(digest, str) or not _SHA.fullmatch(digest):
            continue
        if any(row["text_sha256"] == digest for row in found):
            continue
        found.append({"paper_id": str(receipt.get("paper_id") or "")[:120],
                      "title": str(paper.get("title") or "")[:200],
                      "source": str(receipt.get("source") or "")[:200],
                      "access_level": str(receipt.get("access_level") or ""),
                      "extraction": str(receipt.get("extraction") or ""),
                      "text_file": path, "text_sha256": digest})
        if len(found) >= MAX_TEXTS:
            break
    return found


def file_name(row: dict[str, str], index: int) -> str:
    """A safe, unique name inside inputs/precedent/: order, paper id, and the start of the hash."""
    label = re.sub(r"[^A-Za-z0-9._-]+", "_", row.get("paper_id", "")).strip("._-")[:60] or "paper"
    return f"{index}-{label}-{row['text_sha256'][:8]}.md"


def step_note(listing: list[tuple[str, dict[str, str]]], unavailable: dict[str, str]) -> str:
    """What a step is told about the stored texts, after the runner staged what it could."""
    lines: list[str] = []
    if listing:
        lines.append(
            "Stored texts of this request's precedent search are readable only at inputs/precedent/ (read-only "
            "copies, hash-checked). Read them BEFORE any lookup, and cite the file and line when you use one:")
        lines.extend(f"- inputs/precedent/{name}: {row.get('title') or row.get('paper_id')} "
                     f"(access_level={row.get('access_level') or 'unknown'}, extraction={row.get('extraction') or 'unknown'})"
                     for name, row in listing)
        lines.append("A paper missing from this list is not stored for the request. If you cannot read a stored text, "
                     "say that this step could not read it; do not say the request has no source text.")
    lines.extend(f"- {reason}" for reason in unavailable.values())
    return "\n".join(lines)


def synthesis_note(record: Any) -> str:
    """For the report writer: the request-level list, so a step's failure is not written up as 'no source exists'."""
    stored = rows(record)
    if not stored:
        return ""
    lines = ["\n\nStored precedent texts recorded for this request. A text is opened read-only to a research step as "
             "inputs/precedent/ only when it passed the runner's path, receipt and hash checks, so a text listed here "
             "may have been unavailable to a given step (that step's own record says which it could read):"]
    lines.extend(f"- {row.get('title') or row.get('paper_id')} (access_level={row.get('access_level') or 'unknown'}, "
                 f"extraction={row.get('extraction') or 'unknown'}, sha256 {row['text_sha256'][:12]})" for row in stored)
    lines.append("Keep two things apart: a step that did not obtain or read a text (a step-level gap) and the request "
                 "having no source text (not true while this list is non-empty). Never report '0 sources read from the "
                 "original text' for the request when a step's own evidence or the review quotes lines from these files.")
    return "\n".join(lines)
