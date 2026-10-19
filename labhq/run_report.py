"""Pipeline diagram, step timeline and execution report of the steps that actually ran (#756).

Every edition and its PDF show what ran: the plan's steps with a run record, in plan order, with their staff, inputs,
outputs and QC points, and a timeline of each step's recorded start, end, duration and cost. A value without a record
is written as missing, never as zero or a guessed time. Nextflow's own report, timeline, trace and DAG are linked when a
step left them in ``outputs/pipeline_info/`` (bioinfo-agent turns all four on); a step without them says so.

The caller decides the data policy: ``hidden`` names the steps on internal data, which appear only as
"내부 자료 단계 N" without inputs or outputs, and ``scrub`` masks private text before anything is drawn or written.
The pictures follow `report_abstract`: Korean in ``<text>``, a system font stack, no script, no external resource.
"""

from __future__ import annotations

import posixpath
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import quote

from .costs import task_cost_item
from .report_contract import ReportAsset, ReportPackage
from .report_abstract import AGENT_LABELS, FONT, INK, INK_2, LINE, PAPER, SURFACE, _clip, _esc, _plain

DIAGRAM_FILE = "run/pipeline_diagram.svg"
TIMELINE_FILE = "run/run_timeline.svg"
REPORT_FILE = "run/run_report.md"
DIAGRAM_LABEL = "분석 파이프라인 모식도"
TIMELINE_LABEL = "단계별 실행 타임라인"
MISSING = "기록 없음"

NEXTFLOW_KINDS = ("report", "timeline", "trace", "dag")
_NEXTFLOW_SUFFIXES = {"report": {".html"}, "timeline": {".html"}, "trace": {".txt", ".tsv"},
                      "dag": {".html", ".svg", ".dot", ".mmd"}}
# nf-core and bioinfo-agent name them execution_report_<ts>.html, pipeline_dag_<ts>.html or <run_label>-trace-<ts>.txt.
_NEXTFLOW_FILE = re.compile(r"(?i)(?:^|[._-])(?:execution_|pipeline_)?(report|timeline|trace|dag)(?=[._-]|$)")

FLOW_COLORS = {  # (accent, soft background): the web office's work, hpc, warn and accent tokens
    "입력": ("#1D6B40", "#E6F1EA"),
    "방법": ("#7A3E9D", "#F1E9F6"),
    "QC": ("#7A4A00", "#FFF0D0"),
    "결과": ("#2848A6", "#E9EDF9"),
}
WIDTH, PAD = 800, 28
MAX_DRAWN = 16
_UNITS = 84  # text width in report_abstract units inside a box (14 px)


def nextflow_kind(name: str) -> str | None:
    """``report``, ``timeline``, ``trace`` or ``dag`` for a Nextflow run record under a ``pipeline_info`` folder."""
    path = PurePosixPath(name.replace("\\", "/"))
    if "pipeline_info" not in {part.casefold() for part in path.parts[:-1]}:
        return None
    found = _NEXTFLOW_FILE.search(path.stem)
    kind = found.group(1).casefold() if found else None
    return kind if kind and path.suffix.casefold() in _NEXTFLOW_SUFFIXES[kind] else None


def step_flow(step: Mapping[str, Any], *, first: bool, terminal: bool) -> str:
    text = f"{step.get('id', '')} {step.get('instruction', '')}".casefold()
    if re.search(r"(?:^|[_\s-])qc(?:$|[_\s-])|품질|검토|review|check", text):
        return "QC"
    if terminal or re.search(r"보고|report|종합|결론|result", text):
        return "결과"
    if first or re.search(r"입력|자료|manifest|download|verify|source", text):
        return "입력"
    return "방법"


@dataclass
class StepRun:
    step_id: str
    label: str
    hidden: bool
    agent: str
    flow: str
    status: str
    branch: str = ""
    depends: list[str] = field(default_factory=list)
    inputs: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    engine: str = ""
    started: float | None = None
    ended: float | None = None
    cost: str = MISSING
    cost_usd: float | None = None
    nextflow: dict[str, str] = field(default_factory=dict)
    nextflow_note: str = ""


@dataclass
class RunSummary:
    steps: list[StepRun]
    not_run: list[str]
    other_nextflow: dict[str, dict[str, str]] = field(default_factory=dict)
    withheld: list[str] = field(default_factory=list)


def _ran(result: Any) -> bool:
    # The CSO records a step it never started (unmet dependency, skipped revision) with an empty task id.
    return isinstance(result, Mapping) and not ("task_id" in result and not result.get("task_id"))


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _runs(result: Mapping[str, Any]) -> tuple[dict[str, Any], list[Mapping[str, Any]]]:
    provenance = result.get("provenance") if isinstance(result.get("provenance"), Mapping) else {}
    runs = provenance.get("runs") if isinstance(provenance.get("runs"), Mapping) else {}
    return dict(provenance), [run for run in runs.values() if isinstance(run, Mapping)]


def _times(result: Mapping[str, Any]) -> tuple[float | None, float | None]:
    """The first recorded start and, when every started run ended, the last end. A wake-up run counts in the span."""
    _provenance, runs = _runs(result)
    starts = [value for run in runs if (value := _number(run.get("started_at"))) is not None]
    ends = [value for run in runs if (value := _number(run.get("ended_at"))) is not None]
    started = min(starts) if starts else None
    ended = max(ends) if ends and len(ends) >= len(starts) else None
    if started is not None and ended is not None and ended < started:
        ended = None
    return started, ended


def _engine(result: Mapping[str, Any]) -> str:
    provenance, runs = _runs(result)
    own = (provenance.get("runs") or {}).get(result.get("task_id")) if isinstance(provenance.get("runs"), Mapping) else None
    run = own if isinstance(own, Mapping) else (runs[0] if runs else {})
    engine = run.get("engine") or provenance.get("engine")
    model = run.get("model_id") or run.get("model") or provenance.get("model")
    return " · ".join(str(value) for value in (engine, model) if value)


def _cost(result: Mapping[str, Any]) -> tuple[str, float | None]:
    item = task_cost_item(dict(result))
    usd = item.get("usd")
    if item.get("status") == "actual" and isinstance(usd, (int, float)):
        return f"${usd:.2f}", float(usd)
    if item.get("status") == "estimated" and isinstance(usd, (int, float)):
        return f"추정 ${usd:.2f}", float(usd)
    return MISSING, None


def _names(values: Any, scrub: Callable[[str], str], limit: int = 4) -> list[str]:
    names = []
    for value in values if isinstance(values, list) else []:
        text = scrub(str(value)).replace("\\", "/").rstrip("/")
        name = text.rsplit("/", 1)[-1] if "<local-path>" not in text else "로컬 자료"
        if name and name not in names:
            names.append(name)
    return names[:limit] + ([f"외 {len(names) - limit}개"] if len(names) > limit else [])


def collect(req: Mapping[str, Any], *, hidden: Callable[[str], bool], scrub: Callable[[str], str],
            nextflow: Mapping[str, Mapping[str, str]] | None = None,
            withheld: list[str] | None = None) -> RunSummary:
    """The executed steps of ``req`` in plan order, then any recorded step the plan does not list (a direct run).

    ``nextflow``: published Nextflow records by step id and kind. ``withheld``: notes on records left out."""
    plan = req.get("plan") if isinstance(req.get("plan"), Mapping) else {}
    planned = [step for step in plan.get("steps") or [] if isinstance(step, Mapping) and step.get("id")]
    results = req.get("results") if isinstance(req.get("results"), Mapping) else {}
    ids = [str(step["id"]) for step in planned]
    steps_by_id = {str(step["id"]): step for step in planned}
    ids += [str(sid) for sid in results if str(sid) not in steps_by_id]
    needed = {str(dep) for step in planned for dep in step.get("depends_on") or []}
    internal_count = 0
    labels: dict[str, str] = {}
    for sid in ids:
        if hidden(sid):
            internal_count += 1
            labels[sid] = f"내부 자료 단계 {internal_count}"
        else:
            labels[sid] = _plain(scrub(sid)) or "단계"
    nextflow = {str(key): dict(value) for key, value in (nextflow or {}).items()}
    runs: list[StepRun] = []
    not_run: list[str] = []
    for index, sid in enumerate(ids):
        step = steps_by_id.get(sid, {"id": sid})
        result = results.get(sid)
        if not _ran(result):
            not_run.append(labels[sid])
            continue
        secret = hidden(sid)
        agent = str(step.get("agent_id") or result.get("agent_id") or "")
        started, ended = _times(result)
        cost, usd = _cost(result)
        ok = result.get("ok")
        status = "완료" if ok is True else "실패" if ok is False else "미확인"
        records = nextflow.pop(sid, {})
        runs.append(StepRun(
            step_id=sid, label=labels[sid], hidden=secret, agent=agent,
            flow=step_flow(step, first=index == 0, terminal=bool(planned) and sid not in needed),
            status=status, branch="" if secret else _plain(scrub(str(step.get("branch") or ""))),
            depends=[labels.get(str(dep), _plain(scrub(str(dep)))) for dep in step.get("depends_on") or []],
            inputs=[] if secret else _names(step.get("input_refs"), scrub),
            outputs=[] if secret else _names(result.get("outputs"), scrub),
            engine=_plain(scrub(_engine(result))), started=started, ended=ended, cost=cost, cost_usd=usd,
            nextflow={} if secret else records,
            nextflow_note=("내부 자료 단계라 게시하지 않음" if secret and records else
                           "" if records else
                           "기록 없음 · Nextflow 사용 여부 미확인" if agent == "bioinfo-agent" else
                           "쓰지 않음(산출 기록 기준)"),
        ))
    other = {labels.get(sid, _plain(scrub(sid)) or "단계"): records
             for sid, records in nextflow.items() if records and not hidden(sid)}
    return RunSummary(runs, not_run, other, list(withheld or []))


# ---------------------------------------------------------------------------------------------------------------------
# text

def clock(value: float | None) -> str:
    if value is None:
        return MISSING
    try:
        return datetime.fromtimestamp(value).astimezone().strftime("%Y-%m-%d %H:%M:%S")
    except (OverflowError, OSError, ValueError):
        return MISSING


def zone(summary: RunSummary) -> str:
    stamp = next((step.started for step in summary.steps if step.started is not None), None)
    if stamp is None:
        return ""
    try:
        offset = datetime.fromtimestamp(stamp).astimezone().strftime("%z")
    except (OverflowError, OSError, ValueError):
        return ""
    return f"UTC{offset[:3]}:{offset[3:]}" if len(offset) == 5 else ""


def duration(started: float | None, ended: float | None) -> str:
    if started is None:
        return MISSING
    if ended is None:
        return "종료 기록 없음"
    seconds = int(round(ended - started))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}시간 {minutes}분"
    if minutes:
        return f"{minutes}분 {secs}초"
    return f"{secs}초"


def _agent_label(agent: str) -> str:
    return f"{AGENT_LABELS[agent]}({agent})" if agent in AGENT_LABELS else (agent or "담당 기록 없음")


def _nextflow_text(step: StepRun) -> str:
    if not step.nextflow:
        return step.nextflow_note
    missing = [kind for kind in NEXTFLOW_KINDS if kind not in step.nextflow]
    return "사용 · " + "·".join(kind for kind in NEXTFLOW_KINDS if kind in step.nextflow) + (
        f" (없음: {'·'.join(missing)})" if missing else "")


def _cell(value: str) -> str:
    return " ".join(str(value).replace("|", "\\|").split()) or "-"


def _link(document: str, target: str, label: str) -> str:
    relative = posixpath.relpath(target, posixpath.dirname(document) or ".")
    return f"[{label}]({quote(relative, safe='/')})"


def _nextflow_items(summary: RunSummary, document: str | None) -> list[str]:
    records = [(step.label, step.nextflow) for step in summary.steps if step.nextflow]
    records += list(summary.other_nextflow.items())
    return [f"{_cell(label)} " + " · ".join(_link(document, path, kind) if document else kind for kind, path in sorted(
        found.items(), key=lambda item: NEXTFLOW_KINDS.index(item[0]))) for label, found in records]


def nextflow_line(summary: RunSummary, document: str | None) -> str:
    """One line for the front: the Nextflow records (linked from ``document``, or plain), or why there are none."""
    items = _nextflow_items(summary, document)
    if not items:
        return "Nextflow 보고서: 이 요청의 실행 기록에 없습니다. labhq 실행 보고서가 같은 역할을 합니다."
    return "Nextflow 보고서: " + "; ".join(items)


def run_report_markdown(summary: RunSummary) -> str:
    document = REPORT_FILE
    lines = ["# 실행 보고서", "",
             "labhq가 계획과 작업 기록에서 실제로 실행된 단계만 모았습니다. 기록이 없는 시각·시간·비용은 "
             f"'{MISSING}'으로 두었고 추정해 채우지 않았습니다."]
    if shown_zone := zone(summary):
        lines[-1] += f" 시각은 {shown_zone} 기준입니다."
    lines += ["", f"![{DIAGRAM_LABEL}]({posixpath.basename(DIAGRAM_FILE)})", "",
              f"![{TIMELINE_LABEL}]({posixpath.basename(TIMELINE_FILE)})", "",
              "## 단계별 실행", ""]
    if summary.steps:
        lines += ["| 순서 | 단계 | 담당 | 상태 | 시작 | 종료 | 걸린 시간 | 비용 | Nextflow |",
                  "|---:|---|---|---|---|---|---|---|---|"]
        lines += [f"| {index} | {_cell(step.label)} | {_cell(_agent_label(step.agent))} | {step.status} | "
                  f"{clock(step.started)} | {clock(step.ended) if step.started is not None else MISSING} | "
                  f"{duration(step.started, step.ended)} | {_cell(step.cost)} | {_cell(_nextflow_text(step))} |"
                  for index, step in enumerate(summary.steps, 1)]
        known = [step.cost_usd for step in summary.steps if step.cost_usd is not None]
        unknown = len(summary.steps) - len(known)
        total = f"${sum(known):.2f}" if known else MISSING
        lines += ["", f"- 기록된 단계 비용 합계: {total}" + (f" (비용 기록 없는 단계 {unknown}개 제외)" if known and unknown else "")]
    else:
        lines.append("실행 기록이 있는 단계가 없습니다.")
    if summary.not_run:
        lines += ["", "## 실행하지 않은 계획 단계", "",
                  "아래 단계는 계획에 있었지만 실행 기록이 없어 그림과 표에서 뺐습니다.", ""]
        lines += [f"- {_cell(label)}" for label in summary.not_run]
    lines += ["", "## Nextflow", ""]
    used = [step for step in summary.steps if step.nextflow]
    if used or summary.other_nextflow:
        lines.append("Nextflow가 남긴 실행 보고서(report), 타임라인(timeline), trace와 DAG입니다. 개인 경로는 게시 전 검사로 바꿨습니다.")
        lines += [""] + [f"- {item}" for item in _nextflow_items(summary, document)]
        for step in used:
            if missing := [kind for kind in NEXTFLOW_KINDS if kind not in step.nextflow]:
                lines.append(f"- {_cell(step.label)}: {'·'.join(missing)} 기록 없음")
    else:
        lines.append("Nextflow 산출 기록이 없습니다. 이 요청은 Nextflow를 쓰지 않았거나 그 기록을 남기지 않았고, "
                     "위 표가 같은 역할의 labhq 실행 보고서입니다.")
    if summary.withheld:
        lines += [""] + [f"- 넣지 않은 기록: {_cell(note)}" for note in summary.withheld]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------------------------------------------------
# pictures

def _svg_open(height: int, label: str, title: str) -> list[str]:
    return [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {height}" width="{WIDTH}" height="{height}" '
        f'role="img" aria-label="{_esc(label)}" xml:lang="ko">',
        f"<title>{_esc(label)}</title>",
        f'<rect x="0" y="0" width="{WIDTH}" height="{height}" fill="{PAPER}"/>',
        f'<text x="{PAD}" y="{PAD + 22}" font-family="{FONT}" font-size="22" font-weight="700" fill="{INK}">'
        f"{_esc(title)}</text>",
    ]


def _text(x: int, y: int, value: str, *, size: int = 14, color: str = INK, bold: bool = False,
          anchor: str = "start") -> str:
    weight = ' font-weight="700"' if bold else ""
    end = f' text-anchor="{anchor}"' if anchor != "start" else ""
    return (f'<text x="{x}" y="{y}" font-family="{FONT}" font-size="{size}"{weight}{end} fill="{color}">'
            f"{_esc(value)}</text>")


def pipeline_svg(summary: RunSummary) -> str:
    """Executed steps top to bottom: flow band, staff, inputs, outputs and how it ran."""
    band, line_h, gap = 76, 20, 26
    boxes: list[tuple[StepRun, list[tuple[str, bool, str]]]] = []
    for index, step in enumerate(summary.steps[:MAX_DRAWN], 1):
        head = f"{index}. {step.label}" + (f" · 갈래 {step.branch}" if step.branch else "")
        lines = [(_clip(head + f" · {_agent_label(step.agent)}", _UNITS), True, INK)]
        if step.depends:
            lines.append((_clip("선행: " + ", ".join(step.depends), _UNITS), False, INK_2))
        if step.hidden:
            lines.append(("입력·산출: 내부 자료라 표시하지 않음", False, INK_2))
        else:
            lines.append((_clip("입력: " + (", ".join(step.inputs) or "계획에 적힌 입력 없음"), _UNITS), False, INK))
            lines.append((_clip("산출: " + (", ".join(step.outputs) or "기록 없음"), _UNITS), False, INK))
        ran = " · ".join(value for value in (step.engine, "Nextflow " + _nextflow_text(step)) if value)
        lines.append((_clip(f"{step.status} · {ran}", _UNITS), False, INK_2))
        boxes.append((step, lines))
    heights = [len(lines) * line_h + 20 for _step, lines in boxes] or [60]
    extra = len(summary.steps) - MAX_DRAWN
    height = PAD + 54 + sum(heights) + gap * max(len(heights) - 1, 0) + 64 + (22 if extra > 0 else 0)
    out = _svg_open(height, "분석 파이프라인 모식도: 실제로 실행된 단계, 담당, 입력, 산출, QC 지점",
                    "분석 파이프라인 — 실행된 단계")
    top = PAD + 54
    if not boxes:
        out.append(f'<rect x="{PAD}" y="{top}" width="{WIDTH - 2 * PAD}" height="60" rx="12" fill="{SURFACE}" '
                   f'stroke="{LINE}" stroke-width="1.5"/>')
        out.append(_text(PAD + 16, top + 35, "실행 기록이 있는 단계가 없습니다.", color=INK_2))
        top += 60
    for position, ((step, lines), box_h) in enumerate(zip(boxes, heights)):
        accent, soft = FLOW_COLORS.get(step.flow, FLOW_COLORS["방법"])
        out.append(f'<rect x="{PAD}" y="{top}" width="{WIDTH - 2 * PAD}" height="{box_h}" rx="12" fill="{soft}" '
                   f'stroke="{accent}" stroke-width="1.5"/>')
        out.append(f'<path d="M{PAD + 12} {top} H{PAD + band} V{top + box_h} H{PAD + 12} '
                   f'a12 12 0 0 1 -12 -12 V{top + 12} a12 12 0 0 1 12 -12 Z" fill="{accent}"/>')
        out.append(_text(PAD + band // 2, top + box_h // 2 + 6, step.flow, size=16, color=SURFACE, bold=True,
                         anchor="middle"))
        y = top + 24
        for value, bold, color in lines:
            out.append(_text(PAD + band + 14, y, value, color=color, bold=bold))
            y += line_h
        top += box_h
        if position < len(boxes) - 1:
            x = WIDTH // 2
            out.append(f'<polygon points="{x - 12},{top + 5} {x + 12},{top + 5} {x},{top + gap - 5}" fill="{INK_2}"/>')
            top += gap
    if extra > 0:
        out.append(_text(PAD, top + 22, f"외 {extra}단계는 실행 보고서 표에 있습니다.", color=INK_2))
        top += 22
    skipped = f" 실행하지 않은 계획 단계 {len(summary.not_run)}개는 그리지 않았습니다." if summary.not_run else ""
    out.append(_text(PAD, height - 36, "labhq가 계획과 작업 기록에서 실행 기록이 있는 단계만 그렸습니다." + skipped,
                     size=12, color=INK_2))
    out.append(_text(PAD, height - 16, "띠 색: 입력·방법·QC·결과. 선행은 그 단계가 기다린 단계입니다.", size=12, color=INK_2))
    out.append("</svg>")
    return "\n".join(out)


def timeline_svg(summary: RunSummary) -> str:
    """One row per executed step: a bar from its recorded start to end, or the missing record written out."""
    label_w, right_w, row_h = 210, 170, 30
    left, right = PAD + label_w, WIDTH - PAD - right_w
    steps = summary.steps[:MAX_DRAWN]
    timed = [step for step in steps if step.started is not None and step.ended is not None]
    first = min((step.started for step in timed), default=None)
    last = max((step.ended for step in timed), default=None)
    extra = len(summary.steps) - MAX_DRAWN
    height = PAD + 54 + 40 + max(len(steps), 1) * row_h + 56 + (22 if extra > 0 else 0)
    out = _svg_open(height, "단계별 실행 타임라인: 시작, 종료, 걸린 시간, 담당, 비용", "단계별 실행 타임라인")
    top = PAD + 54
    if first is not None and last is not None:
        out.append(f'<line x1="{left}" y1="{top + 20}" x2="{right}" y2="{top + 20}" stroke="{INK_2}" stroke-width="1"/>')
        out.append(_text(left, top + 12, "시작 " + clock(first), size=12, color=INK_2))
        out.append(_text(right, top + 12, "끝 " + clock(last), size=12, color=INK_2, anchor="end"))
        out.append(_text(WIDTH - PAD, top + 12, "전체 " + duration(first, last), size=12, color=INK_2, anchor="end"))
    else:
        out.append(_text(left, top + 14, "시작·종료 시각이 모두 기록된 단계가 없어 막대를 그리지 않았습니다.",
                         size=12, color=INK_2))
    top += 40
    if not steps:
        out.append(_text(PAD, top + 20, "실행 기록이 있는 단계가 없습니다.", color=INK_2))
    span = (last - first) if first is not None and last is not None else 0.0
    for index, step in enumerate(steps):
        y = top + index * row_h
        if index % 2 == 0:
            out.append(f'<rect x="{PAD}" y="{y}" width="{WIDTH - 2 * PAD}" height="{row_h}" fill="{SURFACE}"/>')
        name = _clip(f"{index + 1}. {step.label}", 26)
        who = AGENT_LABELS.get(step.agent, step.agent or "담당 기록 없음")
        out.append(_text(PAD + 6, y + 13, name, size=12, bold=True))
        out.append(_text(PAD + 6, y + 26, _clip(who, 26), size=11, color=INK_2))
        accent, _soft = FLOW_COLORS.get(step.flow, FLOW_COLORS["방법"])
        if step.started is not None and step.ended is not None and first is not None:
            scale = (right - left) / span if span > 0 else 0.0
            x = left + (step.started - first) * scale
            bar = max((step.ended - step.started) * scale, 3.0)
            out.append(f'<rect x="{x:.1f}" y="{y + 7}" width="{bar:.1f}" height="{row_h - 14}" rx="4" fill="{accent}"/>')
        else:
            note = "시각 기록 없음" if step.started is None else "종료 기록 없음 · 시작 " + clock(step.started)
            out.append(_text(left + 4, y + 20, note, size=12, color=INK_2))
        out.append(_text(WIDTH - PAD - 6, y + 20, _clip(f"{duration(step.started, step.ended)} · {step.cost}", 30),
                         size=12, anchor="end"))
    y = top + max(len(steps), 1) * row_h
    if extra > 0:
        out.append(_text(PAD, y + 20, f"외 {extra}단계는 실행 보고서 표에 있습니다.", size=12, color=INK_2))
    shown_zone = zone(summary)
    out.append(_text(PAD, height - 16, "막대는 기록된 시작부터 종료까지입니다. 오른쪽은 걸린 시간 · 비용, 기록이 없으면 "
                     f"'{MISSING}'입니다." + (f" 시각 {shown_zone}." if shown_zone else ""), size=12, color=INK_2))
    out.append("</svg>")
    return "\n".join(out)


def add_to_package(package: ReportPackage, summary: RunSummary, document: str, *,
                   diagram: str = DIAGRAM_FILE, timeline: str = TIMELINE_FILE, report: str | None = REPORT_FILE) -> None:
    """Show the two pictures under the answer, with links relative to ``document``."""
    def relative(target: str) -> str:
        return posixpath.relpath(target, posixpath.dirname(document) or ".")

    package.pipeline = [ReportAsset(DIAGRAM_LABEL, relative(diagram)), ReportAsset(TIMELINE_LABEL, relative(timeline))]
    package.pipeline_note = ([_link(document, report, "실행 보고서") + " — 단계별 시작·종료·걸린 시간·담당·비용"]
                             if report else [])
    package.pipeline_note.append(nextflow_line(summary, document if report else None))


def nextflow_outputs(req: Mapping[str, Any]) -> dict[str, dict[str, str]]:
    """Nextflow records each step reported as outputs, by step id and kind (the PDF names them without links)."""
    results = req.get("results") if isinstance(req.get("results"), Mapping) else {}
    found: dict[str, dict[str, str]] = {}
    for sid, result in results.items():
        for output in (result.get("outputs") or []) if isinstance(result, Mapping) else []:
            if kind := nextflow_kind(str(output)):
                found.setdefault(str(sid), {}).setdefault(kind, str(output))
    return found


def build(req: Mapping[str, Any], *, hidden: Callable[[str], bool], scrub: Callable[[str], str],
          nextflow: Mapping[str, Mapping[str, str]] | None = None,
          withheld: list[str] | None = None) -> tuple[RunSummary, dict[str, str | bytes]]:
    """The summary and the edition files: the two SVGs (bytes) and ``run/run_report.md``."""
    summary = collect(req, hidden=hidden, scrub=scrub, nextflow=nextflow, withheld=withheld)
    return summary, {
        DIAGRAM_FILE: pipeline_svg(summary).encode("utf-8"),
        TIMELINE_FILE: timeline_svg(summary).encode("utf-8"),
        REPORT_FILE: run_report_markdown(summary),
    }
