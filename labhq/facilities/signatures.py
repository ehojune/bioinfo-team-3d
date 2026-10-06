"""Deterministic environment-failure signatures (#35 stage 2).

A step that failed because the runner PC lacks something (a package, a command, disk, DNS, Docker, the Codex sandbox
setup) is an ``environment`` failure: another identical attempt fails the same way, so labhq does not retry it and
shows the PI the signature's cause and hint instead of a raw error. The table is data (``signatures.yaml``); this
module loads it and matches text. Only failure text is ever matched: the step's error, the CLI's stderr tail, and the
output of shell commands that failed. A command that succeeded, or a log that quotes the phrase, is not evidence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

from ..yaml_unique import load_yaml_unique

SIGNATURE_FILE = Path(__file__).with_name("signatures.yaml")
ENGINES = frozenset({"any", "claude_code", "codex", "gemini", "antigravity", "cli", "mock"})
REQUIRED = ("id", "engine", "pattern", "cause", "hint", "fix")
OPTIONAL = ("error_kind",)
# Where the evidence came from. "command" (a failed shell command the agent may have worked around) is weaker than
# the step's own error or the CLI's stderr: failure_kind consults it only after its other rules.
SOURCES = ("error", "stderr", "command")
SCAN_CHARS = 20_000  # tail of each text that is matched; the conclusion of a traceback is at its end
_EXIT_PREFIX = re.compile(r"^exit -?\d+: ")
# Lines that quote text rather than report it (PR #447 review): a pytest failure ("E   ..." or "> source line"), a
# compiler or grep -n hit ("path:12: ..."), and a grep hit in a log, text or source file ("logs/run1.log: ...").
_QUOTING_LINE = re.compile(r"^(?:E\s|\s*>|(?:[a-z]:)?[^\s:'\"]+:\d+:|(?:[a-z]:)?[^\s:'\"]*[/\\][^\s:'\"]*"
                           r"\.(?:log|txt|out|err|py|r|sh|md|ya?ml|json|tsv|csv|ipynb):)", re.IGNORECASE)


class SignatureError(ValueError):
    """signatures.yaml is malformed. The message names the entry, never a path."""


@dataclass(frozen=True)
class Signature:
    id: str
    engine: str
    pattern: re.Pattern[str]
    cause: str
    hint: str
    fix: str | None = None
    error_kind: str | None = None

    def applies_to(self, engine: str) -> bool:
        """An unknown engine (``""``, e.g. a stored record read back) matches every signature."""
        return self.engine == "any" or not engine or self.engine == engine

    def record(self, source: str) -> dict[str, str]:
        """What a result and an event carry: id, cause, hint, where it was seen, and the fix id once there is one."""
        out = {"id": self.id, "cause": self.cause, "hint": self.hint, "source": source}
        if self.fix:
            out["fix"] = self.fix
        return out


def _text(entry: dict, key: str, where: str, *, required: bool = True) -> str | None:
    value = entry.get(key)
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value.strip() or "\n" in value.strip():
        raise SignatureError(f"{where}: {key} must be one non-empty line")
    return value.strip()


def load(path: Path | None = None) -> tuple[Signature, ...]:
    """Read and check the table: known keys only, unique ids, known engines, patterns that compile."""
    source = path or SIGNATURE_FILE
    raw = load_yaml_unique(source.read_text(encoding="utf-8"), source.name)
    if not isinstance(raw, list) or not raw:
        raise SignatureError(f"{source.name}: expected a non-empty list of signatures")
    seen: set[str] = set()
    out = []
    for n, entry in enumerate(raw, 1):
        where = f"{source.name} entry {n}"
        if not isinstance(entry, dict):
            raise SignatureError(f"{where}: expected a mapping")
        missing = [key for key in REQUIRED if key not in entry]
        unknown = sorted(set(entry) - set(REQUIRED) - set(OPTIONAL))
        if missing or unknown:
            raise SignatureError(f"{where}: missing {missing} unknown {unknown}")
        sid = _text(entry, "id", where)
        if not re.fullmatch(r"[a-z][a-z0-9_]*", sid or "") or sid in seen:
            raise SignatureError(f"{where}: id must be unique snake_case")
        seen.add(sid)
        engine = _text(entry, "engine", where)
        if engine not in ENGINES:
            raise SignatureError(f"{where}: engine must be one of {sorted(ENGINES)}")
        try:
            pattern = re.compile(_text(entry, "pattern", where) or "", re.IGNORECASE | re.MULTILINE)
        except re.error as exc:
            raise SignatureError(f"{where}: pattern does not compile: {exc}") from None
        out.append(Signature(id=sid, engine=engine, pattern=pattern, cause=_text(entry, "cause", where) or "",
                             hint=_text(entry, "hint", where) or "", fix=_text(entry, "fix", where, required=False),
                             error_kind=_text(entry, "error_kind", where, required=False)))
    return tuple(out)


@lru_cache(maxsize=1)
def signatures() -> tuple[Signature, ...]:
    return load()


def by_id(sid: str) -> Signature | None:
    return next((sig for sig in signatures() if sig.id == sid), None)


def _lines(text: str) -> str:
    """A step error joins the stderr tail with " | " after "exit N: " (BaseAdapter.run). Put each part back on its
    own line so a pattern anchored with ^ sees the tool's own error line."""
    text = text[-SCAN_CHARS:]
    parts = (_EXIT_PREFIX.sub("", part) for part in text.replace(" | ", "\n").splitlines())
    return "\n".join(part for part in parts if not _QUOTING_LINE.match(part))


def match(engine: str, text: str | None, *, error_kind: str | None = None) -> Signature | None:
    """The first signature for this engine that the error kind or the text matches, else None."""
    usable = [sig for sig in signatures() if sig.applies_to(engine)]
    if error_kind:
        found = next((sig for sig in usable if sig.error_kind == error_kind), None)
        if found:
            return found
    if not text or not text.strip():
        return None
    body = _lines(text)
    return next((sig for sig in usable if sig.pattern.search(body)), None)


def scan_run(engine: str, stderr_lines: Iterable[str], failed_outputs: Iterable[Any]) -> dict[str, str] | None:
    """Runner side: the CLI's stderr tail first, then the last failed command's output. The adapters clear that output
    when a later shell command succeeds, so a failure the agent got past is not evidence (PR #447 review)."""
    found = match(engine, "\n".join(str(line) for line in stderr_lines))
    if found:
        return found.record("stderr")
    last = list(failed_outputs)[-1:]
    found = match(engine, str(last[0])) if last else None
    return found.record("command") if found else None


def clean_record(value: Any) -> dict[str, str] | None:
    """A result's environment field as another runner version may send it: bounded strings, an id, a known source."""
    if not isinstance(value, dict):
        return None
    out = {key: value[key].strip()[:300] for key in ("id", "cause", "hint", "source", "fix")
           if isinstance(value.get(key), str) and value[key].strip()}
    if not out.get("id"):
        return None
    if out.get("source") not in SOURCES:
        out["source"] = "command"  # unknown provenance is the weak kind
    return out


def problem_text(record: dict | None) -> str:
    """The one line the PI sees: "환경 문제: cause — hint"."""
    if not record:
        return ""
    cause = record.get("cause") or record.get("id") or ""
    hint = record.get("hint") or ""
    return f"환경 문제: {cause} — {hint}" if hint else f"환경 문제: {cause}"
