"""Deterministic benchmark check: every configured term must occur in answer.md."""

from pathlib import Path
import sys


def main() -> int:
    answer = Path(sys.argv[1]) / "answer.md"
    text = answer.read_text(encoding="utf-8").casefold() if answer.is_file() else ""
    missing = [term for term in sys.argv[2:] if term.casefold() not in text]
    if missing:
        print("missing: " + ", ".join(missing))
        return 1
    print(f"ok: {len(sys.argv) - 2} terms")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
