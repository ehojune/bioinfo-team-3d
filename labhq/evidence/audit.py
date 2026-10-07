"""`labhq verify <request>` and the per-request audit bundle (#58 ⑥).

The gateway's record of a request names the files each step produced and the sha256 labhq recorded for them.
On the runner's PC this module hashes those files again with the runner's own outputs walker (reads stop at
``runner.output_hash_max_bytes``, no link or junction is followed, folders are read through held handles,
restricted zones are left out), runs the claim-anchor check of a research report again, and lists the outputs a
staff member wrote without reporting them. The bundle carries the records, never the output files.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import os
import re
from collections import Counter
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from .. import __version__
from .report_check import check_report

OK, MISMATCH, MISSING, UNREADABLE, UNCHECKED, UNRECORDED = (
    "ok", "mismatch", "missing", "unreadable", "unchecked", "unrecorded")
STATUS_ORDER = (OK, MISMATCH, MISSING, UNREADABLE, UNCHECKED, UNRECORDED)
STATUS_KO = {OK: "일치", MISMATCH: "불일치", MISSING: "없음", UNREADABLE: "읽지 못함", UNCHECKED: "확인 못함",
             UNRECORDED: "기록된 해시 없음"}
PROBLEM_STATUSES = frozenset({MISMATCH, MISSING, UNREADABLE, UNCHECKED})
BUNDLE_FILES = ("README.md", "report.md", "report_appendix.md", "claims.json", "artifacts.json")
NO_FILES_LINE = "산출 파일 자체는 넣지 않았습니다(크기와 데이터 경계). 원본은 `artifacts.json`의 sha256으로 대조합니다."
_SAFE_REQUEST_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")


def _is_plain_dir(path: Path) -> bool:
    from ..adapters.owned import is_link

    try:
        return not is_link(path) and path.is_dir()
    except OSError:
        return False


def _lexically_inside(path: str, root: Path) -> bool:
    """``path`` is a local absolute path under ``root`` by text alone: no UNC, device or ``\\\\?\\`` form, no ``..``."""
    if path.startswith(("\\\\", "//")) or ".." in re.split(r"[\\/]", path) or not os.path.isabs(path):
        return False
    text = os.path.normcase(os.path.normpath(path))
    base = os.path.normcase(os.path.normpath(os.path.abspath(str(root))))
    return text != base and text.startswith(base.rstrip("\\/") + os.sep)


def locate_workdir(root: Path, result: Mapping[str, Any]) -> tuple[Path | None, str | None]:
    """The step's work folder on this PC, inside ``runner.workspace_root``, or None and the reason.

    A work folder is ``<root>/<YYYY-MM-DD>/<workdir_id>``. The recorded absolute path is tried first, then each
    date folder; a link in the folder's place or a path that resolves outside the root is never used."""
    workdir_id = result.get("workdir_id")
    # ':' would make `root / day / id` a drive-relative path on Windows.
    if (not isinstance(workdir_id, str) or workdir_id in ("", ".", "..")
            or any(sep in workdir_id for sep in ("/", "\\", ":"))):
        return None, "결과에 workdir_id가 없습니다"
    try:
        real_root = root.resolve()
    except (OSError, RuntimeError):
        return None, "runner.workspace_root를 확인할 수 없습니다"
    candidates: list[Path] = []
    recorded = result.get("workdir")
    # The recorded path comes from the gateway: it is checked as text before any lookup, so a UNC or other
    # outside path never reaches the file system (an lstat of \\host\share would already open SMB and send NTLM).
    if (isinstance(recorded, str) and recorded and Path(recorded).name == workdir_id
            and _lexically_inside(recorded, root)):
        candidates.append(Path(recorded))
    try:
        with os.scandir(root) as entries:
            days = sorted(entry.name for entry in entries if entry.is_dir(follow_symlinks=False))
    except OSError:
        days = []
    candidates += [root / day / workdir_id for day in days]
    for candidate in candidates:
        if not _is_plain_dir(candidate):
            continue
        try:
            real = candidate.resolve()
        except (OSError, RuntimeError):
            continue
        if real != real_root and real.is_relative_to(real_root):
            return real, None
    return None, f"이 PC의 runner.workspace_root에 작업 폴더 {workdir_id}가 없습니다"


def _compare(recorded: str | None, row: Mapping[str, Any] | None, note: str | None, *,
             files_below: int = 0, in_zone: bool = False) -> dict[str, Any]:
    """One output: the hash labhq recorded against what the walker reads now.

    A path in the result's ``outputs`` is checked for presence even without a recorded hash (over
    ``output_hash_max_bytes``, changed while hashed, a record from before #334, a wrap-up's outputs). A declared
    folder has no row of its own, since the walker lists files only: it is there while a file lies under it."""
    if row is None:
        if recorded is None and files_below:
            return {"size": None, "sha256": None, "status": UNRECORDED,
                    "detail": f"폴더 산출이라 해시가 없습니다(아래 파일 {files_below}개)"}
        if in_zone:  # the walker leaves restricted zones out: absence is not shown
            return {"size": None, "sha256": None, "status": UNCHECKED, "detail": "통제 구역 안이라 확인하지 않았습니다"}
        if note:  # the walk stopped or skipped a folder: absence is not shown
            return {"size": None, "sha256": None, "status": UNCHECKED, "detail": note}
        return {"size": None, "sha256": None, "status": MISSING,
                "detail": "기록에 있는데 파일이 없습니다" + ("" if recorded else "(기록된 해시는 없음)")}
    now = row.get("sha256") if isinstance(row.get("sha256"), str) else None
    if row.get("link"):
        status, detail = (UNREADABLE if recorded else UNRECORDED), "지금은 링크라서 따라가지 않았습니다"
    elif now is None:
        status, detail = (UNREADABLE if recorded else UNRECORDED), str(row.get("reason") or "해시하지 못했습니다")
    elif recorded is None:
        status, detail = UNRECORDED, "기록된 해시가 없습니다"
    elif now == recorded:
        status, detail = OK, ""
    else:
        status, detail = MISMATCH, "sha256이 기록과 다릅니다"
    return {"size": row.get("size"), "sha256": now, "status": status, "detail": detail}


def research_ledgers(req: Mapping[str, Any]) -> dict[str, Any]:
    """Each plan step's result ledger, as the research report check read them (cso._research_cp2)."""
    plan = req.get("plan") if isinstance(req.get("plan"), Mapping) else {}
    results = req.get("results") if isinstance(req.get("results"), Mapping) else {}
    steps = [step.get("id") for step in plan.get("steps") or [] if isinstance(step, Mapping)]
    return {sid: (results.get(sid) or {}).get("structured") for sid in steps if isinstance(sid, str)}


def ledger_binding_problems(req: Mapping[str, Any]) -> list[str]:
    """Completed research step ledgers that name another plan than the one frozen at CP1 (PR #448 review).

    A continuation keeps a reused result as a copy bound to the new plan, and puts the earlier plan back when it
    ends before any step ran; anything else is a record that pairs results with a plan they did not run under. The
    stored plan is not hashed again here: an old record must not fail on a later serializer."""
    contract = req.get("research_contract")
    if not isinstance(contract, Mapping) or not isinstance(contract.get("plan_sha256"), str):
        return []
    frozen = contract["plan_sha256"]
    problems: list[str] = []
    results = req.get("results") if isinstance(req.get("results"), Mapping) else {}
    for sid, ledger in research_ledgers(req).items():
        result = results.get(sid)
        bound = ledger.get("plan_sha256") if isinstance(ledger, Mapping) else None
        if isinstance(result, Mapping) and result.get("ok") and isinstance(bound, str) and bound != frozen:
            problems.append(f"{sid}: 단계 ledger가 고정 계획 {frozen[:12]}가 아니라 {bound[:12]}에 묶여 있습니다")
    return problems


def _receipt(req: Mapping[str, Any]) -> dict[str, Any]:
    contract = req.get("research_contract") if isinstance(req.get("research_contract"), Mapping) else {}
    receipt = (contract.get("checkpoints") or {}).get("cp2") if isinstance(contract.get("checkpoints"), Mapping) \
        else None
    return dict(receipt) if isinstance(receipt, Mapping) else {}


def rerun_report_check(req: Mapping[str, Any]) -> dict[str, Any] | None:
    """The research report's claim-anchor check run again on the stored report; None for other requests."""
    contract = req.get("research_contract")
    if not isinstance(contract, Mapping):
        return None
    recorded = contract.get("report_check")
    if not isinstance(recorded, Mapping):
        return {"recorded": None, "rerun": None, "same": None,
                "note": f"보고서 앵커 검사까지 가지 않은 연구 요청입니다(outcome {req.get('outcome') or '-'})"}
    receipt = _receipt(req)
    rerun = check_report(str(req.get("report") or ""), research_ledgers(req),
                         unsupported=receipt.get("unsupported_claims") or [],
                         refused=receipt.get("refused_evidence") or [],
                         artifact_sha256=receipt.get("artifact_sha256") or {})
    same = sorted(rerun["problems"]) == sorted(str(p) for p in recorded.get("problems") or [])
    return {"recorded": dict(recorded), "rerun": rerun, "same": same,
            "note": "" if same else "다시 돌린 결과가 기록된 검사와 다릅니다"}


async def _live_source_reports(req: Mapping[str, Any], settings: Any) -> list[Any]:
    from ..research.contract import ResearchResult
    from .verify import LiveSourceResolver, verify_sources

    resolver = LiveSourceResolver(contact=settings.research.live_source_contact,
                                  request_timeout_s=settings.research.live_source_timeout_s)
    parsed = [ResearchResult.model_validate(ledger) for ledger in research_ledgers(req).values()
              if isinstance(ledger, Mapping)]
    return list(await asyncio.gather(*(verify_sources(
        result, resolver, timeout_s=settings.research.live_source_timeout_s,
        deadline_s=settings.research.live_source_deadline_s) for result in parsed)))


def _run_live_source_reports(req: Mapping[str, Any], settings: Any) -> list[Any]:
    if not settings.research.live_source_check or not isinstance(req.get("research_contract"), Mapping):
        return []
    return asyncio.run(_live_source_reports(req, settings))


def verify_request(req: Mapping[str, Any], settings: Any) -> dict[str, Any]:
    """Compare a request's recorded outputs with the files on this PC. ``exit_code`` 0 clean, 1 problems, 2 when a
    step's work folder is not on this PC."""
    from ..adapters.owned import case_sensitive_directory
    from ..intake import overlaps_zone
    from ..runner.workspace import TaskWorkspace, restricted_zones

    root = settings.path(settings.runner.workspace_root)
    zones = restricted_zones(settings)
    results = req.get("results") if isinstance(req.get("results"), Mapping) else {}
    files: list[dict[str, Any]] = []
    reasons: list[str] = []
    unreported: dict[str, list[str]] = {}
    scans: dict[str, tuple[list[dict[str, Any]], str | None]] = {}
    for step_id, result in results.items():
        if not isinstance(result, Mapping):
            continue
        hashes = result.get("output_sha256") if isinstance(result.get("output_sha256"), Mapping) else {}
        tool_ids = result.get("output_tool_use_ids") if isinstance(result.get("output_tool_use_ids"), Mapping) else {}
        recorded = {path: value for path, value in hashes.items() if isinstance(path, str) and isinstance(value, str)}
        outputs = [path for path in result.get("outputs") or [] if isinstance(path, str)]
        if result.get("unreported_outputs"):
            unreported[step_id] = [str(path) for path in result["unreported_outputs"]]
        paths = list(dict.fromkeys([*outputs, *recorded]))
        if not paths:
            continue
        base = {"step_id": step_id, "task_id": result.get("task_id"), "agent_id": result.get("agent_id"),
                "workdir_id": result.get("workdir_id")}
        workdir, reason = locate_workdir(root, result)
        if workdir is None:
            reasons.append(f"{step_id}: {reason}")
            files += [{**base, "path": path, "tool_use_id": tool_ids.get(path),
                       "recorded_sha256": recorded.get(path), "size": None, "sha256": None,
                       "status": UNCHECKED, "detail": reason} for path in paths]
            continue
        if str(workdir) not in scans:  # a revision reuses its step's folder: one walk serves both
            scans[str(workdir)] = TaskWorkspace.existing(workdir, str(result.get("task_id") or "")).scan_output_records(
                zones, settings.runner.reference_scan_max_entries, settings.runner.reference_scan_max_depth,
                settings.runner.output_hash_max_bytes)
        records, note = scans[str(workdir)]
        # A declared name matched a differently cased file on a case-insensitive volume when it was collected.
        key = (lambda name: name) if case_sensitive_directory(workdir / "outputs") else str.casefold
        by_path = {key(row["path"]): row for row in records}
        for path in paths:
            row = by_path.get(key(path))
            below = 0 if row else sum(1 for name in by_path if name.startswith(key(path) + "/"))
            files.append({**base, "path": path, "tool_use_id": tool_ids.get(path),
                          "recorded_sha256": recorded.get(path),
                          **_compare(recorded.get(path), row, note, files_below=below,
                                     in_zone=row is None and overlaps_zone(workdir / path, zones))})
    report_check = rerun_report_check(req)
    source_reports = _run_live_source_reports(req, settings)
    problems = [f"{row['step_id']}: {row['path']} {row['status']} ({row['detail']})"
                for row in files if row["status"] in PROBLEM_STATUSES]
    if report_check and report_check["rerun"]:
        problems += [f"보고서 앵커: {problem}" for problem in report_check["rerun"]["problems"]]
    problems += [f"계획 결속: {problem}" for problem in ledger_binding_problems(req)]
    for checked in source_reports:
        problems += [f"live source: {checked.step_id}/{evidence_id} 결함"
                     for evidence_id in checked.defective_evidence]
        problems += [f"live source: {claim.claim} " + "; ".join(claim.defects)
                     for claim in checked.claims if claim.state == "defective"]
        problems += [f"live source: {checked.step_id} 재인용 결함: {message}"
                     for message in checked.recitations]
        reasons += [f"live source: {checked.step_id}/{evidence_id} requires_verification"
                    for evidence_id in checked.lookup_failures]
        reasons += [f"live source: {checked.step_id}/{evidence.evidence_id} 검사 미완료 "
                    f"({resolution.error_kind}: {resolution.detail})"
                    for evidence in checked.evidence for resolution in evidence.resolutions
                    if resolution.lookup == "skipped"]
        reasons += [f"live source: {claim.claim} 검사 미완료"
                    + (f" ({', '.join(claim.unverified_evidence)})" if claim.unverified_evidence else "")
                    for claim in checked.claims if claim.state == "unverified"]
    reasons = list(dict.fromkeys(reasons))
    request_bundle = None
    request_id = str(req.get("id") or "")
    if _SAFE_REQUEST_ID.fullmatch(request_id):
        from ..ro_crate import verify_bundle_copy

        request_bundle = verify_bundle_copy(root / "requests" / request_id, request_id)
        if req.get("bundle_path") and not request_bundle["present"]:
            request_bundle["problems"].append("요청에 기록된 묶음 폴더가 없습니다")
        problems += [f"요청 묶음 사본: {problem}" for problem in request_bundle["problems"]]
    return {"request_id": req.get("id"), "text": req.get("text"), "status": req.get("status"),
            "outcome": req.get("outcome"), "checked_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "labhq_version": __version__, "files": files, "report_check": report_check,
            "unreported_outputs": unreported, "problems": problems, "reasons": reasons,
            "source_verification": [checked.model_dump(mode="json") for checked in source_reports],
            "request_bundle": request_bundle,
            "exit_code": 2 if reasons else 1 if problems else 0}


def _counts(report: Mapping[str, Any]) -> str:
    counts = Counter(row["status"] for row in report["files"])
    return " · ".join(f"{STATUS_KO[status]} {counts[status]}" for status in STATUS_ORDER if counts[status]) or "없음"


def _sha(value: Any) -> str:
    return value[:12] if isinstance(value, str) else "-"


def render_verify(report: Mapping[str, Any]) -> str:
    """The table `labhq verify` prints."""
    lines = [f"요청 {report.get('request_id')} · status {report.get('status') or '-'} · "
             f"outcome {report.get('outcome') or '-'}",
             f"산출 파일 {len(report['files'])}개: {_counts(report)}"]
    rows = [(str(row["step_id"]), str(row.get("agent_id") or "-"), str(row.get("tool_use_id") or "-"), row["status"],
             f"{_sha(row['recorded_sha256'])}→{_sha(row['sha256'])}",
             row["path"] + (f"  ({row['detail']})" if row["detail"] and row["status"] != OK else ""))
             for row in report["files"]]
    if rows:
        table = [("step", "agent", "tool_use_id", "status", "sha256 기록→지금", "path"), *rows]
        widths = [max(len(row[i]) for row in table) for i in range(5)]
        lines += ["  " + "  ".join(cell.ljust(width) for cell, width in zip(row[:5], widths)) + "  " + row[5]
                  for row in table]
    check = report.get("report_check")
    if check is None:
        lines.append("보고서 앵커: 연구 요청이 아니라 검사하지 않음")
    elif check["rerun"] is None:
        lines.append(f"보고서 앵커: {check['note']}")
    else:
        found = check["rerun"]["problems"]
        lines.append(f"보고서 앵커(다시 돌림): 앵커 {check['rerun']['anchors']}개, 문제 {len(found)}건"
                     + ("" if check["same"] else f" — {check['note']}"))
        lines += [f"  - {problem}" for problem in found]
    unreported = report["unreported_outputs"]
    if unreported:
        lines.append("보고하지 않은 산출(경고):")
        lines += [f"  - {sid}: {path}" for sid, paths in unreported.items() for path in paths]
    source_reports = report.get("source_verification") or []
    if source_reports:
        from .verify import VerificationReport, verification_lines

        lines.append("Live source verification:")
        lines += [f"  - {line}" for raw in source_reports
                  for line in verification_lines(VerificationReport.model_validate(raw))]
    request_bundle = report.get("request_bundle")
    if request_bundle and request_bundle.get("present"):
        if request_bundle.get("crate"):
            lines.append("요청 묶음 사본: RO-Crate·MANIFEST·파일 대조 "
                         + ("문제 없음" if not request_bundle["problems"]
                            else f"문제 {len(request_bundle['problems'])}건"))
            lines.append(f"외부 입력 hash: 기록끼리 {request_bundle.get('external_input_hashes', 0)}개 대조"
                         "(원본 재해시 아님)")
        else:
            lines.append("요청 묶음 사본: " + (
                f"RO-Crate 없음(문제 {len(request_bundle['problems'])}건)"
                if request_bundle["problems"] else "RO-Crate 없는 이전 형식"
            ))
    if report.get("bundle"):
        lines.append(f"감사 번들: {report['bundle']}")
    lines += [f"확인 못함: {reason}" for reason in report["reasons"]]
    verdict = {0: "문제 없음", 1: f"문제 {len(report['problems'])}건", 2: "이 PC에서 끝까지 검사하지 못함"}
    lines.append(f"결과: {verdict[report['exit_code']]} (exit {report['exit_code']})")
    return "\n".join(lines)


def _bundle_readme(report: Mapping[str, Any]) -> str:
    check = report.get("report_check")
    if check is None:
        anchors = "연구 요청이 아니라 검사하지 않음"
    elif check["rerun"] is None:
        anchors = check["note"]
    else:
        anchors = (f"다시 돌린 검사에서 문제 {len(check['rerun']['problems'])}건"
                   + (" (기록과 같음)" if check["same"] else f" ({check['note']})"))
    request = str(report.get("text") or "").strip() or "-"
    verdict = f"문제 {len(report['problems'])}건" if report["problems"] else "문제 없음"
    lines = [f"# labhq 감사 번들 · {report.get('request_id')}", "", "## 요청", "",
             *[f"> {line}" for line in request.splitlines()], "",
             "## 검사 결과", "",
             f"- status · outcome: {report.get('status') or '-'} · {report.get('outcome') or '-'}",
             f"- 판정: {verdict}",
             f"- 산출 파일 {len(report['files'])}개: {_counts(report)}",
             f"- 보고서 앵커: {anchors}",
             f"- 보고하지 않은 산출: {sum(len(paths) for paths in report['unreported_outputs'].values())}개",
             f"- 만든 시각: {report.get('checked_at')}",
             f"- labhq 판본: {report.get('labhq_version')}", "",
             NO_FILES_LINE, ""]
    if report.get("reasons"):
        lines += [f"파일 재해시는 러너 PC에서 `labhq verify {report.get('request_id')}`를 실행하세요.", ""]
    if report["problems"]:
        lines += ["## 문제", "", *[f"- {problem}" for problem in report["problems"]], ""]
    lines += ["## 파일", "",
              "- `report.md`: PI용 본문(이 본문을 claim anchor 검사)",
              "- `report_appendix.md`: 단계·경고·리뷰·비용 실행 기록",
              "- `claims.json`: 단계 ledger, CP2 receipt(`artifact_sha256` 포함), 보고서 앵커 검사(기록과 다시 돌린 결과)",
              "- `artifacts.json`: 산출 파일마다 경로·크기·기록 sha256·지금 sha256·상태·만든 직원·task", ""]
    return "\n".join(lines)


def bundle_bytes(report: Mapping[str, Any], req: Mapping[str, Any]) -> bytes:
    """Build the three-record audit zip in memory. No output file goes in."""
    import zipfile

    contract = req.get("research_contract") if isinstance(req.get("research_contract"), Mapping) else {}
    receipt = _receipt(req)
    claims = {"request_id": report.get("request_id"), "plan_sha256": contract.get("plan_sha256"),
              "ledgers": research_ledgers(req) if contract else {},
              "artifact_sha256": receipt.get("artifact_sha256") or {},
              "cp2": {key: receipt[key] for key in ("decision", "plan_sha256", "refused_rows", "refused_evidence",
                                                    "unsupported_claims", "unreported_outputs") if key in receipt},
              "report_check": report.get("report_check"),
              "source_verification": report.get("source_verification") or []}
    artifacts = [{key: row.get(key) for key in ("step_id", "task_id", "agent_id", "tool_use_id", "workdir_id",
                                                 "path", "size", "recorded_sha256", "sha256", "status")}
                 for row in report["files"]]
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr("README.md", _bundle_readme(report))
        bundle.writestr("report.md", str(req.get("report") or ""))
        bundle.writestr("report_appendix.md", str(req.get("report_appendix") or ""))
        bundle.writestr("claims.json", json.dumps(claims, ensure_ascii=False, indent=2, default=str))
        bundle.writestr("artifacts.json", json.dumps(artifacts, ensure_ascii=False, indent=2))
    return stream.getvalue()


def write_bundle(report: Mapping[str, Any], req: Mapping[str, Any], out: Path) -> Path:
    """Write README.md, claims.json and artifacts.json into ``out`` (a zip). No output file goes in."""
    import tempfile

    out = Path(out)
    if not out.name or out.is_dir():
        raise ValueError(f"감사 번들은 파일 이름이 있는 경로여야 합니다: {out}")
    # A new, exclusively created temp file beside the target: a name planted in advance (a link at
    # `<out>.partial` pointing at a PI file) is never opened, so writing the bundle cannot truncate another file.
    # os.replace then swaps the name itself and never follows a link at `out`.
    fd, partial = tempfile.mkstemp(prefix=f".{out.name}.", suffix=".partial", dir=out.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(bundle_bytes(report, req))
        os.replace(partial, out)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(partial)
        raise
    return out
