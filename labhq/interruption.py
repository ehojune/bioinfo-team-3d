"""Shutdown classification shared by runner, orchestration and PI corrections."""
import json
import re
import signal
from datetime import datetime


class ShutdownInterrupted(Exception):
    """A runner stopped; the gateway caller still owns its state and retry policy."""
    def __init__(self, evidence):
        self.evidence = evidence
        super().__init__("task interrupted by shutdown")


def shutdown_interruption(*, exit_code=None, error=None, stopping=False, cancelled=False) -> bool:
    """A signal-shaped exit alone is not evidence that labhq was shutting down."""
    if exit_code is None:
        match = re.search(r"\bexit(?: code)?\s+(-?\d+)\b", error or "")
        exit_code = int(match[1]) if match else None
    # HUP, INT, QUIT, KILL, TERM; Windows TerminateProcess(1), CONTROL_C_EXIT,
    # and STATUS_PROCESS_IS_TERMINATING (both signed and unsigned wire forms).
    codes = {1, 0xC000013A, -1073741510, 0xC000010A, -1073741558}
    codes.update(-n for n in (1, 2, 3, 9, 15))
    codes.update(128 + n for n in (1, 2, 3, 9, 15))
    # Python task cancellation has no process exit code. Only a runner's shutdown/recovery
    # path may supply this evidence; a plain user cancellation is not a shutdown.
    return stopping is True and (cancelled is True or exit_code in codes)


def stopping_marker(settings, since: float) -> bool:
    """Use the launcher marker written before down kills either process, not an old stop."""
    root = settings.path(settings.gateway.state_dir)
    for role in ("gateway", "runner"):
        try:
            if (root / f"labhq-{role}.stopping").stat().st_mtime >= since:
                return True
        except OSError:
            pass
    return False


def watch_shutdown_signals(mark, *, interrupt=True, default_handler=None):
    """Mark before delegating to the CLI/uvicorn's existing shutdown handler."""
    previous = {}
    for name in (("SIGINT", "SIGTERM", "SIGHUP") if interrupt else ("SIGTERM", "SIGHUP")):
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        handler = signal.getsignal(sig)
        if not callable(handler):
            if handler not in (signal.SIG_DFL, signal.SIG_IGN) or default_handler is None:
                continue
        previous[sig] = handler
        delegate = handler if callable(handler) else default_handler

        def wrapped(signum, frame, handler=delegate):
            mark()
            return handler(signum, frame)

        signal.signal(sig, wrapped)

    def restore():
        for sig, handler in previous.items():
            signal.signal(sig, handler)
    return restore


def recorded_shutdown_evidence(settings, request):
    """Legacy failures need a contemporaneous gateway signal record, never today's stop flag."""
    if request.get("shutdown_evidence"):
        return request["shutdown_evidence"]
    error = request.get("error") or ""
    if not shutdown_interruption(error=error, stopping=True):
        return {}
    finished = request.get("finished_at")
    if not isinstance(finished, (int, float)):
        return {}
    path = settings.path(settings.gateway.state_dir) / "logs" / "gateway-diagnostics.jsonl"
    try:
        with path.open("rb", encoding=None) as source:
            size = source.seek(0, 2)
            source.seek(max(0, size - 65536))
            lines = source.read().splitlines()
    except OSError:
        return {}
    for line in reversed(lines):
        try:
            row = json.loads(line)
            if not isinstance(row, dict) or row.get("role") != "gateway" or row.get("event") != "signal":
                continue
            at = datetime.fromisoformat(row["time"].replace("Z", "+00:00")).timestamp()
            if (max(float(request.get("created_at") or 0), finished - 10) <= at <= finished
                    and shutdown_interruption(exit_code=row.get("code"), stopping=True)):
                return {"error": error, "stopping": True, "source": "gateway-diagnostics", "at": at,
                        "pid": row.get("pid"), "signal_code": row["code"]}
        except (KeyError, TypeError, ValueError, UnicodeError):
            continue
    return {}
