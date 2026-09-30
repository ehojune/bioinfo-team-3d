"""Require literals, ``re:PATTERN``, or ``num:EXPECTED:PATTERN`` in answer.md.

Numeric patterns capture ``value`` (optional ``unit=kb`` converts to bp).
``num:COUNT/TOTAL:PATTERN`` also accepts a ``percent`` capture. Every captured
value must agree, so a correct value cannot hide a contradictory assertion.
"""

from decimal import Decimal, InvalidOperation
from pathlib import Path
import re
import sys


def matches(text: str, term: str) -> bool:
    if term.startswith("re:"):
        return re.search(term[3:], text, re.IGNORECASE | re.MULTILINE) is not None
    if not term.startswith("num:"):
        return term.casefold() in text.casefold()
    expected, pattern = term[4:].split(":", 1)
    count, sep, total = expected.partition("/")
    expected_value = Decimal(count)
    found = list(re.finditer(pattern, text, re.IGNORECASE | re.MULTILINE))
    if not found:
        return False
    for match in found:
        groups = match.groupdict()
        raw = groups.get("value") or groups.get("percent")
        if raw is None:
            raise ValueError("numeric pattern needs a value or percent capture")
        value = Decimal(raw.replace(",", ""))
        target = expected_value
        if groups.get("percent") is not None:
            if not sep:
                raise ValueError("percent capture needs COUNT/TOTAL")
            target = expected_value * 100 / Decimal(total)
        elif (groups.get("unit") or "").casefold() == "kb":
            value *= 1000
        if value != target:
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
