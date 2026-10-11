"""파견직 채용물 고정과 외부 전송 선언 (#424).

A contract hire records the commit and licenses it was built from and every external service its tools send data
to. labhq hashes the delivered skill and MCP folders itself, warns when a rehire finds them changed, and keeps staff
that send data out off any task whose inputs touch a restricted data zone: at planning (`validate_steps` guard) and
again on the runner before the engine starts. Inputs are judged lexically, like the approval gate's `touches`.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable

from ..models import SOURCE_COMMIT, AgentSpec, ExternalService, Task

if TYPE_CHECKING:
    from ..settings import Settings

log = logging.getLogger(__name__)


def source_commit(value: Any) -> str | None:
    """A full 40-hex commit as offered, else None (a short or branch name pins nothing)."""
    text = str(value or "").strip().lower()
    return text if SOURCE_COMMIT.fullmatch(text) else None


def folder_sha256(path: str | Path | None) -> str | None:
    """plugin_provenance's content-and-mode hash of a delivered skill or MCP folder; a file hashes its bytes."""
    from ..adapters.claude_code import _file_sha256, plugin_provenance

    if not path:
        return None
    target = Path(path)
    try:
        if target.is_file():
            return _file_sha256(target).hex()
        if not target.is_dir():
            return None
        return plugin_provenance(target.name, target, every_file=True)["sha256"]
    except (OSError, ValueError) as exc:  # unreadable file or a link out of the folder: no hash, not a crash
        log.warning("cannot hash %s: %s", target.name, exc)
        return None


def provenance_changes(spec: AgentSpec) -> list[str]:
    """Which recorded hashes ("skill", "mcp") the folders no longer match; a folder that is gone counts."""
    c = spec.contract
    if c is None:
        return []
    # The target the hash was taken from; a contract saved before mcp_path existed falls back to the server cwd.
    mcp_target = c.mcp_path or next((m.cwd for m in spec.mcp if m.cwd), None)
    changed = []
    for name, recorded, path in (("skill", c.skill_sha256, c.skill_dir), ("mcp", c.mcp_sha256, mcp_target)):
        if recorded and path and folder_sha256(path) != recorded:
            changed.append(name)
    return changed


def service_host(raw: Any) -> tuple[str, str | None]:
    """A declared host as a bare host[:port], and a note when something else had to be dropped.

    The note never repeats the raw value: a URL may carry user:password@, a key in its query or a token in its path.
    A declaration is never dropped (that would open restricted steps), so an unreadable one becomes unknown.invalid."""
    from urllib.parse import urlsplit

    text = str(raw or "").strip()
    try:
        return ExternalService(host=text, what_is_sent="-").host, None
    except ValueError:
        pass
    try:
        parts = urlsplit(text if "://" in text else "//" + text)
        host = parts.hostname or ""
        if parts.port:
            host += f":{parts.port}"
        host = ExternalService(host=host, what_is_sent="-").host
    except ValueError:
        host = "unknown.invalid"
    return host, (f"external_services host was not a bare hostname; stored {host} only "
                  "(scheme, credentials, path, query and fragment are never kept)")


def not_yet_active(agent: AgentSpec | dict) -> bool:
    """A contract hire the PI has not activated (probation): on the roster as 수습, but given no work."""
    if isinstance(agent, AgentSpec):
        return agent.contract is not None and agent.contract.status != "active"
    return agent.get("employment") == "contract" and agent.get("contract_status") not in (None, "active")


def probation_refusal(agent_id: str) -> str:
    return (f"수습 중인 파견직 {agent_id}은 PI가 `labhq contract activate {agent_id}`로 승인하기 전에는 일을 맡을 수 "
            "없습니다 (#424)")


def external_hosts(agent: AgentSpec | dict) -> list[str]:
    """Hosts a contract hire declared its tools send data to; [] for core staff or an older roster entry."""
    if isinstance(agent, AgentSpec):
        return [s.host for s in agent.contract.external_services] if agent.contract else []
    services = (agent.get("contract_provenance") or {}).get("external_services") or []
    return [str(s.get("host")) for s in services if isinstance(s, dict) and s.get("host")]


def _zones(settings: "Settings") -> list[str]:
    return [z.path for z in settings.policy.data_zones if z.level == "restricted"]


def touches_restricted(texts: Iterable[Any], dirs: Iterable[Any], settings: "Settings") -> bool:
    """Whether a folder overlaps a restricted zone or any text names a path inside one."""
    from ..intake import overlaps_restricted
    from ..policy import touches

    zones = _zones(settings)
    if not zones:
        return False
    if any(d and overlaps_restricted(str(d), settings) for d in dirs):
        return True
    return touches([str(t) for t in texts if t], zones) is not None


def _refusal(hosts: list[str]) -> str:
    return (f"외부 전송 파견직({', '.join(hosts)})은 통제 데이터 구역을 입력으로 쓰는 일을 맡을 수 없습니다. "
            "정규직에게 맡기세요 (#424)")


def consult_refusal(hosts: list[str]) -> str:
    return (f"외부 전송 파견직({', '.join(hosts)})에게는 통제 데이터 구역을 입력으로 쓰는 작업에서 질문할 수 없습니다. "
            "정규직이나 CSO에게 물으세요 (#424)")


def restricted_task(task: Task, settings: "Settings") -> bool:
    """Whether a task's inputs touch a restricted zone: the planner's verdict (a step's ancestors, or the asking
    task of a consult), its own text, or a folder it opens. The runner puts this on the task's ask token too."""
    meta = task.meta
    return bool(meta.get("restricted_inputs")) or touches_restricted(
        [task.prompt, task.context, meta.get("request"), meta.get("instruction")],
        [*(meta.get("project_dirs") or []), *(meta.get("reference_dirs") or []), meta.get("source_workdir")],
        settings)


def external_service_refusal(agent: AgentSpec, task: Task, settings: "Settings") -> str | None:
    """The runner's check before the engine starts. No zone path in the text: errors reach reports."""
    hosts = external_hosts(agent)
    if not hosts or not restricted_task(task, settings):
        return None
    return consult_refusal(hosts) if task.meta.get("kind") == "consult" else _refusal(hosts)


def restricted_input_steps(steps: list[dict], texts: list[Any], dirs: list[Any], settings: "Settings") -> set[str]:
    """Steps whose inputs touch a restricted zone: the request, its folders, the step or any step it waits on.

    An upstream step's outputs are linked into every step downstream of it, so its instruction counts as input.
    Unknown dependency ids are skipped (a draft plan may hold them; validate_steps rejects them separately)."""
    if not _zones(settings):
        return set()
    if touches_restricted(texts, dirs, settings):
        return {str(s.get("id")) for s in steps if isinstance(s, dict)}
    by_id = {str(s.get("id")): s for s in steps if isinstance(s, dict)}
    direct = {sid for sid, s in by_id.items() if touches_restricted([s.get("instruction")], [], settings)}
    found = set()
    for sid in by_id:
        seen, pending = set(), [sid]
        while pending:
            current = pending.pop()
            if current in seen or current not in by_id:
                continue
            seen.add(current)
            pending.extend(str(d) for d in by_id[current].get("depends_on") or [])
        if seen & direct:
            found.add(sid)
    return found


def external_contract_conflicts(steps: list[dict], roster: list[dict], texts: list[Any], dirs: list[Any],
                                settings: "Settings") -> list[str]:
    """`step (agent)` for each step given to staff that send data out while its inputs touch a restricted zone."""
    sending = {a.get("id") for a in roster if isinstance(a, dict) and external_hosts(a)}
    if not sending or not _zones(settings):
        return []
    restricted = restricted_input_steps(steps, texts, dirs, settings)
    return [f"{s.get('id')} ({s.get('agent_id')})" for s in steps
            if isinstance(s, dict) and s.get("agent_id") in sending and str(s.get("id")) in restricted]
