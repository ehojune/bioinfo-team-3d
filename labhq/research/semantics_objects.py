"""Read-only object and link view of one request (#150 B1), shaped like an operational ontology.

Objects are the lab's operating things (staff, requests, steps, tasks, jobs, data assets, approvals,
artifacts) and links say how they connect. Both are projected from copies of gateway rows the shadow
worker hands in. There are no actions: nothing here approves, submits, dispatches or writes anything.
Only the counts of ``summarize`` leave this module; object ids stay in memory.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from ..evidence.claims import normalize_artifact_path
from ..vocab.declare import FIELDS, UNKNOWN, read, staff_declarations, unknown
from .contract import validate_research_result

OBJECT_TYPES = ("Staff", "Request", "Step", "Task", "Job", "DataAsset", "Approval", "Artifact")
# link type -> (source object type, allowed target types)
LINK_TYPES: dict[str, tuple[str, tuple[str, ...]]] = {
    "request_step": ("Request", ("Step",)),
    "step_depends_on": ("Step", ("Step",)),
    "step_staff": ("Step", ("Staff",)),           # the step is assigned to this employee
    "step_input": ("Step", ("Artifact", "DataAsset")),  # research input_refs
    "task_step": ("Task", ("Step",)),             # a run of the step (attempts, revisions, wake-ups)
    "task_request": ("Task", ("Request",)),       # briefing, plan, review, synthesis, direct
    "task_staff": ("Task", ("Staff",)),           # performed by
    "task_parent": ("Task", ("Task",)),           # wake-up or continuation of an earlier task
    "task_job": ("Task", ("Job",)),
    "task_artifact": ("Task", ("Artifact",)),     # reported output
    "approval_target": ("Approval", ("Request", "Task")),
    "request_asset": ("Request", ("DataAsset",)),  # reference pointers
}
_EXTERNAL = re.compile(r"([a-z][a-z0-9_]*):([^@/]+)@([^@/]+)")


def opaque(*parts: Any) -> str:
    """A stable id that does not carry the value it stands for (paths, reference values)."""
    return hashlib.sha256("\x1f".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:16]


@dataclass
class ObjectView:
    objects: dict[str, dict[str, dict[str, Any]]] = field(default_factory=lambda: {t: {} for t in OBJECT_TYPES})
    links: list[tuple[str, str, str]] = field(default_factory=list)
    unresolved: list[tuple[str, str]] = field(default_factory=list)

    def add(self, otype: str, oid: str, **props: Any) -> str:
        self.objects[otype].setdefault(oid, {}).update({k: v for k, v in props.items() if v is not None})
        return oid

    def type_of(self, oid: str) -> str | None:
        return next((t for t, objs in self.objects.items() if oid in objs), None)

    def link(self, rel: str, src: str, dst: str | None) -> None:
        source, targets = LINK_TYPES[rel]
        if self.type_of(src) != source:
            raise ValueError(f"link {rel} does not start at a {source}")
        if dst is None or self.type_of(dst) not in targets:
            self.unresolved.append((rel, src))
            return
        if (rel, src, dst) not in self.links:
            self.links.append((rel, src, dst))


def _task_state(task: Mapping[str, Any], woken: bool) -> str:
    result = task.get("result")
    if not isinstance(result, dict) or not task.get("completed"):
        return "running" if task.get("accepted") else "dispatched"
    if (result.get("pending_jobs") or result.get("pending_asks")) and not woken:
        return "waiting"
    return "done" if result.get("ok") else "failed"


def _type_props(fields: Mapping[str, Any], vocab: Any) -> dict[str, Any]:
    """Artifact type properties: the key (what judgments use) and, apart, its EDAM id or local/unknown."""
    props: dict[str, Any] = {}
    for name, short in (("data_type", "data"), ("format", "format")):
        field = fields.get(name) or unknown("not_declared")
        known = field.basis != "unknown"
        props[name] = field.value if known else UNKNOWN
        props[f"{short}_basis"] = field.basis
        props[f"{short}_edam"] = ((vocab.edam_id(field.value) if vocab else None) or "local") if known else "unknown"
    return props


def type_artifacts(view: ObjectView, snap: Mapping[str, Any], vocab: Any) -> ObjectView:
    """Give each Artifact its type properties, read by labhq.vocab.declare.read as the provenance model reads them,
    under the ``vocab`` the caller loaded (None: every declaration reads as unknown). Read-only attributes: no
    link, approval or action depends on them. A file several tasks reported with different types is unknown."""
    rid = snap["rid"]
    req = (snap.get("requests") or {}).get(rid) or {}
    plan = req.get("plan") if req.get("research_contract") and isinstance(req.get("plan"), dict) else None
    seen: dict[str, list[dict[str, Any]]] = {}
    for _, task in sorted((snap.get("tasks") or {}).items()):
        if task.get("request_id") != rid:
            continue
        result = task.get("result") if isinstance(task.get("result"), dict) else {}
        payload = task.get("payload") if isinstance(task.get("payload"), dict) else {}
        staff: dict[str, dict[str, str]] = {}
        if plan is not None and task.get("step_id") and result.get("structured"):
            try:  # the provenance model's own check, so both models read the same staff declarations
                refs = validate_research_result(result["structured"], plan=plan).artifact_refs
                staff = staff_declarations(refs, normalize_artifact_path)
            except (ValueError, TypeError):
                staff = {}
        typed = read(payload.get("meta"), result, vocab, normalize=normalize_artifact_path, staff=staff)
        for out in result.get("outputs") or []:
            path = normalize_artifact_path(str(out))
            art = f"artifact:{opaque(rid, result.get('workdir_id'), path)}"
            if art in view.objects["Artifact"]:
                seen.setdefault(art, []).append(_type_props(typed.get(path) or {}, vocab))
    for art, props in seen.items():
        same = all(p == props[0] for p in props)
        view.add("Artifact", art, **(props[0] if same else _type_props(
            {f: unknown("declaration_conflict") for f in FIELDS}, vocab)))
    return view


def build_view(snap: Mapping[str, Any], *, vocab: Any = None) -> ObjectView:
    """Project one request of a shadow snapshot onto objects and links (with ``vocab``, typed artifacts too)."""
    view = ObjectView()
    rid = snap["rid"]
    req = snap["requests"][rid]
    for aid, agent in sorted((snap.get("agents") or {}).items()):
        view.add("Staff", f"staff:{aid}", engine=agent.get("engine"), employment=agent.get("employment"))
    request = view.add("Request", f"request:{rid}", mode=req.get("mode"), status=req.get("status"),
                       lane=req.get("lane"))

    steps = ((req.get("plan") or {}).get("steps")) or []
    step_ids = {str(s.get("id")) for s in steps if isinstance(s, dict)}
    for step in steps:
        if isinstance(step, dict):
            view.add("Step", f"step:{rid}/{step.get('id')}")
    for step in steps:
        if not isinstance(step, dict):
            continue
        sid = f"step:{rid}/{step.get('id')}"
        view.link("request_step", request, sid)
        view.link("step_staff", sid, f"staff:{step.get('agent_id')}")
        for dep in step.get("depends_on") or []:
            view.link("step_depends_on", sid, f"step:{rid}/{dep}" if dep in step_ids else None)

    tasks = {tid: t for tid, t in (snap.get("tasks") or {}).items() if t.get("request_id") == rid}
    # The CSO dispatches a wake-up only after the parent's jobs finished and its asks were answered, and that row
    # stays after saving the step result clears the jobs_done checkpoint (#163). A wrap-up turn waits for nothing.
    woken = {t.get("parent_task") for t in tasks.values() if t.get("parent_task") and t.get("kind") != "wrap_up"}
    for tid, task in sorted(tasks.items()):
        view.add("Task", f"task:{tid}", kind=task.get("kind"), state=_task_state(task, tid in woken))
    jobs_done = snap.get("jobs_done") or {}
    for tid, task in sorted(tasks.items()):
        node = f"task:{tid}"
        result = task.get("result") if isinstance(task.get("result"), dict) else {}
        payload = task.get("payload") if isinstance(task.get("payload"), dict) else {}
        if task.get("step_id"):
            target = f"step:{rid}/{task['step_id']}"
            view.link("task_step", node, target if task["step_id"] in step_ids else None)
        else:
            view.link("task_request", node, request)
        view.link("task_staff", node, f"staff:{payload.get('agent_id')}")
        parent = task.get("parent_task")
        if parent:
            view.link("task_parent", node, f"task:{parent}" if parent in tasks else None)
        finished = {str(j.get("job_id")): j.get("state") for j in (jobs_done.get(tid) or {}).get("jobs") or []
                    if isinstance(j, dict) and j.get("job_id") is not None}
        for job_id in [*map(str, result.get("pending_jobs") or []), *finished]:
            state = finished[job_id] if job_id in finished else "finished" if tid in woken else "pending"
            job = view.add("Job", f"job:{tid}/{job_id}", state=state)  # finished: ended, final state not kept
            view.link("task_job", node, job)
        for out in result.get("outputs") or []:
            path = normalize_artifact_path(str(out))  # one spelling per file, as the provenance model reads it
            art = view.add("Artifact", f"artifact:{opaque(rid, result.get('workdir_id'), path)}")
            view.link("task_artifact", node, art)

    for ref in req.get("references") or []:
        if isinstance(ref, dict):
            asset = view.add("DataAsset", f"asset:{opaque(ref.get('kind'), ref.get('value'))}", kind=ref.get("kind"))
            view.link("request_asset", request, asset)
    for step in steps:
        if not isinstance(step, dict):
            continue
        sid = f"step:{rid}/{step.get('id')}"
        for ref in step.get("input_refs") or []:
            ref = str(ref)
            match = _EXTERNAL.fullmatch(ref)
            if match and match.group(1) not in ("step", "art"):
                view.link("step_input", sid, view.add("DataAsset", f"asset:{opaque('external', ref)}", kind="external"))
            elif ref.startswith("step:"):
                source, _, path = ref[len("step:"):].partition("/")
                runs = [t for t in tasks.values() if t.get("step_id") == source]
                workdirs = {(t.get("result") or {}).get("workdir_id") for t in runs}
                target = (f"artifact:{opaque(rid, next(iter(workdirs)), normalize_artifact_path(path))}"
                          if len(workdirs) == 1 else None)
                view.link("step_input", sid, target)
            else:
                view.link("step_input", sid, None)

    for approval in snap.get("approvals") or []:
        aid = view.add("Approval", f"approval:{approval.get('id')}", kind=approval.get("kind"),
                       state=approval.get("state"))
        if approval.get("task_id"):
            view.link("approval_target", aid, f"task:{approval['task_id']}" if approval["task_id"] in tasks else None)
        else:
            view.link("approval_target", aid, request if approval.get("request_id") == rid else None)
    return type_artifacts(view, snap, vocab) if vocab is not None else view


def summarize(view: ObjectView) -> dict[str, Any]:
    """Counts only: objects and links by type, unresolved links by type, pending approvals and jobs."""
    links: dict[str, int] = {rel: 0 for rel in LINK_TYPES}
    for rel, _, _ in view.links:
        links[rel] += 1
    unresolved: dict[str, int] = {}
    for rel, _ in view.unresolved:
        unresolved[rel] = unresolved.get(rel, 0) + 1
    pending = sum(1 for a in view.objects["Approval"].values() if a.get("state") == "pending")
    types = {"data_type": {}, "format": {}}  # basis counts only: no key or id leaves this module
    for art in view.objects["Artifact"].values():
        for name, short in (("data_type", "data"), ("format", "format")):
            basis = art.get(f"{short}_basis", "unknown")
            types[name][basis] = types[name].get(basis, 0) + 1
    return {"objects": {t: len(view.objects[t]) for t in OBJECT_TYPES}, "links": links,
            "artifact_types": {k: dict(sorted(v.items())) for k, v in types.items()},
            "link_total": len(view.links), "unresolved": len(view.unresolved),
            "unresolved_by": dict(sorted(unresolved.items())), "pending_approvals": pending,
            "pending_jobs": sum(1 for j in view.objects["Job"].values() if j.get("state") == "pending")}
