"""The manual is split into chapters under docs/manual/ (#1166); tests that check its wording read them together."""
from pathlib import Path

DOCS = Path(__file__).resolve().parents[1] / "docs"


def manual_text(english: bool = False) -> str:
    """All chapters in order, as the one document the manual used to be."""
    names = sorted(p for p in (DOCS / "manual").glob("*.md") if p.name.endswith(".en.md") == english)
    return "\n".join(p.read_text(encoding="utf-8") for p in names)
