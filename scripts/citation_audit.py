"""Citation audit for the evaluation suite (#1383, #1381 section 3): no model calls.

Checks each cited work against recorded lookup responses and, when the source text is at hand, each quote and number
attributed to it. Lookups are never sent from here: ``--records`` is a JSON file of responses recorded earlier (DOI and
PMID resolution with the registered title), so the same input always gives the same verdict.

Input (``citations.json``)::

    {"citations": [{"id": "c1", "doi": "10.1371/journal.pone.0099625", "pmid": "24926665",
                    "title": "RNA-Seq transcriptome profiling ...", "source": "sources/himes2014.txt",
                    "quotes": ["... exact words ..."], "numbers": ["2.97", "1.5"]}]}

Numbers are strings: a JSON number has already lost how it was written (2.970 reads as 2.97).

Records (``records.json``)::

    {"doi:10.1371/journal.pone.0099625": {"found": true, "title": "..."}, "pmid:24926665": {"found": false}}

Grades per citation: ``ok``; ``minor`` (title differs only slightly, or an identifier has no record so it was not
checked); ``major`` (identifier not found, title of another work, DOI and PMID name different works, or a quote or
number the source does not hold). Exit 1 when any citation is ``major``, 2 on bad input.

Usage: python scripts/citation_audit.py citations.json --records records.json [--root DIR] [--json out.json]
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from labhq.evidence.literal_check import Quantity, Source, is_supported, normalize, quantities_in  # noqa: E402

# Title tokens shared (Jaccard) at or above this are a near match: punctuation, a subtitle or a typo, not another work.
NEAR_TITLE = 0.8
GRADES = ("ok", "minor", "major")
# Scientific notation, in the claim ("1e-5", "2.3E+04") and in source text (also "1.2 × 10^-5", "1.2x10-5").
_SCI_CLAIM = re.compile(r"[-+]?(\d+(?:\.\d+)?)[eE]([-+]?\d+)")
_SCI_TEXT = re.compile(r"(?<![\w.])([-+−]?\d+(?:\.\d+)?)(?:[eE]|\s*[×xX]\s*10\^?)([-+−]?\d+)(?![\w.])")


def _ascii_signs(text: str) -> str:
    """Typographic minus and en dash written as ``-``, so a claim and a source spell signs the same way."""
    return text.replace("−", "-").replace("–", "-")


def _significant(mantissa: str) -> int:
    digits = mantissa.replace(".", "").lstrip("0")
    return max(len(digits), 1)


def _sci_values(text: str) -> list[float]:
    return [float(m.group(1).replace("−", "-")) * 10 ** int(m.group(2).replace("−", "-"))
            for m in _SCI_TEXT.finditer(text)]


def source_numbers(text: str) -> tuple[tuple[Quantity, ...], list[float]]:
    """The numbers a source writes: plain ones (with their decimal places) and scientific ones as whole values.

    A scientific span is taken out before the plain numbers are read, so ``1e-5`` is one value and never the plain
    numbers ``1`` and ``5``."""
    text = _ascii_signs(text)  # one spelling of signs for both readers, claim and source alike
    return quantities_in(_SCI_TEXT.sub(" ", text)), _sci_values(text)


def number_held(number: str, source: Source) -> bool | None:
    """Whether the source holds ``number`` at its written precision; None when it is not a number.

    A plain number is compared at its decimal places (``literal_check``). A number in scientific notation is compared
    at its significant digits against every number the source writes, so ``1e-5`` matches ``1e-05`` or ``1 × 10^-5``
    and never a value that merely rounds to zero, such as ``0.2``."""
    text = _ascii_signs(number.strip())
    plain, scientific = source_numbers(source.text)
    sci = _SCI_CLAIM.fullmatch(text)
    if sci:
        value, sig = float(text), _significant(sci.group(1))
        # an exact value always counts; otherwise a nonzero value must agree at the claim's significant digits
        return any(v == value or (v != 0 and value != 0 and f"{v:.{sig - 1}e}" == f"{value:.{sig - 1}e}")
                   for v in [q.value for q in plain] + scientific)
    claim = Quantity.parse(text)
    if claim is None:
        return None
    if is_supported(claim, plain):
        return True
    return claim.value != 0 and any(round(v, claim.decimals) == round(claim.value, claim.decimals) for v in scientific)


# A registry title can carry markup (Europe PMC: "<i>KRAS</i>"); that is formatting, not a different title.
_TAG = re.compile(r"</?[A-Za-z][A-Za-z0-9]*(?:\s[^<>]*)?/?>")
# A stored text that only points at another file. A step that does not copy third-party text into a bundle writes
# "# 포인터: ..." or "# Pointer: ..." in its place, so the source itself is not in the bundle (#910 11th run).
# The marker word must be followed by a colon: "# Pointer analysis" is a real heading of a real source.
_POINTER = re.compile(r"\s*#\s*(?:포인터|pointer)\s*[:：]", re.I)


def _plain_titles(text: str) -> tuple[str, str]:
    """The title without markup, read two ways: the tag joining its neighbours ("CO<sub>2</sub>" -> "CO2") and the tag
    as a word break ("KRAS<sup>G12C</sup>" -> "KRAS G12C", the same words as "KRAS(G12C)"). Entities come first, so an
    escaped tag ("&lt;sup&gt;") is a tag too."""
    text = html.unescape(text)
    return normalize(_TAG.sub("", text)), normalize(_TAG.sub(" ", text))


def _overlap(a: str, b: str) -> float:
    x, y = set(a.split()), set(b.split())
    return len(x & y) / len(x | y) if x and y else 0.0


def is_pointer(text: str) -> bool:
    """Whether a stored text is only a pointer to the source (its first non-empty line is a pointer heading)."""
    first = next((line for line in text.splitlines() if line.strip()), "")
    return bool(_POINTER.match(first))


def title_match(cited: str, registered: str) -> str:
    """``match`` (equal after folding and dropping markup), ``near`` (token overlap at least NEAR_TITLE) or
    ``mismatch``."""
    pairs = [(a, b) for a in _plain_titles(cited) for b in _plain_titles(registered)]
    # Spacing aside, so a title whose tags need different treatment ("KRAS<sup>G12C</sup> and CO<sub>2</sub>")
    # still equals "KRAS(G12C) and CO2": markup and punctuation decide where words break, not what the words are.
    if any(a == b or a.replace(" ", "") == b.replace(" ", "") for a, b in pairs):
        return "match"
    if max(_overlap(a, b) for a, b in pairs) >= NEAR_TITLE:
        return "near"
    return "mismatch"


def _identifiers(citation: dict) -> list[tuple[str, str]]:
    out = []
    for kind in ("doi", "pmid"):
        value = citation.get(kind)
        if isinstance(value, str) and value.strip():
            value = value.strip()
            out.append((kind, value.lower() if kind == "doi" else value))
    return out


def audit_one(citation: dict, records: dict, root: Path) -> dict:
    findings: list[dict] = []

    def note(grade: str, check: str, detail: str) -> None:
        findings.append({"grade": grade, "check": check, "detail": detail})

    ids = _identifiers(citation)
    if not ids:
        note("major", "identifier", "no DOI or PMID")
    titles: dict[str, str] = {}
    for kind, value in ids:
        record = records.get(f"{kind}:{value}")
        if not isinstance(record, dict):
            note("minor", "existence", f"{kind}:{value} has no recorded lookup; not checked")
            continue
        if record.get("found") is not True:
            note("major", "existence", f"{kind}:{value} not found")
            continue
        registered = record.get("title")
        if not isinstance(registered, str) or not registered.strip():
            note("minor", "title", f"{kind}:{value} record has no title; not checked")
            continue
        titles[kind] = registered
        cited = citation.get("title")
        if isinstance(cited, str) and cited.strip():
            verdict = title_match(cited, registered)
            if verdict == "near":
                note("minor", "title", f"{kind}:{value} title differs slightly from the registered one")
            elif verdict == "mismatch":
                note("major", "title", f"{kind}:{value} is registered as another work: {registered!r}")
    if len(titles) == 2 and title_match(titles["doi"], titles["pmid"]) == "mismatch":
        note("major", "identifier", "DOI and PMID are registered as different works")

    quotes, numbers = [], []
    for key, kept in (("quotes", quotes), ("numbers", numbers)):
        entries = citation.get(key) or []
        if not isinstance(entries, list):
            note("major", key, f"{key} must be a list")
            continue
        for entry in entries:
            if isinstance(entry, str) and entry.strip():
                kept.append(entry)
            else:  # a JSON number has already lost its written precision (2.970 -> 2.97), so numbers are strings
                note("major", key, f"entry is not a non-empty string: {entry!r}")
    if quotes or numbers:
        source_path = citation.get("source")
        text = None
        if isinstance(source_path, str) and source_path:
            path = (root / source_path).resolve()
            if path.is_file() and root.resolve() in path.parents:
                text = path.read_text(encoding="utf-8")
        if text is not None and is_pointer(text):
            note("minor", "source", f"{source_path} only points at the source, which is not in the bundle; "
                                    "quotes and numbers not checked")
        elif text is None:
            note("minor", "source", "no source text in the bundle; quotes and numbers not checked")
        else:
            source = Source(text)
            for quote in quotes:
                if not source.holds_quote(quote):
                    note("major", "quote", f"source does not hold: {quote[:120]!r}")
            for number in numbers:
                held = number_held(number, source)
                if held is None:
                    note("major", "number", f"not a number: {number!r}")
                elif not held:
                    note("major", "number", f"source does not hold {number} at its written precision")

    grade = max((f["grade"] for f in findings), key=GRADES.index, default="ok")
    return {"id": citation.get("id"), "grade": grade, "findings": findings}


def audit(citations: list[dict], records: dict, root: Path) -> dict:
    rows = [audit_one(c, records, root) for c in citations]
    counts = {g: sum(r["grade"] == g for r in rows) for g in GRADES}
    return {"citations": rows, "counts": counts, "total": len(rows)}


def _load(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("citations", type=Path)
    ap.add_argument("--records", type=Path, required=True, help="recorded DOI/PMID lookups (JSON)")
    ap.add_argument("--root", type=Path, help="folder that source paths are relative to (default: the citations file's)")
    ap.add_argument("--json", type=Path, help="also write the full result here")
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    try:
        data, records = _load(args.citations), _load(args.records)
        citations = data.get("citations") if isinstance(data, dict) else None
        if not isinstance(citations, list) or not all(isinstance(c, dict) for c in citations) or not isinstance(records, dict):
            raise ValueError("citations must be {'citations': [...]} and records an object")
        for c in citations:
            for key, kinds in (("quotes", (str,)), ("numbers", (str,))):
                entries = c.get(key)
                if entries is None:
                    continue
                if not isinstance(entries, list):
                    raise ValueError(f"{c.get('id')!r}: {key} must be a list")
                if any(isinstance(e, bool) or not isinstance(e, kinds) for e in entries):
                    raise ValueError(f"{c.get('id')!r}: every entry of {key} must be {' or '.join(k.__name__ for k in kinds)}")
    except (OSError, ValueError) as error:
        print(f"입력 오류: {error}", file=sys.stderr)
        return 2
    result = audit(citations, records, args.root or args.citations.parent)
    for row in result["citations"]:
        print(f"{row['grade']:5}  {row['id']}")
        for f in row["findings"]:
            print(f"       {f['grade']:5} {f['check']}: {f['detail']}")
    c = result["counts"]
    print(f"인용 {result['total']}건: ok {c['ok']} · minor {c['minor']} · major {c['major']}")
    if args.json:
        args.json.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return 1 if c["major"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
