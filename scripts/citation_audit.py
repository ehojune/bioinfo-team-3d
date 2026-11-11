"""Citation audit for the evaluation suite (#1383, #1381 section 3): no model calls.

Checks each cited work against recorded lookup responses and, when the source text is at hand, each quote and number
attributed to it. Lookups are never sent from here: ``--records`` is a JSON file of responses recorded earlier (DOI and
PMID resolution with the registered title), so the same input always gives the same verdict.

Input (``citations.json``)::

    {"citations": [{"id": "c1", "doi": "10.1371/journal.pone.0099625", "pmid": "24926665",
                    "title": "RNA-Seq transcriptome profiling ...", "source": "sources/himes2014.txt",
                    "quotes": ["... exact words ..."], "numbers": ["2.97", "1.5"]}]}

Records (``records.json``)::

    {"doi:10.1371/journal.pone.0099625": {"found": true, "title": "..."}, "pmid:24926665": {"found": false}}

Grades per citation: ``ok``; ``minor`` (title differs only slightly, or an identifier has no record so it was not
checked); ``major`` (identifier not found, title of another work, DOI and PMID name different works, or a quote or
number the source does not hold). Exit 1 when any citation is ``major``, 2 on bad input.

Usage: python scripts/citation_audit.py citations.json --records records.json [--root DIR] [--json out.json]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from labhq.evidence.literal_check import Quantity, Source, is_supported, normalize  # noqa: E402

# Title tokens shared (Jaccard) at or above this are a near match: punctuation, a subtitle or a typo, not another work.
NEAR_TITLE = 0.8
GRADES = ("ok", "minor", "major")


def _tokens(text: str) -> set[str]:
    return set(normalize(text).split())


def title_match(cited: str, registered: str) -> str:
    """``match`` (equal after folding), ``near`` (token overlap at least NEAR_TITLE) or ``mismatch``."""
    if normalize(cited) == normalize(registered):
        return "match"
    a, b = _tokens(cited), _tokens(registered)
    if a and b and len(a & b) / len(a | b) >= NEAR_TITLE:
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

    quotes = [q for q in citation.get("quotes", []) if isinstance(q, str) and q.strip()]
    numbers = [n for n in citation.get("numbers", []) if isinstance(n, (str, int, float))]
    if quotes or numbers:
        source_path = citation.get("source")
        text = None
        if isinstance(source_path, str) and source_path:
            path = (root / source_path).resolve()
            if path.is_file() and root.resolve() in path.parents:
                text = path.read_text(encoding="utf-8")
        if text is None:
            note("minor", "source", "no source text in the bundle; quotes and numbers not checked")
        else:
            source = Source(text)
            for quote in quotes:
                if not source.holds_quote(quote):
                    note("major", "quote", f"source does not hold: {quote[:120]!r}")
            for number in numbers:
                claim = Quantity.parse(str(number))
                if claim is None:
                    note("major", "number", f"not a number: {number!r}")
                elif not is_supported(claim, source.quantities):
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
