"""Pure-string differential checks against the pre-optimization scanner."""

import random

import pytest

from labhq import policy


def _reference_token_candidates(token):
    """Split one lexical token without retrying at every delimiter in a run."""
    # Frozen 2642fa16 implementation: an independent oracle for bounded searches.
    chunk_start = 0
    length = len(token)
    while chunk_start < length:
        while chunk_start < length and token[chunk_start] in policy._CHUNK_BREAKS:
            chunk_start += 1
        if chunk_start >= length:
            break
        chunk_end = chunk_start
        while chunk_end < length and token[chunk_end] not in policy._CHUNK_BREAKS:
            chunk_end += 1
        left, right = chunk_start, chunk_end
        while left < right and token[left] in "[]{}":
            left += 1
        while right > left and token[right - 1] in "[]{}":
            right -= 1
        part_start = left
        while part_start < right:
            while part_start < right and token[part_start] in "[]{}":
                part_start += 1
            if part_start >= right:
                break
            uri = policy._URI_PREFIX.match(token, part_start, right)
            if uri and len(uri.group(1)) > 1:
                if uri.group(1).casefold() == "file":
                    path = policy._file_uri_path(token[part_start:right])
                    if path:
                        yield path
                break
            colon = part_start
            while colon < right:
                if token[colon] == ":" and not (
                        colon == part_start + 1 and token[part_start].isalpha()):
                    break
                colon += 1
            if colon == right:
                yield token[part_start:right]
                break
            if colon > part_start:
                yield token[part_start:colon]
            part_start = colon + 1
            while part_start < right and token[part_start] == ":":
                part_start += 1
        chunk_start = chunk_end + 1


@pytest.mark.parametrize("token,expected", [
    ("", ()),
    ("=<>(),::::", ()),
    ("[[/restricted/a]]", ("/restricted/a",)),
    ("C:/restricted/file:stream", ("C:/restricted/file", "stream")),
    ("C:relative", ("C:relative",)),
    ("/restricted::C:/restricted", ("/restricted", "C:/restricted")),
    ("x=/restricted/a,(D:\\restricted\\b):tail",
     ("x", "/restricted/a", "D:\\restricted\\b", "tail")),
    ("file:///C:/restricted/a%20b", ("C:/restricted/a b",)),
    ("file://server/share/private", ("//server/share/private",)),
    ("https://example.test/restricted", ()),
    ("file:///restricted/a,/neighbor", ("/restricted/a", "/neighbor")),
    ("/restricted:::,C:/neighbor:::/restricted",
     ("/restricted", "C:/neighbor", "/restricted")),
])
def test_token_search_preserves_explicit_path_boundaries(token, expected):
    assert tuple(policy._token_candidates(token)) == expected
    assert tuple(_reference_token_candidates(token)) == expected


@pytest.mark.parametrize("text,expected", [
    ("/restricted/a", "/restricted"),
    ("/restricted-neighbor/a", None),
    ("/restricted/../outside", None),
    ("/outside/../restricted/a", "/restricted"),
    ("C:/restricted/a:stream", "C:/restricted"),
    ("C:relative", "C:/restricted"),
    ("D:relative", None),
    (r"\\server\share\private\a", "//server/share/private"),
    (r"\\server\share\private-neighbor\a", None),
    ("file:///restricted/a", "/restricted"),
    ("https://example.test/restricted/a", None),
    ('cat "/restricted/a b"', "/restricted"),
    (r"cat /restricted/a\ b", "/restricted"),
    ("cat=/restricted/a:::,C:/outside", "/restricted"),
    ('cat "/restricted space/a"', "/restricted space"),
    (r"cat /restricted\ space/a", "/restricted space"),
    ('"/restricted spaces/a"', None),
    # Unquoted whitespace ends a token; preserve the existing conservative hit.
    ("/restricted spaces/a", "/restricted"),
])
def test_bounded_search_keeps_lexical_security_decisions(text, expected):
    zones = ("/restricted", "C:/restricted", "//server/share/private", "/restricted space")
    assert policy.touches({"command": text}, zones) == expected
    assert policy.mentions_zone(text, zones) is (expected is not None)


def _adversarial_texts():
    atoms = (
        "", "/restricted/a", "/restricted-neighbor/a", "/restricted/../neighbor",
        "C:/restricted/a", "C:relative", "C:\\restricted\\a:stream",
        "\\\\server\\share\\private", "file:///restricted/a%20b",
        "file://server/share/private", "https://example.test/restricted/a",
        "'[[]] /restricted/a'", '"C:/restricted/a b"', "/restricted/a\\ b",
        "검토/자료", "[file:///restricted/a]", "A:foo", "/:/\\::/restricted",
    )
    delimiters = ("", ":", "::::", "=", "<>,()", " ", "\n", "\\\"")
    for left in atoms:
        for right in atoms:
            for delimiter in delimiters:
                yield left + delimiter + right
    rng = random.Random(788)
    alphabet = "abZ09가é:/\\=<>(),[]{}'\"`|;& \t\n%._-"
    for _ in range(512):
        yield "".join(rng.choice(alphabet) for _ in range(rng.randrange(128)))
    # Run ends meet chunk/trim bounds, not just the end of the entire token.
    for run in (":" * 4096, "/" * 4096, "\\" * 4096):
        yield f"/restricted{run},C:/restricted{run}]]"


def _outcome(call):
    try:
        return ("value", call())
    except ValueError as error:
        # Preserve malformed URI rejection as well as successful candidates.
        return ("error", type(error), str(error))


def test_bounded_search_matches_original_candidates_metadata_and_zone_decisions(monkeypatch):
    current = policy._token_candidates
    zones = ("/restricted", "C:/restricted", "//server/share/private")
    for text in _adversarial_texts():
        actual_tokens = _outcome(lambda: tuple(current(text)))
        expected_tokens = _outcome(lambda: tuple(_reference_token_candidates(text)))
        assert actual_tokens == expected_tokens, repr(text)

        def decisions():
            return (
                policy._scan_path_text(text),
                policy.mentions_zone(text, zones),
                policy.touches({"command": text}, zones),
            )

        actual = _outcome(decisions)
        with monkeypatch.context() as old:
            old.setattr(policy, "_token_candidates", _reference_token_candidates)
            expected = _outcome(decisions)
        assert actual == expected, repr(text)
