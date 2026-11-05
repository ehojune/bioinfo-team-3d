"""Literal quote and number checks adapted from OKF Loremaster.

Original: https://github.com/PFreda-Lab/okf-loremaster
Commit: ead3819
Files: src/okf_loremaster/verification.py and src/okf_loremaster/schemas/limits.py
License: Apache-2.0; see NOTICE.md and LICENSE-APACHE-2.0.txt in this directory.

Modified for labhq: removed OKF schema dependencies, kept Python 3.10 support,
and preserved Unicode letters and numbers while normalizing quotes.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Sequence
import unicodedata


MINUS = "\N{MINUS SIGN}"
EN_DASH = "\N{EN DASH}"
MIDDLE_DOT = "\N{MIDDLE DOT}"

_DASHES = "-" + MINUS + EN_DASH
_NUMBER = re.compile(rf"[{_DASHES}]?\d[\d,]*(?:[.{MIDDLE_DOT}]\d+)?")
_DECIMALS = re.compile(rf"[.{MIDDLE_DOT}](\d+)$")
_BOUNDARY = re.compile(r"(?<=[.!?])[\"'\)\]]*\s+")
_ABBREVIATIONS = frozenset(
    {
        "al",
        "approx",
        "ca",
        "cf",
        "e.g",
        "eq",
        "est",
        "fig",
        "figs",
        "i.e",
        "max",
        "min",
        "no",
        "ref",
        "refs",
        "st",
        "vs",
    }
)
_TRAILING_PUNCT = "\"'()[]"

# A larger located span is usually a flattened table rather than one sentence.
MAX_LOCATED_QUOTE_WORDS = 80


@dataclass(frozen=True, slots=True)
class Quantity:
    """A number and the count of decimal places in its written form."""

    value: float
    decimals: int

    @classmethod
    def parse(cls, literal: str) -> Quantity | None:
        normalized = literal.replace(",", "").replace(MIDDLE_DOT, ".")
        for dash in (MINUS, EN_DASH):
            normalized = normalized.replace(dash, "-")
        try:
            value = float(normalized)
        except ValueError:
            return None
        found = _DECIMALS.search(literal)
        return cls(value=value, decimals=len(found.group(1)) if found else 0)

    @classmethod
    def of(cls, value: float) -> Quantity:
        """Represent a claim at the precision carried by its float value."""
        text = f"{float(value):.10f}".rstrip("0")
        _, _, fraction = text.partition(".")
        return cls(value=float(value), decimals=len(fraction))


def quantities_in(text: str) -> tuple[Quantity, ...]:
    """Return every written number in a passage, in order."""
    found: list[Quantity] = []
    for match in _NUMBER.finditer(text):
        literal = match.group(0)
        if literal[0] in _DASHES and not _is_sign(text, match.start()):
            literal = literal[1:]
        quantity = Quantity.parse(literal)
        if quantity is not None:
            found.append(quantity)
    return tuple(found)


def _is_sign(text: str, start: int) -> bool:
    """Return whether the dash at ``start`` negates the following number."""
    index = start - 1
    if index < 0:
        return True
    if text[index].isdigit() or text[index].isalpha():
        return False
    spaced = index
    while spaced >= 0 and text[spaced] in " \t":
        spaced -= 1
    return not (spaced < index and spaced >= 0 and text[spaced].isdigit())


def is_supported(claim: Quantity, found: Sequence[Quantity]) -> bool:
    """Return whether a source number supports a claim at the claim's precision."""
    return any(
        round(other.value, claim.decimals) == round(claim.value, claim.decimals)
        for other in found
    )


def normalize(text: str) -> str:
    """Fold case, punctuation and whitespace while preserving Unicode alphanumerics."""
    folded = unicodedata.normalize("NFKC", text).casefold()
    return " ".join("".join(char if char.isalnum() else " " for char in folded).split())


def sentences(text: str) -> list[str]:
    """Split prose into sentences while retaining terminators."""
    stripped = " ".join(text.split())
    if not stripped:
        return []
    parts: list[str] = []
    start = 0
    for match in _BOUNDARY.finditer(stripped):
        candidate = stripped[start : match.start()]
        if _ends_with_abbreviation(candidate):
            continue
        parts.append(candidate)
        start = match.end()
    tail = stripped[start:]
    if tail:
        parts.append(tail)
    return parts


def _ends_with_abbreviation(fragment: str) -> bool:
    last = fragment.rstrip(_TRAILING_PUNCT).split()
    if not last:
        return False
    token = last[-1].rstrip(".").lower()
    return token in _ABBREVIATIONS or len(token) == 1


class Source:
    """One source text prepared for repeated quote and quantity checks."""

    __slots__ = ("_normalized", "_quantities", "_sentences", "text")

    def __init__(self, text: str) -> None:
        self.text = text
        self._normalized = normalize(text)
        self._quantities = quantities_in(text)
        self._sentences = tuple(
            (part, normalize(part)) for line in text.splitlines() for part in sentences(line)
        )

    @property
    def quantities(self) -> tuple[Quantity, ...]:
        return self._quantities

    def sentence_for(self, locator: str) -> str:
        """Return the bounded source sentence opened by ``locator``, or an empty string."""
        needle = normalize(locator)
        if not needle:
            return ""
        for original, folded in self._sentences:
            if needle in folded:
                return _windowed(original, needle)
        return ""

    def holds_quote(self, quote: str) -> bool:
        needle = normalize(quote)
        return bool(needle) and needle in self._normalized

    def holds(self, value: float) -> bool:
        return is_supported(Quantity.of(value), self._quantities)


def _windowed(sentence: str, needle: str) -> str:
    """Return a sentence, or an 80-word source window beginning at ``needle``."""
    words = sentence.split()
    if len(words) <= MAX_LOCATED_QUOTE_WORDS:
        return sentence
    start = _opens_at(words, needle.split())
    return " ".join(words[start : start + MAX_LOCATED_QUOTE_WORDS])


def _opens_at(words: Sequence[str], needle: Sequence[str]) -> int:
    tokens: list[str] = []
    owners: list[int] = []
    for index, word in enumerate(words):
        for token in normalize(word).split():
            tokens.append(token)
            owners.append(index)

    span = len(needle)
    for start in range(len(tokens) - span + 1):
        if tokens[start : start + span] == list(needle):
            return owners[start]
    return 0
