"""Whether a staff model could read the PI's LLM-wiki during a request (#1037).

A wiki note is the PI's own text. A request whose staff could read it must not publish its outputs to a public place
until a later stage can tell what was copied, so a publication is allowed only when the request proves it was never
exposed. The record lives on the request (`wiki_exposure`) and is written by the gateway:

* ``none``     the request was created by a gateway that records this, and no task was dispatched to a runner that
               could attach the wiki tool to its staff;
* ``possible`` a task went to a runner that offers (or may offer) the wiki tool to that agent. It never goes back to
               ``none``;
* missing or any other value is **unknown**: a request from before this record, or one whose history cannot be read.

Unknown and possible both close publication; only ``none`` opens it. Independent proof that no note was copied, and
masking of what was, are later stages."""
from __future__ import annotations

from typing import Any, Mapping

KEY = "wiki_exposure"
NONE = "none"
POSSIBLE = "possible"
UNKNOWN = "unknown"


def exposure(request: Mapping[str, Any] | None) -> str:
    """``none``, ``possible`` or ``unknown`` for a request record; anything not exactly written by the gateway is unknown."""
    value = (request or {}).get(KEY)
    return value if value in (NONE, POSSIBLE) else UNKNOWN


def after_wiki_dispatch(request: Mapping[str, Any] | None) -> str | None:
    """What to store when a task that may reach the wiki is dispatched: ``possible``, or None (write nothing) when the
    history is already unknown, so an unknown request never turns into one that looks recorded."""
    return None if exposure(request) == UNKNOWN else POSSIBLE


def cp2_warning(request: Mapping[str, Any] | None) -> str | None:
    """The line the CP2 evidence card and the report's limits carry when the request is not a recorded ``none``. CP2 is
    the PI's own review, not a publication, so it is not refused: the PI is told what the card cannot tell, and the
    request is not published (`publication_blocker`)."""
    state = exposure(request)
    if state == NONE:
        return None
    reason = ("이 요청의 직원이 PI의 LLM-wiki 도구를 쓸 수 있었습니다" if state == POSSIBLE
              else "이 요청의 wiki 노출 이력을 확인할 수 없습니다")
    return (f"wiki 노출: {reason}. wiki 요약은 원문을 직접 읽은 근거가 아닌데 이 단계는 그것을 가려내지 못하므로 "
            "근거 승인을 원문 확인으로 읽지 마세요. 이 요청의 결과는 GitHub에 게시되지 않습니다.")


def publication_blocker(request: Mapping[str, Any] | None) -> str | None:
    """Why this request must not publish to a public place, or None when it may. Only a recorded ``none`` may."""
    state = exposure(request)
    if state == NONE:
        return None
    if state == POSSIBLE:
        return "이 요청의 직원이 wiki 도구를 쓸 수 있었으므로 공개 게시를 막았습니다"
    return "이 요청의 wiki 노출 이력을 확인할 수 없어 공개 게시를 막았습니다"
