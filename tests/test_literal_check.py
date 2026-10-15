"""Literal-check tests adapted from OKF Loremaster and modified for labhq.

Original: https://github.com/PFreda-Lab/okf-loremaster
Commit: ead3819
File: tests/test_verification.py
License: Apache-2.0; see labhq/evidence/NOTICE.md and LICENSE-APACHE-2.0.txt.

Modified for labhq: removed schema-level verification cases and added Korean Unicode
normalization and Python 3.10 grammar checks.
"""

import ast
import inspect

import labhq.evidence.literal_check as literal_check
from labhq.evidence.literal_check import (
    EN_DASH,
    MAX_LOCATED_QUOTE_WORDS,
    MIDDLE_DOT,
    MINUS,
    Quantity,
    Source,
    normalize,
    quantities_in,
)


def test_journal_number_shapes_are_read_as_numbers() -> None:
    text = f"OR 1{MIDDLE_DOT}82, beta {MINUS}0.44, n = 12,405"
    assert [quantity.value for quantity in quantities_in(text)] == [1.82, -0.44, 12405]


def test_interval_dash_is_not_a_negative_sign() -> None:
    assert [q.value for q in quantities_in("CI 1.21-2.74")] == [1.21, 2.74]
    assert [q.value for q in quantities_in(f"CI 1.21{EN_DASH}2.74")] == [1.21, 2.74]
    assert [q.value for q in quantities_in("CI 1.21 - 2.74")] == [1.21, 2.74]
    assert [q.value for q in quantities_in("at 30-day follow-up, 12 events")] == [30, 12]


def test_claim_may_be_less_precise_than_source_but_not_more_precise() -> None:
    assert Source("the adjusted odds ratio was 1.84").holds(1.8)
    assert Source("the adjusted odds ratio was 1.84").holds(1.84)
    assert not Source("the adjusted odds ratio was 1.8").holds(1.84)
    assert not Source("the adjusted odds ratio was 1.84").holds(1.9)


def test_bare_integer_does_not_support_claimed_decimal() -> None:
    source = Source("Table 4 lists the 2019 cohort of 244 adults.")
    assert not source.holds(4.44)
    assert source.holds(244)


def test_precision_survives_tiny_effect() -> None:
    assert Quantity.of(0.00003).decimals == 5


def test_quote_matching_ignores_case_punctuation_and_reflowed_whitespace() -> None:
    source = Source("The association held\nafter adjustment (P = 0.03).")
    assert source.holds_quote("the association held after adjustment, p=0.03")
    assert not source.holds_quote("The association did not hold after adjustment")
    assert normalize("A--B  c!") == "a b c"


def test_normalize_preserves_korean_quote_text() -> None:
    source = Source("치료 효과는 유의했다 (P = 0.03).")
    assert normalize("한글—인용문!") == "한글 인용문"
    assert source.holds_quote("치료 효과는 유의했다")
    assert not source.holds_quote("대조군 효과는 유의했다")


def test_locator_returns_original_sentence_without_heading() -> None:
    sentence = "In adjusted models the association was 1.82 (95% CI 1.21-2.74)."
    source = Source(f"## RESULTS\n{sentence}\nTable 2 follows.")
    assert source.sentence_for("In adjusted models") == sentence
    assert source.sentence_for("In unadjusted models") == ""


def test_flattened_table_is_limited_to_word_window() -> None:
    table = "Characteristic " + " ".join(f"row {n} value {n}.0" for n in range(200))
    quote = Source(table).sentence_for("row 40 value")
    assert len(quote.split()) == MAX_LOCATED_QUOTE_WORDS
    assert quote.startswith("row 40 value 40.0 row 41")
    assert quote in table
    assert Source(quote).holds(40.0)
    assert not Source(quote).holds(180.0)


def test_long_prose_sentence_below_cap_is_left_whole() -> None:
    sentence = (
        "In models adjusted for "
        + ", ".join(f"covariate {n}" for n in range(20))
        + ", the association was 1.82 (95% CI 1.21-2.74)."
    )
    assert len(sentence.split()) < MAX_LOCATED_QUOTE_WORDS
    assert Source(sentence).sentence_for("In models adjusted") == sentence


def test_module_parses_with_python_3_10_grammar() -> None:
    source = inspect.getsource(literal_check)
    ast.parse(source, feature_version=(3, 10))
