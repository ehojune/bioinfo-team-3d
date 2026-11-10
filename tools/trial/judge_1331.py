"""Judge the #1331 trial: airway RNA-seq (GSE52778) run by several staff on one HPC node.

Five checks, each PASS/FAIL/SKIP with its evidence; exit 0 only when every check that ran passed and none was skipped
without --allow-skip.

  genes     DUSP1, KLF15, PER1, TSC22D3, ZBTB16, FKBP5: at least 5 up (log2FC > 0) with padj < 0.05 (Himes 2014).
  overlap   two or more HPC-using staff steps of the request ran at the same time.
  session   while those steps overlapped, at most one HPC login was recorded (they shared one session).
  window    no 60-minute window holds more than 2 HPC logins (whole ledger around the request).
  verify    `labhq verify <request>` exits 0.
  public    no site term (host, user, remote path, queue) appears in the request's report or bundle text files.

Start ``--watch`` right after the request is created: the events table keeps only recent events, so
dispatch/result times are recorded while the run goes (see read_events). Read only: the state database and the login ledger are opened with ``mode=ro``. Site terms are never printed, only
which file held how many. Run it with the instance's python so ``labhq`` imports:

    python -I tools/trial/judge_1331.py --config <labhq.yaml> --request <req_id> [--de <table>] [--json <out>]
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

GENES = {  # symbol -> Ensembl gene id (GRCh38; version suffix ignored)
    "DUSP1": "ENSG00000120129", "KLF15": "ENSG00000163884", "PER1": "ENSG00000179094",
    "TSC22D3": "ENSG00000157514", "ZBTB16": "ENSG00000109906", "FKBP5": "ENSG00000096060",
}
NEED_GENES = 5
MIN_ROWS = 10000  # a genome-wide table, not six hand-picked rows
PADJ = 0.05
WINDOW_S = 3600
WINDOW_MAX = 2
SESSION_MAX = 1
HPC_TOOL = re.compile(r"\bhpc_[a-z_]+\b|mcp__[A-Za-z0-9_-]*hpc[A-Za-z0-9_-]*")
FC_NAMES = ("log2foldchange", "log2fc", "logfc", "log2_fold_change")
PADJ_NAMES = ("padj", "fdr", "adj.p.val", "adj_p_val", "qvalue", "q_value", "p_adj", "adj_pval")
BINARY_SUFFIXES = {".gz", ".bgz", ".zip", ".bz2", ".xz", ".zst", ".bam", ".cram", ".bai", ".png", ".jpg", ".jpeg", ".gif",
                   ".pdf", ".h5", ".h5ad", ".rds", ".sqlite3", ".db"}


def is_text(path: Path) -> bool:
    """By content, not by name: scheduler logs (.out/.err), notebooks and files without a suffix are text too. A file
    with a NUL byte in its first 8 KiB is binary; known compressed and image formats are skipped by suffix."""
    if path.suffix.lower() in BINARY_SUFFIXES:
        return False
    try:
        with path.open("rb") as fh:
            return b"\0" not in fh.read(8192)
    except OSError:
        return False


def result(name: str, status: str, detail: str, **evidence) -> dict:
    return {"check": name, "status": status, "detail": detail, **evidence}


# ----- genes -----
def _pick(header: list[str], names: tuple[str, ...]) -> int | None:
    low = [h.strip().strip('"').lower() for h in header]
    for name in names:
        if name in low:
            return low.index(name)
    return None


def read_de(path: Path) -> dict[str, tuple[float, float]]:
    """{symbol: (log2FC, padj)} for the six genes found in a CSV/TSV DE table. A row matches on any cell equal to the
    symbol or the Ensembl id (version stripped); NA values are skipped."""
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    dialect = "excel-tab" if text.split("\n", 1)[0].count("\t") > text.split("\n", 1)[0].count(",") else "excel"
    rows = list(csv.reader(text.splitlines(), dialect=dialect))
    if not rows:
        return {}
    header = rows[0]
    fc, padj = _pick(header, FC_NAMES), _pick(header, PADJ_NAMES)
    if fc is None or padj is None:
        return {}
    by_key = {}
    for symbol, ensg in GENES.items():
        by_key[symbol.upper()] = symbol
        by_key[ensg] = symbol
    found: dict[str, tuple[float, float]] = {}
    for row in rows[1:]:
        if len(row) <= max(fc, padj):
            continue
        symbol = None
        for cell in row:
            key = cell.strip().strip('"').split(".")[0].upper()
            if key in by_key:
                symbol = by_key[key]
                break
        if symbol is None or symbol in found:
            continue
        try:
            found[symbol] = (float(row[fc]), float(row[padj]))
        except ValueError:
            continue
    return found


def find_de_tables(root: Path) -> list[Path]:
    """CSV/TSV files under ``root`` whose header has a fold-change and an adjusted-p column."""
    out = []
    for path in sorted(root.rglob("*")):
        if path.suffix.lower() not in {".csv", ".tsv", ".txt"} or not path.is_file() or path.stat().st_size > 200 << 20:
            continue
        try:
            with path.open(encoding="utf-8-sig", errors="replace") as fh:
                first = fh.readline()
        except OSError:
            continue
        header = re.split(r"[\t,]", first)
        if _pick(header, FC_NAMES) is not None and _pick(header, PADJ_NAMES) is not None:
            out.append(path)
    return out


def check_genes(tables: list[Path]) -> dict:
    if not tables:
        return result("genes", "FAIL", "DE 표(log2FC·padj 열)를 찾지 못했습니다")
    best, best_rank = None, None
    for table in tables:
        found = read_de(table)
        up = sorted(s for s, (fc, p) in found.items() if fc > 0 and p < PADJ)
        with table.open(encoding="utf-8-sig", errors="replace") as fh:
            n_rows = max(sum(1 for _ in fh) - 1, 0)
        row = {"table": str(table), "rows": n_rows, "up": up, "values": {s: {"log2fc": v[0], "padj": v[1]} for s, v in found.items()},
               "missing": sorted(set(GENES) - set(found))}
        rank = (len(up) >= NEED_GENES and n_rows >= MIN_ROWS, len(up), n_rows)
        if best is None or rank > best_rank:
            best, best_rank = row, rank
    ok = len(best["up"]) >= NEED_GENES and best["rows"] >= MIN_ROWS
    down = sorted(s for s, v in best["values"].items() if v["log2fc"] < 0 and v["padj"] < PADJ)
    hint = " (유의하게 하향인 유전자가 많으면 대조 방향이 dex 대 untreated의 반대인지 확인)" if len(down) >= NEED_GENES else ""
    return result("genes", "PASS" if ok else "FAIL", f"6개 중 {len(best['up'])}개 상향·padj<{PADJ}, 표 {best['rows']}행(하한 {MIN_ROWS}){hint}", **best)


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


def file_intervals(steps_dir: Path, forced: set[str]) -> list[dict]:
    """The same intervals from the runner's per-step ``events.jsonl`` files, for a run whose dispatch/result events the
    gateway no longer keeps and that was not watched. The step id is the folder name; a task starts at its first event
    and ends at its ``task.result``; it used HPC if one of its events names an HPC tool. Times stand in for seq."""
    tasks: dict[str, dict] = {}
    for events in sorted(steps_dir.glob("*/events.jsonl")):
        step = events.parent.name
        for line in events.read_text(encoding="utf-8").splitlines():
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            tid, ts = ev.get("task_id"), ev.get("ts")
            if not tid or not isinstance(ts, (int, float)):
                continue
            task = tasks.setdefault(tid, {"task_id": tid, "agent_id": ev.get("agent_id"), "step_id": step,
                                          "attempt": None, "start_seq": float(ts), "start_ts": float(ts),
                                          "end_seq": None, "end_ts": None, "hpc": False})
            if ev.get("type") == "task.result" and task["end_seq"] is None:
                task["end_seq"] = task["end_ts"] = float(ts)
            if ev.get("type") != "task.result" and HPC_TOOL.search(line):
                task["hpc"] = True
    for task in tasks.values():
        task["hpc"] = task["hpc"] or task["step_id"] in forced or task["task_id"] in forced
    return sorted(tasks.values(), key=lambda t: t["start_seq"])


def prefer_complete(tasks: list[dict], workspace_root: Path, bundle: Path, request_id: str, forced: set[str]) -> list[dict]:
    """The gateway's intervals when they cover the whole request, else the step files'. Retention can cut a request in
    the middle (early steps gone, a late one still complete), so the test is per task: every task the step files know
    must have a dispatch and a result in the gateway's view."""
    steps = next((d for d in (workspace_root / "hpc" / request_id / "steps", bundle / "steps")
                  if any(d.glob("*/events.jsonl"))), None)
    if steps is None:
        return tasks
    from_files = file_intervals(steps, forced)
    complete = {t["task_id"] for t in tasks if t["end_seq"] is not None}
    if all(t["task_id"] in complete for t in from_files):
        return tasks
    print(f"단계 시간: gateway 이벤트가 요청 전체를 담고 있지 않아 단계별 events.jsonl에서 읽습니다 ({steps.parent.name})")
    return from_files


def overlapping_pairs(tasks: list[dict]) -> list[tuple[dict, dict]]:
    hpc = [t for t in tasks if t["hpc"]]
    pairs = []
    for i, a in enumerate(hpc):
        for b in hpc[i + 1:]:
            same_step = a["step_id"] is not None and a["step_id"] == b["step_id"]  # an unknown step is not the same step
            if same_step or a["agent_id"] == b["agent_id"]:
                continue  # a retry, or one employee with two steps, is not two staff working at once
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
        # From the first of the two steps' start to the end of their overlap (a step still running ends now). One
        # shared session is at most the one login that opened it; a second session opened by either step, before or
        # during the overlap, is a second login here. A session opened before both steps started (0 here) is shared
        # by definition. The ledger holds no session id, so this is the strongest proof the record allows.
        starts = [x for x in (a["start_ts"], b["start_ts"]) if x is not None]
        if not starts:
            continue
        start = min(starts)
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


LOOPBACK = {"127.0.0.1", "localhost", "::1"}  # a tunnel's local end names no site


def argv_terms(argv: list[str]) -> set[str]:
    """Login names and hosts in a connect command: ``user@host`` words, ``-l user``, ``-J jump``, ``-o User=/HostName=``
    and the destination (the first word that is not an option or an option's value). Ports and key files are left out:
    a port number would match ordinary numbers in a report."""
    terms: set[str] = set()

    def add(word: str) -> None:
        for part in re.split(r"[@,:]", word):
            if part and not part.isdigit() and part.lower() not in LOOPBACK:
                terms.add(part)

    words, i, destination = list(argv[1:]), 0, None
    while i < len(words):
        word = words[i]
        if word.startswith("-") and len(word) >= 2 and word[1] in SSH_VALUE_OPTS:
            flag, value = word[1], word[2:] or (words[i + 1] if i + 1 < len(words) else "")
            i += 1 if word[2:] else 2
            if flag in "lJ":
                add(value)
            elif flag == "o":  # "-o User=alice" or "-o 'User alice'": both are valid OpenSSH forms
                key, _, rest = value.replace("=", " ", 1).partition(" ")
                if key.lower() in {"user", "hostname", "proxyjump"} and rest.strip():
                    add(rest.strip())
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


def node_terms(words: list[str]) -> set[str]:
    """Node names in scheduler arguments: ``-w n1``, ``--nodelist=n1``, ``-l h=n1``/``hostname=n1``, ``queue@n1``."""
    terms: set[str] = set()
    words = [str(w) for w in words]
    for i, word in enumerate(words):
        if word in {"-w", "--nodelist"} and i + 1 < len(words):
            terms.update(x for x in re.split(r"[,\[\]]", words[i + 1]) if x)
        for m in re.finditer(r"(?:^|[,:\s])(?:--nodelist|h|hostname|host)=([^:\s]+)", word):
            terms.update(x for x in re.split(r"[,+|]", m.group(1)) if x)
        if "@" in word and not word.startswith("-"):
            terms.add(word.split("@", 1)[1])
    return terms


GENERIC = {"ssh", "sge", "pbs", "slurm", "mock", "none", "scp", "sftp", "rsync", "gateway_tunnel", "gateway_window",
           "vpn", "other", "true", "false"}
PROSE = {"notes", "file_transfer_note", "port_discovery", "tunnel_texts"}  # sentences, not identifiers
# A command line, read by argv_terms (login names and hosts only): split as plain values, its ssh options
# ("-o BatchMode=yes" gave "BatchMode" and "yes") matched every report (4th run's public check).
ARGV_FIELDS = {"connect_argv"}
NODE_FIELD = "node"


def value_terms(value) -> set[str]:
    """Identifiers inside one setting value: split on space, '=', '@', ',' and ':', dropping option names, numbers,
    templates and generic words. A path stays whole."""
    words = value if isinstance(value, (list, tuple)) else [value]
    terms: set[str] = set()
    for word in words:
        if word is None or isinstance(word, (bool, int, float)):
            continue
        text = str(word)
        # A Windows path stays whole wherever it sits (a value, or an option's value like --config=C:/x): splitting
        # "C:/Users/..." on ':' left the drive letter "C", which matched every report (3rd run's public check). The
        # rest is split as before, so an option's Unix path (--chdir=/x) and short site identifiers still count.
        drives = re.findall(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/][^\s,@=]*", text)
        for path in drives:
            terms.add(path)
            text = text.replace(path, " ")
        parts = re.split(r"[\s=@,:]+", text)
        for part in parts:
            if (part and not part.startswith("-") and not part.isdigit() and "{" not in part
                    and part.lower() not in GENERIC):
                terms.add(part)
    return terms


def model_terms(model, nodes: set[str]) -> set[str]:
    """Every value the user set in a settings model (one that differs from the model's default), recursively. A value
    left at its default is labhq's own text, not the site's. Fields whose name holds "node" also go to ``nodes``."""
    terms: set[str] = set()
    try:
        defaults = type(model)()
    except Exception:  # a model with required fields (a site profile): every value is the user's
        defaults = None
    for name in type(model).model_fields:
        if name in PROSE or name in ARGV_FIELDS:
            continue
        value = getattr(model, name, None)
        default = getattr(defaults, name, None) if defaults is not None else object()
        if hasattr(type(value), "model_fields"):
            terms |= model_terms(value, nodes)
            continue
        if isinstance(value, (list, tuple)) and isinstance(default, (list, tuple)):
            value = [v for v in value if v not in default]
        elif value == default:
            continue
        found = value_terms(value)
        terms |= found
        if NODE_FIELD in name.lower():
            nodes |= found
    return terms


def site_terms(settings, extra: Path | None, nodes: list[str] | None = None) -> tuple[list[str], list[str]]:
    """(every site term, the node terms among them).

    Terms are every value the user set under ``hpc`` and in the site profile (hosts, accounts, queues, scheduler
    arguments such as an account or QOS, paths), the account the scheduler falls back to when ``hpc.user`` is empty,
    the login names and hosts of the connect command, ``--site-terms`` and
    ``--node``. Nodes come from --node, node-selection scheduler arguments and fields named "node"; the public check
    refuses to pass without one, since the node is the site detail a report is most likely to name."""
    hpc = settings.hpc
    found_nodes: set[str] = set(nodes or [])
    terms = model_terms(hpc, found_nodes)
    if not hpc.user:  # the scheduler falls back to this PC's account (labhq/tools/scheduler.py)
        import getpass
        terms.add(getpass.getuser())
    found_nodes |= node_terms([*hpc.submit_prefix, *hpc.slurm.sbatch_args, hpc.pbs.resource_template,
                               hpc.default_queue or ""])
    if hpc.site_profile:
        from labhq.hpc_site import load_profile
        profile = load_profile(hpc.site_profile)
        terms |= model_terms(profile, found_nodes)
        terms |= argv_terms(profile.connect_argv)
    if extra:
        terms.update(line.strip() for line in extra.read_text(encoding="utf-8").splitlines() if line.strip())
    terms |= found_nodes
    return sorted(t for t in terms if t.strip()), sorted(found_nodes)


def term_pattern(term: str) -> re.Pattern:
    """The term as a whole word: no letter, digit or underscore right before or after it, so a short account name does
    not match inside ordinary words, and a path still matches where it is written. Case is ignored: host and node
    names are written in either case."""
    return re.compile(r"(?<![A-Za-z0-9_])" + re.escape(term) + r"(?![A-Za-z0-9_])", re.I)


def check_public(roots: list[Path], terms: list[str], nodes: list[str] | None = None) -> dict:
    if not terms:
        return result("public", "SKIP", "사이트 용어가 없어 검사하지 않았습니다(--site-terms 또는 hpc 설정 필요)")
    if nodes is not None and not nodes:
        return result("public", "FAIL", "고정 노드 이름을 설정에서 찾지 못했습니다. --node로 지정하세요")
    patterns = [term_pattern(t) for t in terms]
    hits = {}
    scanned = 0
    for root in roots:
        if not root.exists():
            continue
        files = [root] if root.is_file() else [p for p in root.rglob("*") if p.is_file()]
        for path in files:
            if not is_text(path):
                continue
            scanned += 1
            n = 0
            with path.open(encoding="utf-8", errors="replace") as fh:  # line by line: a large job log is read whole
                for line in fh:
                    n += sum(len(pat.findall(line)) for pat in patterns)
            if n:
                hits[str(path)] = n
    if scanned == 0:
        return result("public", "FAIL", "검사할 보고서·묶음 텍스트가 없습니다(요청 묶음이 아직 없거나 경로가 다름)",
                      roots=[str(r) for r in roots])
    ok = not hits
    return result("public", "PASS" if ok else "FAIL",
                  f"텍스트 {scanned}개 검사, 사이트 용어 {len(terms)}개, 걸린 파일 {len(hits)}개", files=hits)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--config", required=True, help="the instance's labhq.yaml")
    ap.add_argument("--request", required=True, help="request id (req_…)")
    ap.add_argument("--de", action="append", type=Path, default=[], help="DE table; default: search the request bundle")
    ap.add_argument("--hpc-step", action="append", default=[], help="step or task id to count as HPC-using")
    ap.add_argument("--node", action="append", default=[], help="the pinned compute node (local; never committed)")
    ap.add_argument("--site-terms", type=Path, help="local file, one extra site term per line (never committed)")
    ap.add_argument("--labhq", default=None, help="labhq command (default: this python -m labhq.cli)")
    ap.add_argument("--skip", action="append", default=[], choices=["genes", "overlap", "session", "window", "verify", "public"])
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
    if "genes" not in args.skip:
        checks.append(check_genes(args.de or find_de_tables(bundle)))
    tasks = step_intervals(db, args.request, set(args.hpc_step), args.events_log)
    tasks = prefer_complete(tasks, settings.path(settings.runner.workspace_root), bundle, args.request,
                            set(args.hpc_step))
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
        terms, nodes = site_terms(settings, args.site_terms, args.node)
        checks.append(check_public([bundle], terms, nodes))

    for c in checks:
        print(f"{c['status']:4}  {c['check']:8} {c['detail']}")
    # A run with a skipped check is never a trial PASS: it is INCOMPLETE (exit 2) even when every check that ran passed.
    passed = all(c["status"] == "PASS" for c in checks)
    verdict = "FAIL" if not passed else "INCOMPLETE" if args.skip else "PASS"
    print("판정:", verdict, f"({sum(c['status'] == 'PASS' for c in checks)}/{len(checks)})",
          "· 건너뜀: " + ",".join(args.skip) if args.skip else "")
    if args.json:
        args.json.write_text(json.dumps({"request": args.request, "verdict": verdict, "passed": verdict == "PASS",
                                         "skipped": args.skip, "checks": checks},
                                        ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return {"PASS": 0, "FAIL": 1, "INCOMPLETE": 2}[verdict]


if __name__ == "__main__":
    sys.exit(main())
