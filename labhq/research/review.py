"""Claim-level review of one research result: REVIEW v2, the start of R13 (#90, #118).

The ledger checks references and enums and the verifier checks that identifiers exist. Neither reads an
observation sentence, so a search that found nothing, written as ``observed`` with "0 hits", still links as
``contradicts``. Those R05/R07 judgments stay with a reviewer who did not write the result, in a fixed
shape: every claim gets every check, a defect states its severity, using absence as evidence is always
major, and a review that leaves a major defect cannot accept.

Not on the execution path yet: research steps stay off until #90 PR 3. The general REVIEW_SCHEMA in
``orchestrator/cso.py`` is unchanged.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .contract import ResearchResult


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CheckResult(StrictModel):
    outcome: Literal["pass", "defect", "not_applicable"]
    severity: Literal["major", "minor"] | None = None  # defects only
    evidence_ids: list[str] = []  # the rows the judgment is about
    reason: str = ""

    @model_validator(mode="after")
    def outcome_shape(self) -> "CheckResult":
        if self.outcome == "defect" and self.severity is None:
            raise ValueError("a defect needs a severity")
        if self.outcome != "defect" and self.severity is not None:
            raise ValueError("only a defect has a severity")
        if self.outcome != "pass" and not self.reason.strip():
            raise ValueError(f"{self.outcome} needs a reason")
        return self


class ClaimChecks(StrictModel):
    # Does each linked source say what the link claims? The verifier only resolves identifiers.
    citation_support: CheckResult
    # R04/R05: a search with no hits supports or contradicts the claim, however its status is written.
    absence_as_evidence: CheckResult
    # R05: direct or indirect as declared, for the question the claim asks.
    directness: CheckResult
    # R05: re-citation or shared data counted as independent support.
    independence: CheckResult
    # R07: compared quantities measured alike (unit, conditions, denominator, method).
    comparability: CheckResult


REVIEW_CHECKS = tuple(ClaimChecks.model_fields)
ALWAYS_MAJOR = frozenset({"absence_as_evidence"})  # absence is not evidence (R04)
EVIDENCE_CHECKS = ("citation_support", "absence_as_evidence", "directness", "independence")


class ClaimReview(StrictModel):
    claim: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_.:-]{0,79}@[1-9]\d*$")  # "<id>@<revision>"
    checks: ClaimChecks

    @model_validator(mode="after")
    def always_major(self) -> "ClaimReview":
        for name in ALWAYS_MAJOR:
            check = getattr(self.checks, name)
            if check.outcome == "defect" and check.severity != "major":
                raise ValueError(f"{self.claim}: {name} is always a major defect; absence is not evidence")
        return self


class ResearchReview(StrictModel):
    schema_version: Literal[2]
    plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    step_id: str = Field(min_length=1)
    reviewer: str = Field(min_length=1)  # agent id; must not be the result's author
    verdict: Literal["accept", "revise"]
    claims: list[ClaimReview]
    notes: str = ""

    @model_validator(mode="after")
    def accept_needs_no_major_defect(self) -> "ResearchReview":
        keys = [item.claim for item in self.claims]
        duplicates = sorted({key for key in keys if keys.count(key) > 1})
        if duplicates:
            raise ValueError(f"review lists claims {duplicates} more than once")
        if self.verdict == "accept" and self.major_defects:
            raise ValueError(f"review cannot accept while major defects remain: {self.major_defects}")
        return self

    @property
    def major_defects(self) -> list[str]:
        found = []
        for item in self.claims:
            for name in REVIEW_CHECKS:
                check = getattr(item.checks, name)
                if check.outcome == "defect" and check.severity == "major":
                    rows = f" ({', '.join(check.evidence_ids)})" if check.evidence_ids else ""
                    found.append(f"{item.claim} {name}{rows}: {check.reason}")
        return found


RESEARCH_REVIEW_SCHEMA: dict[str, Any] = ResearchReview.model_json_schema()


def validate_research_review(value: Any, *, result: ResearchResult, author: str) -> ResearchReview:
    """Parse a review and bind it to the result it reviews: same plan and step, every claim, known rows.

    ``author`` is the agent that wrote the result (the task's agent id). It is required: ``ResearchResult``
    does not record its author, and a review whose independence was never checked must not pass (#169, #188).

    A check may be ``not_applicable`` only where it cannot apply: the evidence checks when the claim links
    no countable evidence, comparability when the claim compares no quantities.
    """
    if not isinstance(author, str) or not author.strip():
        raise ValueError("review validation needs the result's author to check that the reviewer is another agent")
    review = ResearchReview.model_validate(value)
    errors: list[str] = []
    if (review.plan_sha256, review.step_id) != (result.plan_sha256, result.step_id):
        errors.append(f"review of {review.step_id} reviews a different result than {result.step_id}")
    if review.reviewer.strip().casefold() == author.strip().casefold():
        errors.append(f"reviewer {review.reviewer} wrote the result; the review needs another agent")
    expected = {claim.key: claim for claim in result.claims}
    reviewed = {item.claim: item for item in review.claims}
    missing = [key for key in expected if key not in reviewed]
    if missing:
        errors.append(f"review does not review claims {missing}")
    unknown = sorted(set(reviewed) - set(expected))
    if unknown:
        errors.append(f"review covers claims {unknown} that the result does not report")
    by_id = {row.id: row for row in result.evidence}
    for key, item in reviewed.items():
        for name in REVIEW_CHECKS:
            for eid in getattr(item.checks, name).evidence_ids:
                if eid not in by_id:
                    errors.append(f"{key} {name} cites unknown evidence {eid}")
        claim = expected.get(key)
        if claim is None:
            continue
        countable = any(link.claim_id == claim.id and link.relation != "context"
                        and link.evidence_id in by_id and by_id[link.evidence_id].countable
                        for link in result.links)
        if countable:
            errors += [f"{key} links countable evidence; {name} must be judged, not not_applicable"
                       for name in EVIDENCE_CHECKS if getattr(item.checks, name).outcome == "not_applicable"]
        if claim.comparisons and item.checks.comparability.outcome == "not_applicable":
            errors.append(f"{key} compares quantities; comparability must be judged, not not_applicable")
    if errors:
        raise ValueError("; ".join(errors))
    return review
