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


def _task_state(task: Mapping[str, Any]) -> str:
    result = task.get("result")
    if not isinstance(result, dict) or not task.get("completed"):
        return "running" if task.get("accepted") else "dispatched"
    if result.get("pending_jobs") or result.get("pending_asks"):
        return "waiting"
    return "done" if result.get("ok") else "failed"


def build_view(snap: Mapping[str, Any]) -> ObjectView:
    """Project one request of a shadow snapshot onto objects and links."""
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
    for tid, task in sorted(tasks.items()):
        view.add("Task", f"task:{tid}", kind=task.get("kind"), state=_task_state(task))
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
            job = view.add("Job", f"job:{tid}/{job_id}", state=finished.get(job_id, "pending"))
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
    return view


def summarize(view: ObjectView) -> dict[str, Any]:
    """Counts only: objects and links by type, unresolved links by type."""
    links: dict[str, int] = {rel: 0 for rel in LINK_TYPES}
    for rel, _, _ in view.links:
        links[rel] += 1
    unresolved: dict[str, int] = {}
    for rel, _ in view.unresolved:
        unresolved[rel] = unresolved.get(rel, 0) + 1
    pending = sum(1 for a in view.objects["Approval"].values() if a.get("state") == "pending")
    return {"objects": {t: len(view.objects[t]) for t in OBJECT_TYPES}, "links": links,
            "link_total": len(view.links), "unresolved": len(view.unresolved),
            "unresolved_by": dict(sorted(unresolved.items())), "pending_approvals": pending}
