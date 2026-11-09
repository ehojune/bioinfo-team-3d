"""Judge the #1331 trial: reproduce scDRS (Zhang 2022 Nat Genet) on one HPC node with a Paper2Agent hire.

Seven checks, each PASS/FAIL/SKIP with its evidence; exit 0 only when every check that ran passed.

  repro     the run's scDRS cell type-trait associations reproduce the fixed table ``scdrs_1331_table.tsv``
            (paper values, written before the run): >=15 of 19 expected pairs q<0.05, <=1 of 5 off-block pairs.
  hire      an active Paper2Agent contract hire for the scDRS paper ran the steps the plan gave it.
  overlap   two or more HPC-using staff steps of the request ran at the same time.
  session   while those steps overlapped, at most one HPC login was recorded (they shared one session).
  window    no 60-minute window holds more than 2 HPC logins (whole ledger around the request).
  verify    `labhq verify <request>` exits 0.
  public    no site term (host, user, remote path, queue) appears in the request's report or bundle text files.

Start ``--watch`` right after the request is created: the events table keeps only recent events, so dispatch/result
times are recorded while the run goes (see read_events). Read only: the state database and the login ledger are opened
with ``mode=ro``. Site terms are never printed, only which file held how many. Run it with the instance's python so
``labhq`` imports:

    python -I tools/trial/judge_1331.py --config <labhq.yaml> --request <req_id> --events-log <jsonl> [--json <out>]
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

WINDOW_S = 3600
WINDOW_MAX = 2
SESSION_MAX = 1
HPC_TOOL = re.compile(r"\bhpc_[a-z_]+\b|mcp__[A-Za-z0-9_-]*hpc[A-Za-z0-9_-]*")
TEXT_SUFFIXES = {".md", ".txt", ".html", ".json", ".csv", ".tsv", ".yaml", ".yml", ".log", ".py", ".r", ".sh", ".tex"}


def result(name: str, status: str, detail: str, **evidence) -> dict:
    return {"check": name, "status": status, "detail": detail, **evidence}


# ----- steps and logins -----
def ro(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=10)


STEP_TYPES = {"task.dispatched", "task.result"}
DONE_TYPES = {"request.completed", "request.failed", "request.cancelled"}


def _slim(ev: dict, raw: str) -> dict:
    data = ev.get("data") or {}
    slim = {k: ev.get(k) for k in ("type", "ts", "task_id", "agent_id", "request_id")}
    slim["data"] = {"step_id": data.get("step_id"), "attempt": data.get("attempt")}
    slim["hpc"] = bool(HPC_TOOL.search(raw))
    return slim


def read_events(db_path: Path, request_id: str, log: Path | None = None) -> list[tuple[int, dict, float | None]]:
    """(seq, event, seen_at) for the request's task.dispatched/task.result and its other events that mention an HPC
    tool, oldest first.

    The ``events`` table keeps only the latest events (replay retention), and the per-request archive
    (``messenger_events``) keeps logs and approvals but not dispatch/result. So a run is watched while it goes
    (``--watch`` appends to ``log`` with the time each event was first seen) and the verdict merges that log with what
    the database still holds. An event without ts and not in the log takes the ts of the next live event that has one."""
    found: dict[int, tuple[dict, float | None]] = {}
    if log and log.exists():
        for line in log.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                found[int(row["seq"])] = (row["event"], row.get("seen_at"))
    db = ro(db_path)
    try:
        live = db.execute("SELECT seq, body FROM events ORDER BY seq").fetchall()
        try:
            archived = db.execute("SELECT seq, body FROM messenger_events WHERE request_id = ? ORDER BY seq",
                                  (request_id,)).fetchall()
        except sqlite3.OperationalError:
            archived = []
    finally:
        db.close()
    next_ts: dict[int, float] = {}
    later: float | None = None
    for seq, raw in reversed(live):
        try:
            ts = json.loads(raw).get("ts")
        except ValueError:
            ts = None
        if isinstance(ts, (int, float)):
            later = float(ts)
        if later is not None:
            next_ts[seq] = later
    for seq, raw in [*archived, *live]:
        if seq in found:
            continue
        try:
            ev = json.loads(raw)
        except ValueError:
            continue
        if ev.get("request_id") == request_id and (ev.get("type") in STEP_TYPES or HPC_TOOL.search(raw)):
            found[seq] = (_slim(ev, raw), None)
    out = []
    for seq in sorted(found):
        ev, seen = found[seq]
        if seen is None and not isinstance(ev.get("ts"), (int, float)):
            seen = next_ts.get(seq)
        out.append((seq, ev, seen))
    return out


def watch(db_path: Path, request_id: str, log: Path, every: float, until: float) -> int:
    """Append the request's dispatch/result/HPC events to ``log`` with the time first seen, until the request ends or
    ``until`` (epoch seconds) passes. Safe to restart: rows already in the log are skipped."""
    log.parent.mkdir(parents=True, exist_ok=True)
    seen: set[int] = set()
    if log.exists():
        seen = {int(json.loads(line)["seq"]) for line in log.read_text(encoding="utf-8").splitlines() if line.strip()}
    while time.time() < until:
        now = time.time()
        db = ro(db_path)
        try:
            rows = db.execute("SELECT seq, body FROM events ORDER BY seq").fetchall()
        finally:
            db.close()
        ended = False
        with log.open("a", encoding="utf-8") as fh:
            for seq, raw in rows:
                if seq in seen:
                    continue
                try:
                    ev = json.loads(raw)
                except ValueError:
                    continue
                if ev.get("request_id") != request_id:
                    continue
                ended = ended or ev.get("type") in DONE_TYPES
                if ev.get("type") in STEP_TYPES or HPC_TOOL.search(raw):
                    fh.write(json.dumps({"seq": seq, "seen_at": now, "event": _slim(ev, raw)}, ensure_ascii=False) + "\n")
                    seen.add(seq)
        if ended:
            return 0
        time.sleep(every)
    return 0


def step_intervals(db_path: Path, request_id: str, forced: set[str], log: Path | None = None) -> list[dict]:
    """Each task of the request: dispatch (seq, ts), result (seq, ts), agent, step id, and whether it used HPC.

    Overlap is judged on event order (seq), which the gateway assigns in arrival order; times are for the ledger."""
    tasks: dict[str, dict] = {}
    hpc_ids: set[str] = set()
    for seq, ev, seen in read_events(db_path, request_id, log):
        tid = ev.get("task_id")
        if not tid:
            continue
        typ = ev.get("type")
        ts = float(ev["ts"]) if isinstance(ev.get("ts"), (int, float)) else seen
        if ev.get("hpc"):
            hpc_ids.add(tid)
        if typ == "task.dispatched" and tid not in tasks:
            data = ev.get("data") or {}
            tasks[tid] = {"task_id": tid, "agent_id": ev.get("agent_id"), "step_id": data.get("step_id"),
                          "attempt": data.get("attempt"), "start_seq": seq, "start_ts": ts, "end_seq": None,
                          "end_ts": None, "hpc": False}
        elif typ == "task.result" and tid in tasks and tasks[tid]["end_seq"] is None:
            tasks[tid]["end_seq"], tasks[tid]["end_ts"] = seq, ts
    for task in tasks.values():
        if task["task_id"] in hpc_ids or task["step_id"] in forced or task["task_id"] in forced:
            task["hpc"] = True
    return sorted(tasks.values(), key=lambda t: t["start_seq"])


def overlapping_pairs(tasks: list[dict]) -> list[tuple[dict, dict]]:
    hpc = [t for t in tasks if t["hpc"]]
    pairs = []
    for i, a in enumerate(hpc):
        for b in hpc[i + 1:]:
            if a["step_id"] == b["step_id"]:
                continue  # a retry of the same step is not two staff steps
            a_end = a["end_seq"] if a["end_seq"] is not None else float("inf")
            if b["start_seq"] < a_end:
                pairs.append((a, b))
    return pairs


def check_overlap(tasks: list[dict]) -> dict:
    pairs = overlapping_pairs(tasks)
    hpc = [{k: t[k] for k in ("task_id", "agent_id", "step_id", "start_seq", "end_seq")} for t in tasks if t["hpc"]]
    shown = [[a["step_id"], b["step_id"]] for a, b in pairs]
    if pairs:
        return result("overlap", "PASS", f"HPC 단계 겹침 {len(pairs)}쌍", pairs=shown, hpc_steps=hpc)
    return result("overlap", "FAIL", f"HPC를 쓴 단계 {len(hpc)}개, 겹친 쌍 없음", pairs=[], hpc_steps=hpc)


def ledger_times(ledger: Path) -> list[tuple[float, str | None]]:
    db = ro(ledger)
    try:
        return [(float(at), rid) for at, rid in db.execute("SELECT at, request_id FROM logins ORDER BY at")]
    finally:
        db.close()


def check_session(tasks: list[dict], logins: list[tuple[float, str | None]]) -> dict:
    pairs = overlapping_pairs(tasks)
    if not pairs:
        return result("session", "FAIL", "겹친 HPC 단계가 없어 세션 공유를 판정할 수 없습니다")
    worst = None
    for a, b in pairs:
        # The overlap itself: from the later start to the earlier end (a step still running ends now).
        starts = [x for x in (a["start_ts"], b["start_ts"]) if x is not None]
        if not starts:
            continue
        start = max(starts)
        end = min(x if x is not None else time.time() for x in (a["end_ts"], b["end_ts"]))
        n = sum(1 for at, _ in logins if start <= at <= end)
        if worst is None or n > worst["logins"]:
            worst = {"steps": [a["step_id"], b["step_id"]], "from": start, "to": end, "logins": n}
    if worst is None:
        return result("session", "FAIL", "겹친 단계의 시작 시각이 없어 판정할 수 없습니다(--watch 기록 필요)")
    ok = worst["logins"] <= SESSION_MAX
    return result("session", "PASS" if ok else "FAIL",
                  f"겹친 단계가 돈 동안 로그인 최대 {worst['logins']}회(상한 {SESSION_MAX})", worst=worst)


def check_window(logins: list[tuple[float, str | None]], span: tuple[float, float]) -> dict:
    lo, hi = span[0] - WINDOW_S, span[1] + WINDOW_S
    times = sorted(at for at, _ in logins if lo <= at <= hi)
    peak, at_peak, j = 0, None, 0
    for i, t in enumerate(times):
        while times[j] <= t - WINDOW_S:
            j += 1
        if i - j + 1 > peak:
            peak, at_peak = i - j + 1, t
    ok = peak <= WINDOW_MAX
    return result("window", "PASS" if ok else "FAIL", f"60분 창 최대 {peak}회(상한 {WINDOW_MAX}), 범위 안 로그인 {len(times)}회",
                  peak=peak, peak_at=at_peak, logins=len(times))


# ----- reproduction (scDRS cell type-trait table) -----
TABLE = Path(__file__).with_name("scdrs_1331_table.tsv")
NEED_ASSOC = 15     # of 19 expected associations
MAX_NONE_HITS = 1   # of 5 expected non-associations
MIN_PAIRS = 7000    # the run covers (nearly) all 74 traits x 120 cell types, so BH matches the paper's scale
Q = 0.05
PAPER = re.compile(r"scdrs|s41588-022-01167|36050550|martinjzhang", re.I)


def norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(text).lower()).strip("_")


def load_table(path: Path = TABLE) -> list[dict]:
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line and not line.startswith("#")]
    return list(csv.DictReader(lines, delimiter="\t"))


def _rows(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    if not text:
        return []
    sep = "\t" if text[0].count("\t") >= text[0].count(",") else ","
    return list(csv.DictReader(text, delimiter=sep))


def read_assoc(paths: list[Path]) -> dict[tuple[str, str], float]:
    """{(trait, cell type): assoc_mcp}, both normalized. Reads scDRS ``<trait>.scdrs_group.<column>`` files (first
    column = cell type) and combined tables with trait, cell_type (or group) and assoc_mcp columns."""
    out: dict[tuple[str, str], float] = {}
    for path in paths:
        for row in _rows(path):
            low = {norm(k): v for k, v in row.items() if k is not None}
            p = low.get("assoc_mcp")
            if p in (None, "", "NA", "nan"):
                continue
            if ".scdrs_group." in path.name:
                trait = path.name.split(".scdrs_group.")[0]
                cell = next(iter(row.values()))  # the unnamed index column
            else:
                trait = low.get("trait")
                cell = low.get("cell_type") or low.get("group") or low.get("celltype")
            if not trait or not cell:
                continue
            try:
                out[(norm(trait), norm(cell))] = float(p)
            except ValueError:
                continue
    return out


def find_assoc_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    named = [p for p in root.rglob("*") if p.is_file() and ".scdrs_group." in p.name]
    combined = [p for p in root.rglob("celltype_assoc.*") if p.is_file() and p.suffix in {".tsv", ".csv"}]
    return named or combined


def bh(pvalues: dict) -> dict:
    """Benjamini-Hochberg q for every key."""
    items = sorted(pvalues.items(), key=lambda kv: kv[1])
    n = len(items)
    q, running = {}, 1.0
    for rank in range(n, 0, -1):
        key, p = items[rank - 1]
        running = min(running, p * n / rank)
        q[key] = running
    return q


def check_repro(paths: list[Path], table: list[dict]) -> dict:
    assoc = read_assoc(paths)
    if not assoc:
        return result("repro", "FAIL", "scDRS 세포 유형 연관 결과(assoc_mcp)를 찾지 못했습니다", files=[str(p) for p in paths])
    q = bh(assoc)
    rows, got_assoc, none_hits, missing = [], 0, 0, 0
    for row in table:
        key = (norm(row["trait"]), norm(row["cell_type"]))
        mine = q.get(key)
        hit = mine is not None and mine < Q
        missing += mine is None
        if row["expect"] == "assoc":
            got_assoc += hit
        else:
            none_hits += hit
        rows.append({"label": row["label"], "expect": row["expect"], "paper_fdr": float(row["paper_assoc_fdr"]),
                     "run_p": assoc.get(key), "run_q": mine, "reproduced": hit if row["expect"] == "assoc" else not hit})
    n_assoc = sum(r["expect"] == "assoc" for r in table)
    n_none = len(table) - n_assoc
    ok = got_assoc >= NEED_ASSOC and none_hits <= MAX_NONE_HITS and len(assoc) >= MIN_PAIRS
    return result("repro", "PASS" if ok else "FAIL",
                  f"연관 {got_assoc}/{n_assoc}(기준 {NEED_ASSOC}), 비연관인데 유의 {none_hits}/{n_none}(상한 {MAX_NONE_HITS}), "
                  f"전체 쌍 {len(assoc)}(하한 {MIN_PAIRS}), 표에 없는 쌍 {missing}", pairs=rows, n_pairs=len(assoc),
                  files=[str(p) for p in paths[:5]] + ([f"… {len(paths) - 5} more"] if len(paths) > 5 else []))


# ----- Paper2Agent hire -----
def request_state(db_path: Path, request_id: str) -> dict:
    db = ro(db_path)
    try:
        row = db.execute("SELECT body FROM state WHERE kind = 'request' AND key = ?", (request_id,)).fetchone()
    finally:
        db.close()
    return json.loads(row[0]) if row else {}


def check_hire(req: dict, tasks: list[dict]) -> dict:
    """An active Paper2Agent contract hire for the scDRS paper, assigned to its steps, that ran at least one of them."""
    plan = req.get("plan") or {}
    hires = [r for r in plan.get("recruit_results") or []
             if r.get("status") == "active" and r.get("agent_id") and PAPER.search(json.dumps(r, ensure_ascii=False))]
    if not hires:
        seen = [{k: r.get(k) for k in ("paper", "status", "error", "agent_id")} for r in plan.get("recruit_results") or []]
        return result("hire", "FAIL", "scDRS 논문의 활성 파견직 채용 기록이 없습니다", recruit_results=seen)
    steps = {s.get("id"): s for s in plan.get("steps") or []}
    detail = []
    for hire in hires:
        aid, sids = hire["agent_id"], list(hire.get("step_ids") or [])
        assigned = [sid for sid in sids if (steps.get(sid) or {}).get("agent_id") == aid]
        ran = sorted({t["step_id"] for t in tasks if t["step_id"] in sids and t["agent_id"] == aid
                      and t["end_seq"] is not None})
        others = sorted({t["agent_id"] for t in tasks if t["step_id"] in sids and t["agent_id"] != aid})
        detail.append({"agent_id": aid, "step_ids": sids, "assigned": assigned, "ran": ran, "other_agents": others})
        if sids and len(assigned) == len(sids) and ran and not others:
            return result("hire", "PASS", f"파견직 {aid}가 단계 {len(ran)}/{len(sids)}개를 수행", hires=detail)
    return result("hire", "FAIL", "파견직이 있으나 scDRS 단계를 맡지 않았거나 다른 직원이 수행했습니다", hires=detail)


# ----- verify and public -----
def check_verify(labhq_cmd: list[str], config: str, request_id: str) -> dict:
    try:
        proc = subprocess.run([*labhq_cmd, "-c", config, "verify", request_id, "--json"], capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=900)
    except (OSError, subprocess.TimeoutExpired) as error:
        return result("verify", "FAIL", f"labhq verify를 실행하지 못했습니다: {error}")
    tail = (proc.stdout or proc.stderr or "").strip().splitlines()[-5:]
    return result("verify", "PASS" if proc.returncode == 0 else "FAIL", f"labhq verify exit {proc.returncode}",
                  exit=proc.returncode, tail=tail)


SSH_VALUE_OPTS = set("BbcDEeFIiJLlmOoPpQRSWw")  # ssh options that take the next word as their value


def argv_terms(argv: list[str]) -> set[str]:
    """Login names and hosts in a connect command: ``user@host`` words, ``-l user``, ``-J jump``, ``-o User=/HostName=``
    and the destination (the first word that is not an option or an option's value). Ports and key files are left out:
    a port number would match ordinary numbers in a report."""
    terms: set[str] = set()

    def add(word: str) -> None:
        for part in re.split(r"[@,:]", word):
            if part and not part.isdigit():
                terms.add(part)

    words, i, destination = list(argv[1:]), 0, None
    while i < len(words):
        word = words[i]
        if word.startswith("-") and len(word) >= 2 and word[1] in SSH_VALUE_OPTS:
            flag, value = word[1], word[2:] or (words[i + 1] if i + 1 < len(words) else "")
            i += 1 if word[2:] else 2
            if flag in "lJ":
                add(value)
            elif flag == "o" and "=" in value and value.split("=", 1)[0].lower() in {"user", "hostname", "proxyjump"}:
                add(value.split("=", 1)[1])
            continue
        if word.startswith("-"):
            i += 1
            continue
        if destination is None:
            destination = word
            add(word)
        elif "@" in word:
            add(word)
        i += 1
    return terms


def site_terms(settings, extra: Path | None) -> list[str]:
    terms: set[str] = set()
    hpc = settings.hpc
    for value in (hpc.ssh_host, hpc.user, hpc.default_queue):
        if value:
            terms.add(str(value))
    if hpc.site_profile:
        from labhq.hpc_site import load_profile
        profile = load_profile(hpc.site_profile)
        for value in (profile.remote_workdir, profile.queue):
            if value:
                terms.add(value)
        terms.update(argv_terms(profile.connect_argv))
    if extra:
        terms.update(line.strip() for line in extra.read_text(encoding="utf-8").splitlines() if line.strip())
    return sorted(t for t in terms if len(t) >= 4)


def check_public(roots: list[Path], terms: list[str]) -> dict:
    if not terms:
        return result("public", "SKIP", "사이트 용어가 없어 검사하지 않았습니다(--site-terms 또는 hpc 설정 필요)")
    hits = {}
    scanned = 0
    for root in roots:
        if not root.exists():
            continue
        files = [root] if root.is_file() else [p for p in root.rglob("*") if p.is_file()]
        for path in files:
            if path.suffix.lower() not in TEXT_SUFFIXES or path.stat().st_size > 20 << 20:
                continue
            scanned += 1
            text = path.read_text(encoding="utf-8", errors="replace")
            n = sum(text.count(t) for t in terms)
            if n:
                hits[str(path)] = n
    ok = not hits
    return result("public", "PASS" if ok else "FAIL",
                  f"텍스트 {scanned}개 검사, 사이트 용어 {len(terms)}개, 걸린 파일 {len(hits)}개", files=hits)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--config", required=True, help="the instance's labhq.yaml")
    ap.add_argument("--request", required=True, help="request id (req_…)")
    ap.add_argument("--assoc", action="append", type=Path, default=[],
                    help="scDRS *.scdrs_group.* files or a celltype_assoc table; default: search the request bundle")
    ap.add_argument("--table", type=Path, default=TABLE, help="the fixed reproduction table")
    ap.add_argument("--hpc-step", action="append", default=[], help="step or task id to count as HPC-using")
    ap.add_argument("--site-terms", type=Path, help="local file, one extra site term per line (never committed)")
    ap.add_argument("--labhq", default=None, help="labhq command (default: this python -m labhq.cli)")
    ap.add_argument("--skip", action="append", default=[], choices=["repro", "hire", "overlap", "session", "window", "verify", "public"])
    ap.add_argument("--json", type=Path, help="also write the full result here")
    ap.add_argument("--events-log", type=Path, help="JSONL written by --watch (local; read by the verdict too)")
    ap.add_argument("--watch", action="store_true", help="record the request's step events until it ends, then exit")
    ap.add_argument("--every", type=float, default=10.0, help="--watch poll seconds")
    ap.add_argument("--hours", type=float, default=24.0, help="--watch gives up after this many hours")
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # -I ignores PYTHONUTF8; Korean lines stay readable

    from labhq.settings import Settings
    settings = Settings.load(args.config)
    db = settings.path(settings.gateway.state_dir) / "gateway.sqlite3"
    ledger = Path(settings.hpc._login_ledger) if settings.hpc._login_ledger else None
    bundle = settings.path(settings.runner.workspace_root) / "requests" / args.request
    labhq_cmd = args.labhq.split() if args.labhq else [sys.executable, "-m", "labhq.cli"]
    if args.watch:
        if not args.events_log:
            ap.error("--watch needs --events-log")
        return watch(db, args.request, args.events_log, args.every, time.time() + args.hours * 3600)

    checks: list[dict] = []
    if "repro" not in args.skip:
        files = []
        for item in args.assoc:
            files += find_assoc_files(item) if item.is_dir() else [item]
        checks.append(check_repro(files or find_assoc_files(bundle), load_table(args.table)))
    tasks = step_intervals(db, args.request, set(args.hpc_step), args.events_log)
    if "hire" not in args.skip:
        checks.append(check_hire(request_state(db, args.request), tasks))
    logins = ledger_times(ledger) if ledger and ledger.exists() else []
    if "overlap" not in args.skip:
        checks.append(check_overlap(tasks))
    if "session" not in args.skip:
        checks.append(check_session(tasks, logins) if ledger else result("session", "FAIL", "로그인 기록 파일이 없습니다"))
    if "window" not in args.skip:
        starts = [t["start_ts"] for t in tasks if t["start_ts"]]
        ends = [t["end_ts"] for t in tasks if t["end_ts"]] or [time.time()]
        span = (min(starts), max(ends)) if starts else (time.time() - 86400, time.time())
        checks.append(check_window(logins, span) if ledger else result("window", "FAIL", "로그인 기록 파일이 없습니다"))
    if "verify" not in args.skip:
        checks.append(check_verify(labhq_cmd, args.config, args.request))
    if "public" not in args.skip:
        checks.append(check_public([bundle], site_terms(settings, args.site_terms)))

    for c in checks:
        print(f"{c['status']:4}  {c['check']:8} {c['detail']}")
    passed = all(c["status"] == "PASS" for c in checks)
    print("판정:", "PASS" if passed else "FAIL", f"({sum(c['status'] == 'PASS' for c in checks)}/{len(checks)})",
          "· 건너뜀: " + ",".join(args.skip) if args.skip else "")
    if args.json:
        args.json.write_text(json.dumps({"request": args.request, "passed": passed, "checks": checks},
                                        ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
