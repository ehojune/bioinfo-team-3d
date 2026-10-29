"""Evidence classification for the experimental Claude Bash boundary.

Mock observations only test classification; they are not sandbox evidence.
The real launcher remains fail-closed until its trusted contract is available.
"""

from dataclasses import dataclass
from enum import Enum

import pytest


class Verdict(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNVERIFIED = "UNVERIFIED"


@dataclass(frozen=True)
class Observation:
    launched: bool = False
    command_ran: bool = False
    positive_control: bool = False
    observed: bool = False
    expected: bool = False
    timed_out: bool = False


def verdict(e: Observation) -> Verdict:
    """Classify one probe; expected denial is meaningful only with controls."""
    if e.launched and e.command_ran and e.observed and not e.expected:
        return Verdict.FAIL
    if e.timed_out or not all((e.launched, e.command_ran, e.positive_control, e.observed)):
        return Verdict.UNVERIFIED
    return Verdict.PASS if e.expected else Verdict.FAIL


@pytest.mark.parametrize("missing", ["launched", "command_ran", "positive_control", "observed"])
def test_missing_evidence_is_not_a_denial_pass(missing):
    evidence = dict(launched=True, command_ran=True, positive_control=True, observed=True, expected=True)
    evidence[missing] = False
    assert verdict(Observation(**evidence)) is Verdict.UNVERIFIED


def test_timeout_is_unverified_even_with_other_evidence():
    assert verdict(Observation(True, True, True, True, True, True)) is Verdict.UNVERIFIED


def test_observed_boundary_violation_fails():
    assert verdict(Observation(True, True, True, True, False)) is Verdict.FAIL


def test_observed_violation_fails_without_positive_control_or_after_timeout():
    assert verdict(Observation(True, True, False, True, False, True)) is Verdict.FAIL


def test_observed_expected_result_with_control_passes():
    assert verdict(Observation(True, True, True, True, True)) is Verdict.PASS


def test_real_launcher_contract_is_launch_blocked():
    launcher = pytest.importorskip("labhq.runner.claude_sandbox", reason="W1 launcher not merged; real boundary UNVERIFIED")
    try:
        launcher.build_launch()
    except launcher.SandboxUnavailable as exc:
        pytest.skip(f"launch blocked: {exc}; real boundary UNVERIFIED")
    pytest.fail("Launcher contract changed: implement and review real probe execution before granting a verdict")
