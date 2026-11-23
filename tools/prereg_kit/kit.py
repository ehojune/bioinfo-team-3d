"""Check preregistration outputs, run existing labhq checks, or export blinded reports."""

import argparse
import html
import json
import re
import sys
from collections import defaultdict
from contextlib import redirect_stdout
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from io import StringIO
from pathlib import Path
from secrets import token_hex
from tempfile import TemporaryDirectory


CHECK_IDS = ("sample_inventory", "record_match", "label_match", "batch_condition", "confounders")
CHECK_HEADER = "check_id|점검 항목|단위|n|N|evidence|finding|impact"
CONTROL_HEADER = "control_id|observed|result|evidence"
NUMBER = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?")


def read(path):
    return path.read_text(encoding="utf-8-sig")


def unfenced(text, *, include_quotes=False):
    fence, fence_depth = "", 0
    for line in text.splitlines():
        quote = re.match(r"^(?: {0,3}>[ \t]?)+", line) if include_quotes else None
        depth = quote[0].count(">") if quote else 0
        if depth < fence_depth:
            fence, fence_depth = "", 0
        if quote:
            line = line[quote.end():]
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line)
        if not fence:
            if marker:
                fence, fence_depth = marker[1], depth
                yield ""
            else:
                yield line
        elif depth == fence_depth and marker and marker[1].startswith(fence) and not marker[2].strip():
            fence, fence_depth = "", 0


def rows(text, header):
    columns = header.split("|")
    found = defaultdict(list)
    data = False
    pending = False
    for line in unfenced(text):
        line = line.strip()
        cells = [cell.strip() for cell in line.removeprefix("|").removesuffix("|").split("|")]
        if cells == columns:
            pending, data = True, False
        elif pending:
            data = len(cells) == len(columns) and all(re.fullmatch(r":?-{3,}:?", c) for c in cells)
            pending = False
        elif data and "|" in line:
            found[cells[0]].append(cells[1:])
        else:
            data = False
    return found


def decimal(value):
    if isinstance(value, str) and NUMBER.fullmatch(value):
        try:
            return Decimal(value)
        except InvalidOperation:
            pass
    raise ValueError(f"invalid number: {value!r}")


def checklist_problem(row):
    if len(row) != 7:
        return "wrong number of cells"
    _, unit, n, total, evidence, finding, _ = row
    if not all(re.fullmatch(r"[0-9]+", count) for count in (n, total)):
        return "n and N must be recorded as integers"
    if int(n) > int(total):
        return "n exceeds N"
    if not unit or not evidence or (int(total) == 0 and not finding):
        return "missing unit, evidence, or reason for 0/0"
    return None


def control_problem(row):
    if len(row) != 3:
        return "wrong number of cells"
    observed, result, evidence = row
    if not observed or not evidence:
        return "missing observation or evidence"
    if result not in {"pass", "fail", "not_evaluable"} or (observed == "NA") != (result == "not_evaluable"):
        return "use pass/fail with an observation, or not_evaluable with NA"
    if observed != "NA":
        try:
            decimal(observed)
        except ValueError:
            return "observation must be a finite number"
    return None


def row_issues(table, expected, validate):
    issues = {}
    for key in expected:
        matches = table.get(key, [])
        if len(matches) != 1:
            issues[key] = "duplicate row" if matches else "missing row"
        elif problem := validate(matches[0]):
            issues[key] = problem
    return issues


def check_kit(args):
    def read_sheet(name):
        try:
            return read(args.folder / name)
        except FileNotFoundError:
            return ""

    registration = json.loads(read(args.folder / "preregistration.json"), parse_float=Decimal)
    controls = [item["control_id"] for item in registration["controls"]]
    if (not controls or any(not isinstance(key, str) or not key.strip() for key in controls)
            or len(set(controls)) != len(controls)):
        raise ValueError("registered control IDs must be non-empty and unique")
    metric = registration["primary_metric"]
    limits = [metric[key] for key in ("decrease_at_or_below", "increase_at_or_above")]
    if not all(type(value) in (int, Decimal) for value in limits) or not limits[0] < 0 < limits[1]:
        raise ValueError("registration requires finite numeric thresholds on opposite sides of zero")
    low, high = limits
    checklist = rows(read_sheet("sample_record_checklist.md"), CHECK_HEADER)
    results = rows(read_sheet("control_results.md"), CONTROL_HEADER)
    checklist_issues = row_issues(checklist, CHECK_IDS, checklist_problem)
    control_issues = row_issues(results, controls, control_problem)
    fields = rows(read(args.report), "field|value")
    reported = {}
    for field in ("primary_metric_value", "primary_decision"):
        matches = fields.get(field, [])
        if len(matches) == 1 and len(matches[0]) == 1 and matches[0][0]:
            reported[field] = matches[0][0]
    value, decision = reported.get("primary_metric_value"), reported.get("primary_decision")
    expected = None
    if value == "NA":
        expected = "not_evaluable"
    elif value is not None:
        try:
            number = decimal(value)
        except ValueError:
            pass
        else:
            expected = "decrease" if number <= low else "increase" if number >= high else "inconclusive"
    matches = expected is not None and decision == expected
    status = int(bool(checklist_issues or control_issues or not matches))
    return {"checklist_rows_incomplete": len(checklist_issues), "checklist_issues": checklist_issues,
            "control_rows_incomplete": len(control_issues), "control_issues": control_issues,
            "primary_decision": {"value": value, "expected": expected, "reported": decision, "matches": matches}}, status


def existing_checks(args):
    checkout = args.repo or Path(__file__).resolve().parent.parent.parent
    if not (checkout / "scripts/citation_audit.py").is_file():
        raise ValueError("use --repo to point to the labhq checkout")
    sys.path.insert(0, str(checkout.resolve()))
    from labhq.evidence.report_consistency import MAX_TABLE_BYTES, TABLE_SUFFIXES, _targets, check
    from scripts.citation_audit import main as citation_check

    bundle = args.bundle.resolve(strict=True)
    report_path = (bundle / args.report).resolve()
    if not report_path.is_relative_to(bundle):
        raise ValueError("report must be inside the bundle")
    report = read(report_path)
    tables = {}
    for target in _targets("\n".join(unfenced(report))):
        link = target.replace("\\", "/").removeprefix("./")
        if Path(link).suffix.casefold() not in TABLE_SUFFIXES or "://" in link:
            continue
        path = (report_path.parent / link).resolve()
        tables[link] = None
        try:
            if path.is_relative_to(bundle) and path.is_file() and path.stat().st_size <= MAX_TABLE_BYTES:
                tables[link] = read(path)
        except (OSError, UnicodeError):
            pass
    consistency = check(report, tables)
    citations = {"status": "unavailable"}
    paths = [(bundle / name).resolve() for name in ("citations.json", "records.json")]
    if all(path.is_relative_to(bundle) and path.is_file() for path in paths):
        with TemporaryDirectory() as temp, redirect_stdout(StringIO()):
            receipt = Path(temp) / "audit.json"
            status = citation_check([str(paths[0]), "--records", str(paths[1]), "--root", str(bundle), "--json", str(receipt)])
            if status == 2:
                raise ValueError("citation audit rejected its inputs")
            citations = {"status": "checked", **json.loads(read(receipt))}
    result = {"report_consistency": consistency, "citation_audit": citations}
    return result, int(bool(consistency["findings"] or citations.get("counts", {}).get("major")))


def plain_report(text):
    body = re.sub(r"(?ms)\A---[ \t]*\n.*?^(?:---|\.\.\.)[ \t]*(?:\n|\Z)", "", text)
    body = re.sub(r"(?s)<!--.*?(?:-->|\Z)", "", "\n".join(unfenced(body, include_quotes=True)))
    # Indented code can start at a block boundary, not inside a running paragraph.
    body = re.sub(r"(?m)(\A|^[ \t]*\n|^ {0,3}#{1,6}(?:[ \t][^\n]*)?\n)"
                  r"(?:(?: {4}| {0,3}\t)[^\n]*(?:\n|\Z))+", r"\1", body)
    body = re.sub(r"(?is)<(script|style)\b[^>]*>.*?</\1\s*>", "", body)
    body = re.sub(r"(?i)\[\[\s*claim\s*:[^\]\n]*\]\]", "", body)
    body = re.sub(r"(?m)^ {0,3}\[[^\]\n]+\]:[^\n]*$", "", body)
    body = re.sub(r"(!?)\[([^\]\n]*)\](?:\((?:[^()\n]|\([^()\n]*\))*\)|\[[^\]\n]*\])",
                  lambda match: "" if match[1] else match[2], body)
    body = re.sub(r"</?[A-Za-z][\w-]*(?:\s[^<>]*)?/?>|<https?://[^<>\s]+>", "", body)
    body = re.sub(r"(?m)^[ \t]*\|?[ \t]*:?-{3,}:?(?:[ \t]*\|[ \t]*:?-{3,}:?)+[ \t]*\|?[ \t]*\n?", "", body)
    body = re.sub(r"(?m)^ {0,3}(?:#{1,6}[ \t]+|> ?|[-*+] +|[0-9]+[.)] +)", "", body)
    body = re.sub(r"(?m)^ {0,3}(?:={2,}|-{3,}|\*{3,}|_{3,})[ \t]*$", "", body)
    body = re.sub(r"(?<!\w)(\*{1,3}|_{1,3}|`+)(?=\S)([^\n]+?)(?<=\S)\1(?!\w)", r"\2", body)
    body = re.sub(r"(?i)\bhttps?://[^\s<>]+", "", html.unescape(body))
    lines = [" ".join(line.split()) for line in body.splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines).strip()) + "\n"


def blind(args):
    output, mapping = args.output.resolve(), args.mapping.resolve()
    if mapping.is_relative_to(output) or mapping.exists() or output.exists():
        raise ValueError("output and private mapping must be new, with mapping outside output")
    if not mapping.parent.is_dir():
        raise ValueError("mapping parent must already exist")
    reports = {}
    for source in args.reports:
        code = token_hex(8)
        while code in reports:
            code = token_hex(8)
        plain = plain_report(read(source))
        if not plain.strip():
            raise ValueError(f"no report text: {source}")
        reports[code] = (str(source.resolve()), plain)
    codes = sorted(reports)
    manifest = json.dumps({code: reports[code][0] for code in codes}, ensure_ascii=False, indent=2) + "\n"
    output.mkdir()
    for code in codes:
        (output / f"{code}.txt").write_text(reports[code][1], encoding="utf-8", newline="\n")
    with mapping.open("xb") as file:
        file.write(manifest.encode("utf-8"))
    return {"reports": len(codes), "mapping_sha256": sha256(manifest.encode("utf-8")).hexdigest()}, 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(required=True)
    check = commands.add_parser("check", help="count incomplete rows and check the reported primary decision")
    check.add_argument("folder", type=Path)
    check.add_argument("report", type=Path)
    check.set_defaults(run=check_kit)
    existing = commands.add_parser("existing", help="run the existing labhq checks on a bundle")
    existing.add_argument("bundle", type=Path)
    existing.add_argument("--report", default="report.md")
    existing.add_argument("--repo", type=Path)
    existing.set_defaults(run=existing_checks)
    export = commands.add_parser("blind", help="export text reports with randomized filenames")
    export.add_argument("output", type=Path)
    export.add_argument("mapping", type=Path)
    export.add_argument("reports", nargs="+", type=Path)
    export.set_defaults(run=blind)
    args = parser.parse_args()
    try:
        result, status = args.run(args)
    except (ImportError, InvalidOperation, KeyError, OSError, TypeError, ValueError) as error:
        print(f"Input error: {error}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2))
    return status


if __name__ == "__main__":
    raise SystemExit(main())
