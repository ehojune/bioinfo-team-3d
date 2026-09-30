"""Require literals, ``re:PATTERN``, or ``num:EXPECTED:PATTERN`` in answer.md.

Numeric patterns capture ``value`` (optional ``unit=kb`` converts to bp).
``num:COUNT/TOTAL:PATTERN`` also accepts ``percent`` and ``total`` captures;
when count and percentage are both given, both must agree. Numeric patterns
select the assertion's context, not every nearby number. An optional ``detail``
alternative consumes subgroup statements without scoring them. At least one
scored assertion is required, and every assertion in that context must agree.
``range:MIN..MAX:PATTERN`` checks ordered ``low``/``high`` captures within an
inclusive design envelope (bp, or kb via ``unit``/``low_unit``).
"""

from decimal import Decimal, InvalidOperation
from pathlib import Path
import re
import sys


def numeric_text(text: str) -> str:
    """Ignore inline Markdown decoration without changing IDs or line scopes."""
    return text.replace("`", "").replace("**", "").replace("__", "")


def number(raw: str, unit: str | None = None) -> Decimal:
    value = Decimal(raw.replace(",", ""))
    return value * 1000 if (unit or "").casefold() == "kb" else value


def matches(text: str, term: str) -> bool:
    if term.startswith("re:"):
        return re.search(term[3:], text, re.IGNORECASE | re.MULTILINE) is not None
    if not term.startswith(("num:", "range:")):
        return term.casefold() in text.casefold()
    kind, expected, pattern = term.split(":", 2)
    found = [match for match in re.finditer(pattern, numeric_text(text), re.IGNORECASE | re.MULTILINE)
             if match.groupdict().get("detail") is None]
    if not found:
        return False
    if kind == "range":
        minimum, maximum = map(Decimal, expected.split(".."))
        if minimum >= maximum:
            raise ValueError("range needs MIN < MAX")
        for match in found:
            groups = match.groupdict()
            if groups.get("low") is None or groups.get("high") is None:
                raise ValueError("range pattern needs low and high captures")
            low = number(groups["low"], groups.get("low_unit") or groups.get("unit"))
            high = number(groups["high"], groups.get("unit"))
            if not minimum <= low < high <= maximum:
                return False
        return True
    count, sep, total = expected.partition("/")
    expected_value = Decimal(count)
    for match in found:
        groups = match.groupdict()
        if groups.get("value") is None and groups.get("percent") is None:
            raise ValueError("numeric pattern needs a value or percent capture")
        if groups.get("value") is not None:
            if number(groups["value"], groups.get("unit")) != expected_value:
                return False
        if groups.get("percent") is not None:
            if not sep:
                raise ValueError("percent capture needs COUNT/TOTAL")
            if number(groups["percent"]) != expected_value * 100 / Decimal(total):
                return False
        if groups.get("total") is not None:
            if not sep:
                raise ValueError("total capture needs COUNT/TOTAL")
            if number(groups["total"]) != Decimal(total):
                return False
    return True


def main() -> int:
    answer = Path(sys.argv[1]) / "answer.md"
    text = answer.read_text(encoding="utf-8") if answer.is_file() else ""
    try:
        missing = [term for term in sys.argv[2:] if not matches(text, term)]
    except (ValueError, InvalidOperation, re.error) as exc:
        print(f"invalid check: {exc}")
        return 2
    if missing:
        print("missing: " + ", ".join(missing))
        return 1
    print(f"ok: {len(sys.argv) - 2} terms")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
