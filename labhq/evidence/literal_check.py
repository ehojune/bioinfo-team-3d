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
from collections.abc import Mapping
import contextlib
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
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
MAX_SOURCE_BYTES = 2_000_000


def _source_bytes(root, relative: str, limit: int = MAX_SOURCE_BYTES) -> bytes:
    """Bounded read through held directories; never follow a link or accept a hard link."""
    from ..adapters.held_dir import HeldDir

    parts = PurePosixPath(relative.replace('\\', '/')).parts
    if not parts or any(p in {'', '.', '..'} or ':' in p or p.startswith('/') for p in parts):
        raise ValueError('source path is not workspace-relative')
    with contextlib.ExitStack() as stack:
        held = stack.enter_context(HeldDir.hold(Path(root)))
        for part in parts[:-1]:
            held = stack.enter_context(held.child(part))
        with os.fdopen(held.open_read_file(parts[-1]), 'rb') as opened:
            info = os.fstat(opened.fileno())
            if info.st_nlink != 1 or info.st_size > limit:
                raise ValueError('source is linked or too large')
            data = opened.read(limit + 1)
            if len(data) > limit:
                raise ValueError('source grew beyond limit')
            return data


def _paper_keys(receipt):
    from ..tools.annot_literature import parse_paper_id
    aliases = receipt.get('paper_ids')
    aliases = aliases[:4] if isinstance(aliases, list) else []
    keys = set()
    for raw in [receipt.get('paper_id'), *aliases]:
        try:
            keys.add(parse_paper_id(raw).key)
        except ValueError:
            pass
    return keys


def _saved_papers(root):
    """Index bounded plain sidecars. Aliases come from the tool's receipt, never a title or ledger guess."""
    from ..adapters.held_dir import HeldDir
    from ..tools.annot_literature import parse_paper_id
    found = {}
    try:
        with HeldDir.hold(Path(root)) as held, held.child('.tmp') as tmp, tmp.child('literature') as papers:
            entries = papers.entries(512)
        for entry in entries:
            if entry.kind != 'file' or not entry.name.endswith('.md.receipt.json'):
                continue
            try:
                path = '.tmp/literature/' + entry.name.removesuffix('.receipt.json')
                receipt = json.loads(_source_bytes(root, path + '.receipt.json', 128_000))
                original = parse_paper_id(receipt.get('paper_id'))
                if original.file_name() != entry.name.removesuffix('.receipt.json'):
                    continue
                if receipt.get('access_level') not in {'full_text', 'abstract'}:
                    continue
                for key in _paper_keys(receipt):
                    found.setdefault(key, []).append((root, path, receipt.get('text_sha256')))
            except (OSError, ValueError, TypeError, AttributeError):
                continue
    except (OSError, ValueError, TypeError):
        pass
    return found


def source_texts_for(ledger: Mapping, result, upstream=(), precedents=()) -> dict[str, str]:
    """Read only collected/hash-bound artifacts or the cited paper's saved hash receipt.

    ``upstream`` contains successful DAG ancestors only. A source's observation, URI and arbitrary locator
    never become file paths. Missing, changed, unsafe or oversized text remains unavailable; no network lookup.
    """
    from .claims import normalize_artifact_path, registry_id
    from ..research.precedent_texts import file_name, rows as precedent_rows
    from ..tools.annot_literature import parse_paper_id

    def get(row, key):
        return row.get(key) if isinstance(row, Mapping) else getattr(row, key, None)

    if isinstance(precedents, Mapping):
        aliases = {receipt.get('text_sha256'): _paper_keys(receipt)
            for item in precedents.get('papers') or [] if isinstance(item, Mapping)
            and isinstance(receipt := item.get('fulltext_receipt'), Mapping)
            and isinstance(receipt.get('text_sha256'), str)}
        precedents = [{**row, 'paper_ids': list(aliases.get(row['text_sha256'], ())) }
                      for row in precedent_rows(precedents)]
    artifacts = {}
    for owner in (result, *upstream):
        root = get(owner, 'workdir')
        if not root:
            continue
        hashes = get(owner, 'output_sha256') or {}
        for path in get(owner, 'outputs') or []:
            names = [f'{root}/{path}']
            if get(owner, 'workdir_id'):
                names.append(f"{get(owner, 'workdir_id')}/{path}")
            if owner is result:
                names.append(path)
            for name in names:
                artifacts[normalize_artifact_path(name)] = (root, path, hashes.get(path))
    refs = {str(row.get('artifact_id')): str(row.get('path') or '')
            for row in ledger.get('artifact_refs') or [] if isinstance(row, Mapping)}
    texts, cache, saved = {}, {}, None
    remaining = 16 * MAX_SOURCE_BYTES
    for row in ledger.get('evidence') or []:
        if not isinstance(row, Mapping) or row.get('status') not in (None, 'observed'):
            continue
        source = row.get('source') or {}
        if not isinstance(source, Mapping):
            continue
        candidates = []
        try:
            if source.get('artifact_id'):
                candidates.append(artifacts[normalize_artifact_path(refs[str(source['artifact_id'])])])
            else:
                scheme, value = ((source['id_scheme'], source['id_value'])
                    if source.get('id_scheme') and source.get('id_value')
                    else registry_id(str(source.get('uri') or '')) or ('', ''))
                paper = parse_paper_id(f'{scheme}:{value}')
                root = get(result, 'workdir')
                if saved is None:
                    saved = _saved_papers(root)
                candidates.extend(sorted(saved.get(paper.key, []), key=lambda item: item[1] !=
                                         '.tmp/literature/' + paper.file_name()))
                # A repeat lookup can leave only metadata, a corrupt receipt, or unreadable text. Each is
                # one failed candidate, not a reason to hide an already-staged precedent of the same paper.
                candidates.extend((root, 'inputs/precedent/' + file_name(item, index), item.get('text_sha256'))
                    for index, item in enumerate(precedents, 1)
                    if paper.key in _paper_keys(item) and item.get('access_level') in {'full_text', 'abstract'})
        except (ValueError, TypeError, KeyError, AttributeError):
            continue
        for root, path, digest in candidates:
            try:
                if not isinstance(digest, str) or not re.fullmatch('[a-f0-9]{64}', digest):
                    continue
                key = (str(root), path, digest)
                if key not in cache:
                    data = _source_bytes(root, path, min(MAX_SOURCE_BYTES, remaining))
                    remaining -= len(data)
                    if hashlib.sha256(data).hexdigest() != digest:
                        continue
                    cache[key] = data.decode('utf-8')
                texts[str(row.get('id') or '')] = cache[key]
                break
            except (OSError, ValueError, TypeError, AttributeError):
                continue
    return texts


def check_evidence(ledger: Mapping, source_texts: Mapping[str, str]) -> list[dict]:
    """Check explicit quotations and declared quantities against already-read evidence text.

    ``matched`` means literal presence, never scientific support. Missing text and nonliteral
    quantities remain unverified. Callers supply text keyed by evidence ID after checking provenance.
    """
    results = []
    for row in ledger.get("evidence") or []:
        if not isinstance(row, Mapping):
            continue
        quotes = [a or b for a, b in re.findall(r'"([^"\n]+)"|“([^”\n]+)”', str(row.get("observation") or ""))]
        values = [q.get("value") for q in row.get("quantities") or []
                  if isinstance(q, Mapping) and q.get("value") is not None]
        if not quotes and not values:
            continue
        ident = str(row.get("id") or "")
        text = source_texts.get(ident)
        problems = []
        state = "matched"
        if not isinstance(text, str) or not text.strip():
            state, problems = "unverified", ["source text unavailable for literal check"]
        else:
            source = Source(text)
            for quote in quotes:
                if not source.holds_quote(quote):
                    problems.append(f"quote not found in source: {quote}")
            unknown = False
            for value in values:
                literal = str(value).strip()
                quantity = Quantity.parse(literal) if _NUMBER.fullmatch(literal) else None
                if quantity is None:
                    unknown = True
                    problems.append(f"quantity requires reviewer interpretation: {literal}")
                elif not is_supported(quantity, source.quantities):
                    problems.append(f"quantity not found in source: {literal}")
            state = ("mismatch" if any("not found" in p for p in problems) else
                     "unverified" if unknown else "matched")
        results.append({"evidence_id": ident, "state": state, "problems": problems})
    return results


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
