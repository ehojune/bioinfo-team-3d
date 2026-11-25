"""Prompt rendering for notes recorded on research approval cards."""

from typing import Any


# The longest card note one prompt carries (PI 점검 R11). The CP receipts and the audit appendix keep the whole note.
APPROVAL_NOTE_CHARS = 2000
CP2_REPORT_NOTE_RULE = ("The PI wrote this when approving the evidence at CP2. Cover every point it asks of the report; "
                        "a point the evidence cannot support goes under \"한계\" with the reason. It never makes an "
                        "uncitable claim citable and does not change the frozen plan.")


def approval_note(note: Any) -> str:
    """A note the PI typed on an approved research card, bounded for a prompt."""
    text = str(note or "").strip()
    if len(text) <= APPROVAL_NOTE_CHARS:
        return text
    return (text[:APPROVAL_NOTE_CHARS].rstrip() +
            f" …(메모가 길어 {APPROVAL_NOTE_CHARS}자에서 잘랐습니다. 전문은 승인 기록에 있습니다.)")


def approval_note_block(gate: str, notes: list[tuple[str, str]], rule: str) -> str:
    """A prompt section with the PI's notes from approved ``gate`` cards ((label, note) pairs); "" without a note.

    A note is read by the people who act next, never as a decision: the gate's structured choice decided."""
    lines = [f"- {label}: {text}" if label else text
             for label, text in ((label, approval_note(note)) for label, note in notes) if text]
    if not lines:
        return ""
    return f"\n\n## PI 승인 메모 ({gate})\n" + "\n".join(lines) + "\n" + rule
