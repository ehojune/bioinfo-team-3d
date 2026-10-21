"""Remote-only synthetic transport experiment; never WMI or a role launch.

Both modes share the receiver prefix/framing; only Write-Phase's body differs.
Checkpoints perturb timing. ReadBytes return includes temporary PowerShell result
capture; the following typed assignment is separate. Literal mode still invokes
ConvertFrom-Json, so cmdlet initialization may move to parsing, not disappear.
Completion proves only this probe. The workflow's 3-minute step bound covers
Windows run's untimed write/drain gaps. Fixed-order single observations cannot
separate formatter effects from scheduling/cache changes. Phase and caller clocks
have different origins; early host startup precedes the receiver stopwatch.
"""
from __future__ import annotations

import base64
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from labhq import windows_broker as broker

MODES = ("original", "literal")
SIZES = (1024, 65536)  # JSON bytes, excluding the unchanged four-byte header.
ARTIFACT = Path("transport-probe825.json")
LITERAL_BODY = '''  $ms = $clock.Elapsed.TotalMilliseconds.ToString('F3', [Globalization.CultureInfo]::InvariantCulture)
  [Console]::Out.WriteLine('{"phase":"' + $phase + '","elapsed_ms":' + $ms + '}')
  [Console]::Out.Flush()
'''


def _format_phases(name):
    return ["formatter_enter_" + name, name, "formatter_return_" + name]


PHASES = tuple(_format_phases("powershell_started") + ["stream_open_enter", "stream_open_return"]
    + _format_phases("stdin_open") + ["header_read_enter"] + _format_phases("header_read")
    + ["payload_read_enter", "payload_read_return", "payload_assigned"]
    + _format_phases("input_read") + ["json_complete"] + _format_phases("input_accepted")
    + ["probe_complete"])


def require_remote():
    if sys.platform != "win32" or os.environ.get("GITHUB_ACTIONS") != "true":
        raise RuntimeError("remote_only")


def receiver(mode):
    if mode not in MODES:
        raise ValueError("unknown_mode")
    source = broker.WMI_SCRIPT
    cut = "  $startup = ([wmiclass]"
    if source.count(cut) != 1:
        raise ValueError("receiver_source_drift")
    prefix = source.split(cut, 1)[0]
    if not prefix.endswith("  Write-Phase 'input_accepted'\n"):
        raise ValueError("receiver_source_drift")
    start, tail = prefix.split("function Write-Phase([string]$phase) {\n", 1)
    original, body = tail.split("}\ntry {\n", 1)
    formatter = original if mode == "original" else LITERAL_BODY
    prefix = (start + "function Write-Checkpoint([string]$phase) {\n" + LITERAL_BODY + "}\n"
        + "function Write-Phase([string]$phase) {\n"
        + "  Write-Checkpoint ('formatter_enter_' + $phase)\n" + formatter
        + "  Write-Checkpoint ('formatter_return_' + $phase)\n}\ntry {\n" + body)
    edits = (
        ("  $stream = [Console]::OpenStandardInput()", "  Write-Checkpoint 'stream_open_enter'\n"
         "  $stream = [Console]::OpenStandardInput()\n  Write-Checkpoint 'stream_open_return'"),
        ("  $length = $reader.ReadUInt32()", "  Write-Checkpoint 'header_read_enter'\n  $length = $reader.ReadUInt32()"),
        ("  [byte[]]$payload = $reader.ReadBytes([int]$length)",
         "  Write-Checkpoint 'payload_read_enter'\n  $payloadResult = $reader.ReadBytes([int]$length)\n"
         "  Write-Checkpoint 'payload_read_return'\n  [byte[]]$payload = $payloadResult\n"
         "  Write-Checkpoint 'payload_assigned'"),
        ("  $r = $requestJson | ConvertFrom-Json", "  $r = $requestJson | ConvertFrom-Json\n  Write-Checkpoint 'json_complete'"),
    )
    for old, new in edits:
        if prefix.count(old) != 1:
            raise ValueError("receiver_source_drift")
        prefix = prefix.replace(old, new)
    script = prefix + "  Write-Checkpoint 'probe_complete'\n  exit 0\n} catch { Write-Checkpoint 'receiver_error'; exit 1 }\n"
    if any(token in script for token in ("wmiclass", "CreateInstance", "ProcessId", "runpy", "Start-Process")):
        raise ValueError("receiver_source_drift")
    return script


def synthetic_frame(size):
    if size not in SIZES:
        raise ValueError("unknown_size")
    data = {"command": "synthetic-command-never-executed", "cwd": "synthetic-cwd-never-used",
            "flags": broker.BREAKAWAY | broker.DETACHED | 0x400, "environment": ["PROBE_PAD="]}
    missing = size - (len(broker._request_frame(data)) - 4)
    if missing < 0:
        raise ValueError("synthetic_shape_error")
    data["environment"][0] += "X" * missing
    frame = broker._request_frame(data)
    if len(frame) != size + 4 or int.from_bytes(frame[:4], "little") != size or not frame[4:].isascii():
        raise ValueError("synthetic_shape_error")
    return frame


def project(output):
    """Strict ordered prefix; discard raw/malformed output and never emit values."""
    phases = []
    if not isinstance(output, bytes) or len(output) > 8192:
        return phases, "invalid"
    try:
        lines = output.decode("utf-8-sig").splitlines()
        if len(lines) > len(PHASES) + 1:
            raise ValueError
        for index, line in enumerate(lines):
            row = json.loads(line)
            if not isinstance(row, dict) or set(row) != {"phase", "elapsed_ms"}:
                raise ValueError
            phase, elapsed = row["phase"], row["elapsed_ms"]
            if (type(elapsed) not in (int, float) or not math.isfinite(elapsed) or not 0 <= elapsed <= 60000
                    or (phases and elapsed < phases[-1]["elapsed_ms"])):
                raise ValueError
            if phase == "receiver_error" and index == len(lines) - 1 and len(phases) < len(PHASES):
                phases.append({"phase": phase, "elapsed_ms": elapsed})
                return phases, "receiver_error"
            if len(phases) >= len(PHASES) or phase != PHASES[len(phases)]:
                raise ValueError
            phases.append({"phase": phase, "elapsed_ms": elapsed})
        return phases, "complete" if len(phases) == len(PHASES) else "partial"
    except (ValueError, UnicodeError, RecursionError):
        return phases, "invalid"


def run_case(mode, size):
    require_remote()
    row = {"mode": mode, "size": size, "outcome": "error", "reason": "setup_error", "phases": []}
    started = time.monotonic()
    try:
        shell = broker._powershell()
        if not shell.is_absolute() or not shell.is_file():
            return row
        script = receiver(mode)
        frame = synthetic_frame(size)
        encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
        result = subprocess.run([str(shell), "-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                                input=frame, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                timeout=10, creationflags=0x08000000, check=False)
        row["phases"], row["trace_status"] = project(result.stdout)
        if type(result.returncode) is not int or result.returncode != 0:
            row["reason"] = "receiver_exit_nonzero"
        elif row["trace_status"] != "complete":
            row["reason"] = "incomplete_or_invalid_trace"
        else:
            row.update(outcome="completed", reason="transport_only_completed")
    except subprocess.TimeoutExpired as exc:
        row["phases"], row["trace_status"] = project(exc.output)
        row.update(outcome="timeout", reason="provider_timeout")
    except (OSError, subprocess.SubprocessError):
        row["reason"] = "native_or_subprocess_error"
    except Exception:
        row["reason"] = "setup_or_protocol_error"
    finally:
        elapsed = (time.monotonic() - started) * 1000
        if math.isfinite(elapsed) and 0 <= elapsed <= 180000:
            row["elapsed_ms"] = round(elapsed, 3)
        else:
            row.update(outcome="error", reason="duration_out_of_bound")
    return row


def write_receipt(rows):
    text = json.dumps(rows, indent=2, allow_nan=False)
    if len(text) + 1 > 32768:
        raise ValueError("receipt_bound")
    temporary = ARTIFACT.with_suffix(".tmp")
    temporary.write_text(text + "\n", encoding="utf-8")
    temporary.replace(ARTIFACT)


def main():
    try:
        require_remote()
    except RuntimeError:
        print("remote_only", file=sys.stderr)
        return 2
    try:
        rows = [{"mode": mode, "size": size, "outcome": "not_run", "reason": "not_started", "phases": []}
                for mode in MODES for size in SIZES]
        write_receipt(rows)
        for index, row in enumerate(rows):
            row.update(outcome="started", reason="in_progress")
            write_receipt(rows)
            rows[index] = run_case(row["mode"], row["size"])
            write_receipt(rows)
        return 0 if all(row["outcome"] == "completed" for row in rows) else 1
    except Exception:
        print("probe_receipt_error", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
