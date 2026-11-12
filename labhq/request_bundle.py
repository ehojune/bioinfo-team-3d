"""Portable, per-request copies of final reports and step outputs."""

from __future__ import annotations

import csv
import codecs
import hashlib
import json
import os
import platform
import re
import shlex
import shutil
import tempfile
import time
from collections import Counter
from contextlib import ExitStack, contextmanager
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Iterator
from urllib.parse import quote

from .adapters.held_dir import HeldDir, NotPlainFolder
from .evidence.audit import claims_record, locate_workdir
from .evidence.claims import normalize_artifact_path
from .report_figures import (FIGURE_PLAN_FILE, bundle_figure_check, drop_dead_abstract_links, figure_owners,
                             link_workdir_figures, linked_figure_paths, output_exists)
from .report_contract import (ReportPackage, build_report_package, reader_markdown, render_report_front,
                              with_report_front)
from .ro_crate import FORMAT_MARKER, METADATA_FILE, write_ro_crate


TEXT_SUFFIXES = frozenset({
    ".py", ".r", ".sh", ".bash", ".md", ".tsv", ".csv", ".json", ".txt", ".yaml", ".yml",
    ".toml", ".ini", ".cfg", ".xml", ".html", ".js", ".cjs", ".mjs", ".sql",
})
_TEXT_COPY_SUFFIXES = TEXT_SUFFIXES | {'.out', '.err', '.log', '.conf', '.config', '.properties', '.env'}
_TEXT_COPY_NAMES = frozenset({'condarc', '.condarc', 'config', 'makefile', 'dockerfile',
                             'stdout', 'stderr', 'versions', 'version', 'session_info', 'requirements',
                             '.bashrc', '.bash_profile', '.zshrc', '.profile', '.rprofile', '.renviron'})
SCRIPT_COMMANDS = {".r": "Rscript", ".sh": "bash", ".bash": "bash"}
SCRIPT_SUFFIXES = frozenset({".py", ".r", ".sh", ".bash"})
# Outputs that are prose, not data a script must have produced (the grade does not ask a script for them).
TEXT_ONLY = frozenset({".md"})  # .txt is a common data format (counts, variant lists): PR #431 review
DOCUMENT_SUFFIXES = frozenset({".md"})
MANIFEST_FIELDS = (
    "relative_path", "size", "sha256", "step_id", "original_path", "status",
    "rewritten", "remaining_absolute_paths", "rewritten_files", "reproducibility_grade",
    "reproducibility_reasons", "environment_records", "rerun_receipt",
    "redacted", "source_sha256", "source_size",
)
INPUT_FIELDS = ("step_id", "path", "size", "mtime_ns", "sha256", "skipped", "cached")
_PUBLIC_RECORD_FIELDS = frozenset({
    'request_id', 'id', 'step_id', 'task_id', 'workdir_id', 'agent_id', 'skill_id',
    'artifact_id', 'claim_id', 'evidence_id', 'source_id', 'status', 'decision', 'verdict', 'priority',
    'engine', 'model', 'engine_cli_version', 'started_at', 'ended_at',
    'path', 'bundle_path', 'artifact_sha256',
})
MANIFEST_MAX_BYTES = 16 * 1024 * 1024
_JSON_REDACTION_MAX_BYTES = 16 * 1024 * 1024
_TEXT_LINE_MAX_CHARS = 1024 * 1024
_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_SAFE_SCRIPT_COMPONENT = re.compile(r"[A-Za-z0-9._-]+\Z")
_WINDOWS_ABS = re.compile(r"(?i)(?<![A-Za-z0-9_])(?:[A-Z]:[\\/])[^\s<>\"'|]+")
_UNC_ABS = re.compile(r'''(?<![A-Za-z0-9_:/\\])(?:/{2,}|\\{2,})[^/\\\s<>"'`|]+[/\\]+[^\s<>"'`|]+''')
_PRIVATE_URI = re.compile(r'''(?i)\b(?!(?:http|https)://)[a-z][a-z0-9+.-]*://(?:\\[^\r\n]|[^\s<>"'`|\\])+''')
_POSIX_ABS = re.compile(r"(?:^|(?<=[\s=(\[{:>\"'`]))(?P<path>/(?!/|\.\.?/)[^\s<>\"'`|]+)", re.MULTILINE)
_SCRIPT_QUOTED = re.compile(r'''(?P<quote>["'])(?P<body>(?:\\[^\r\n]|(?!(?P=quote))[^\r\n\\])*)(?P=quote)''')
_PRIVATE_PATH_START = (
    r'''(?:(?<![A-Za-z0-9_])[A-Za-z]:[\\/]|(?<![A-Za-z0-9_:/\\])(?:/{2,}|\\{2,})'''
    r'''|(?:^|(?<=[\s=(\[{:>"'`]))/(?!/|\.\.?/)'''
    r'''|(?i:\b(?!(?:http|https)://)[a-z][a-z0-9+.-]*://))''')
_SCRIPT_ABS = re.compile(_PRIVATE_PATH_START +
    r'''(?:\\[^\r\n]|"(?:\\[^\r\n]|[^"\r\n\\])*"|'[^'\r\n]*'|[^\s<>"'`|\\;])+''', re.MULTILINE)
# Without shell quoting, a log/JSON value cannot distinguish path spaces from trailing prose.
# Hide that whole span conservatively, stopping at explicit markup or a protected bundle reference.
_TEXT_ABS = re.compile(_PRIVATE_PATH_START + r'''[^\r\n<>"'`|\x01]+''', re.MULTILINE)
_SYSTEM_OPERAND = re.compile(r'/(?:((?:usr/(?:local/)?)?s?bin)/[A-Za-z0-9_.+-]+|dev/(?:null|stdin|stdout|stderr))\Z')
_BINARY_PREFIXES = (b'%PDF-', b'\x89PNG', b'\xff\xd8\xff', b'GIF87a', b'GIF89a', b'PK\x03\x04',
                    b'\x1f\x8b', b'BAM\x01', b'\x89HDF', b'\x93NUMPY', b'ARROW1\0\0', b'SQLite format 3\0')
REMOTE_RUNNER_NOTE = "runner가 다른 PC라 묶음을 만들지 않음"
LINK_SCRIPT = "link_inputs.py"
RERUN_RECEIPT_FILE = "rerun_receipt.json"
# Written into the bundle: scripts read upstream files as inputs/<step id>/..., which the runner linked (#423).
LINK_SCRIPT_BODY = '''"""Link steps/<step>/inputs/<upstream> to steps/<upstream>/outputs (labhq request bundle)."""
import os
import shutil
from pathlib import Path

LINKS = __LINKS__
root = Path(__file__).resolve().parent
for step, upstream_ids in LINKS.items():
    for upstream in upstream_ids:
        target, link = root / "steps" / upstream / "outputs", root / "steps" / step / "inputs" / upstream
        if not target.is_dir() or os.path.lexists(link):
            continue
        link.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.symlink(os.path.relpath(target, link.parent), link, target_is_directory=True)
        except OSError:
            try:
                import _winapi  # Windows without the symlink privilege: a junction

                _winapi.CreateJunction(str(target), str(link))
            except (ImportError, OSError):
                shutil.copytree(target, link)
        print(f"steps/{step}/inputs/{upstream} -> steps/{upstream}/outputs")
'''
# Status suffix for a file the step wrote without reporting it: copied without a run-time hash, and a miss does not
# make the bundle incomplete ("not copied (unreported): size" does not start with "not copied:").
UNREPORTED_TAG = " (unreported)"


class NoStepWorkdirs(OSError):
    """No step of the request left a work folder to bundle (a request cancelled before its first folder existed)."""


class RemoteRunnerBundle(OSError):
    """The terminal result names a runner workspace that is not on the gateway host."""


def _rerun_receipt(req: Mapping[str, Any]) -> dict[str, str] | None:
    """Return the small path-free receipt shape that may raise a replayable bundle to rerun_verified."""
    raw = req.get("rerun_receipt")
    if not isinstance(raw, Mapping) or raw.get("status") != "verified" \
            or raw.get("request_id") != req.get("id"):
        return None
    verified_at = raw.get("verified_at")
    try:
        instant = datetime.fromisoformat(str(verified_at).replace("Z", "+00:00"))
    except ValueError:
        return None
    if instant.tzinfo is None:
        return None
    fields = ("source_manifest_sha256", "rerun_manifest_sha256")
    if any(not re.fullmatch(r"[0-9a-fA-F]{64}", str(raw.get(key) or "")) for key in fields):
        return None
    return {"status": "verified", "request_id": str(raw["request_id"]),
            "verified_at": str(verified_at),
            "source_manifest_sha256": str(raw["source_manifest_sha256"]).casefold(),
            "rerun_manifest_sha256": str(raw["rerun_manifest_sha256"]).casefold()}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _absolute_paths(text: str) -> list[str]:
    return sorted(set(_WINDOWS_ABS.findall(text)) |
                  {match.group("path") for match in _POSIX_ABS.finditer(text)})


def _safe_output_path(raw: str) -> PurePosixPath | None:
    value = str(raw).replace("\\", "/")
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or path.parts[0] != "outputs":
        return None
    if any(part in ("", ".", "..") for part in path.parts):
        return None
    return path


def _clear_owned_dir(path: Path, parent: Path, held: HeldDir | None = None) -> None:
    """Remove only the fixed plain directory directly below the resolved request-bundle parent.

    With `held` (the parent, opened once without following a link), the parent must still be that folder: a swap for a
    symlink between the check and the delete is refused instead of deleting below the new target (#405)."""
    from .adapters.owned import is_link

    if not path.exists() and not path.is_symlink():
        return
    if held is not None and not held.same_as(parent):
        raise OSError("request bundle parent changed while the bundle was being built")
    if is_link(path) or not path.is_dir() or path.resolve().parent != parent.resolve():
        raise OSError(f"request bundle target is not a plain child directory: {path}")
    shutil.rmtree(path)


RENAME_RETRY_DELAYS = (0.2, 0.5, 1.0, 2.0)


def _replace_dir(temp: Path, target: Path, *, windows: bool = os.name == "nt") -> None:
    """Move the finished temp bundle into place, retrying a Windows sharing lock briefly.

    Right after the copy, Defender or the search indexer can hold a handle on a new file, and the directory rename
    fails with WinError 5 although nothing is wrong; the v0.5 trial lost its bundle that way (2026-10-08)."""
    for delay in (*RENAME_RETRY_DELAYS, None):
        try:
            temp.replace(target)
            return
        except PermissionError:
            if not windows or delay is None:
                raise
            time.sleep(delay)


def _recorded_outputs(result: Mapping[str, Any]) -> list[tuple[str, PurePosixPath | None, str]]:
    """Runner outputs that have a hash in the same terminal result, preserving output order."""
    hashes = result.get("output_sha256") if isinstance(result.get("output_sha256"), Mapping) else {}
    found: list[tuple[str, PurePosixPath | None, str]] = []
    seen: set[str] = set()
    for raw in result.get("outputs") or []:
        if not isinstance(raw, str) or raw in seen or raw not in hashes:
            continue
        seen.add(raw)
        found.append((raw, _safe_output_path(raw), str(hashes[raw])))
    return found


@contextmanager
def _open_recorded_output(outputs: HeldDir, path: PurePosixPath) -> Iterator[tuple[Any, os.stat_result]]:
    """Open one recorded output by held parents, without listing or following links."""
    with ExitStack() as stack:
        folder = outputs
        for part in path.parts[1:-1]:
            folder = stack.enter_context(folder.child(part))
        fd = folder.open_read_file(path.name)
        try:
            with os.fdopen(fd, "rb") as stream:
                fd = -1
                yield stream, os.fstat(stream.fileno())
        finally:
            if fd >= 0:
                os.close(fd)


def _held_workdir(stack: ExitStack, root: Path,
                  result: Mapping[str, Any], *, shared_wsl: bool = False) -> tuple[HeldDir, Path, dict[str, Any]] | tuple[None, str, dict]:
    """Hold one same-host workdir inside the configured root, or return its local lookup failure."""
    workdir, reason = locate_workdir(root, result)
    if workdir is None:
        return None, str(reason), {}
    try:
        held = stack.enter_context(HeldDir.hold(workdir))
        real_root, real_workdir = root.resolve(), workdir.resolve()
        if (real_workdir == real_root or not real_workdir.is_relative_to(real_root)
                or not held.same_as(real_workdir)):
            return None, "runner.workspace_root 밖이거나 바뀐 작업 폴더", {}
        fd = held.open_read_file("manifest.json")
        with os.fdopen(fd, "rb") as source:
            manifest = json.loads(source.read(MANIFEST_MAX_BYTES + 1).decode("utf-8"))
        if not isinstance(manifest, dict) or (manifest.get("host") != platform.node() and not shared_wsl):
            raise RemoteRunnerBundle(REMOTE_RUNNER_NOTE)
        return held, real_workdir, manifest
    except RemoteRunnerBundle:
        raise
    except (OSError, UnicodeError, ValueError) as exc:
        raise RemoteRunnerBundle(REMOTE_RUNNER_NOTE) from exc


def _job_scripts(held: HeldDir, workdir: Path) -> list[tuple[str, str]]:
    """Submitted job scripts recorded by the runner, deduplicated by safe direct-child name."""
    try:
        fd = held.open_read_file("jobs.jsonl")
    except FileNotFoundError:
        return []
    with os.fdopen(fd, "rb") as source:
        raw = source.read(MANIFEST_MAX_BYTES + 1)
    if len(raw) > MANIFEST_MAX_BYTES:
        raise OSError("jobs.jsonl too large")
    expected_parent = os.path.normcase(os.path.abspath(str(workdir / "jobs")))
    found: dict[str, str] = {}
    for line in raw.decode("utf-8").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, Mapping):
            continue
        script, sha256 = record.get("script"), record.get("script_sha256")
        if not isinstance(script, str) or not isinstance(sha256, str) \
                or not re.fullmatch(r"[0-9a-fA-F]{64}", sha256):
            continue
        path = Path(script)
        if (not path.is_absolute() or path.suffix.casefold() != ".sh"
                or os.path.normcase(os.path.abspath(str(path.parent))) != expected_parent
                or not _SAFE_SCRIPT_COMPONENT.fullmatch(path.name)):
            continue
        found[path.name] = sha256.casefold()
    return sorted(found.items())


def _copy_held(stream, info: os.stat_result, destination: Path) -> str:
    """Copy and hash the already-open source, rejecting a file that changes during the read."""
    digest = hashlib.sha256()
    read = 0
    with open(destination, "wb") as target:
        while block := stream.read(1024 * 1024):
            digest.update(block)
            target.write(block)
            read += len(block)
    after = os.fstat(stream.fileno())
    if read != info.st_size or (after.st_size, after.st_mtime_ns) != (info.st_size, info.st_mtime_ns):
        destination.unlink(missing_ok=True)
        raise OSError("source changed while request bundle copied it")
    return digest.hexdigest()


def _path_prefix_pattern(workdir: Any, *, outputs: bool = True) -> re.Pattern[str]:
    raw = str(workdir)
    windows = bool(re.match(r"^[A-Za-z]:[\\/]", raw) or re.match(r"^[\\/]{2}[^\\/]", raw))
    separator = r"[\\/]+" if windows else "/"
    parts = [part for part in re.split(r"[\\/]+", raw) if part]
    prefix = separator.join(re.escape(part) for part in parts)
    if windows and raw.startswith(("//", "\\\\")):
        prefix = r"[\\/]{2}" + prefix
    elif not windows and raw.startswith(("/", "\\")):
        prefix = "/" + prefix
    # Match the rest of a path until a common text delimiter so its separators can be made portable too.
    boundary = r"(?:^|(?<=[\s\"'=\(\[,:]))"
    flags = re.IGNORECASE if windows else 0
    return re.compile(boundary + prefix + separator + (r"outputs" + separator if outputs else "") +
                      r"(?P<tail>[^\s<>\"'`|]*)", flags)


def _binary_prefix(data: bytes) -> bool:
    return (data.startswith(_BINARY_PREFIXES)
            or (data.startswith(b'RIFF') and data[8:12] in (b'WEBP', b'WAVE', b'AVI '))
            or (data.startswith(b'CRAM') and len(data) >= 6 and 1 <= data[4] <= 4 and data[5] <= 9)
            or (data.startswith(b'SIMPLE  =') and data[29:30] in (b'T', b'F') and data[80:88] == b'BITPIX  '))


def _binary_artifact(path: Path, prefix: bytes) -> bool:
    if _binary_prefix(prefix):
        return True
    # Short ASCII prefixes can also be table values; require the container's footer marker.
    if prefix.startswith((b'PAR1', b'PARE')) and path.stat().st_size >= 12:
        with path.open(mode='rb') as handle:
            handle.seek(-4, os.SEEK_END)
            return handle.read(4) == prefix[:4]
    return False


def _text_lines(path: Path, *, newline=''):
    with path.open('r', encoding='utf-8', newline=newline) as handle:
        while line := handle.readline(_TEXT_LINE_MAX_CHARS + 1):
            if len(line) > _TEXT_LINE_MAX_CHARS:
                raise ValueError('text line exceeds bounded bundle transformation limit; bundle withheld')
            yield line


def _transform_text(path: Path, transform, *, newline='') -> bool | None:
    """Bounded UTF-8 transformation; invalid/binary text leaves the derived copy untouched."""
    with path.open(mode='rb') as handle:
        prefix = handle.read(8192)
        if b'\0' in prefix or _binary_artifact(path, prefix):
            return None
    try:
        with tempfile.TemporaryFile(dir=path.parent) as staged:
            changed = False
            for line in _text_lines(path, newline=newline):
                if '\0' in line:
                    return None
                cleaned = transform(line)
                changed |= cleaned != line
                staged.write(cleaned.encode('utf-8'))
            if changed:
                staged.seek(0)
                with path.open(mode='wb') as target:
                    shutil.copyfileobj(staged, target, length=1024 * 1024)
            return changed
    except UnicodeError:
        return None


def _rewrite_text(path: Path, workdirs: list[tuple[str, Path]],
                  data_inputs: list[tuple[str, Path]] = ()) -> tuple[int, list[str]]:
    try:
        with path.open(mode='rb') as handle:
            prefix = handle.read(8192)
            if b'\0' in prefix or _binary_artifact(path, prefix):
                return 0, []
    except OSError:
        return 0, []
    replacements = 0
    remaining = set()
    remaining_chars = 0

    def record_paths(line):
        nonlocal remaining_chars
        for value in _absolute_paths(line):
            if value not in remaining:
                remaining.add(value)
                remaining_chars += len(value)
        if len(remaining) > 10000 or remaining_chars > 16 * 1024 * 1024:
            raise ValueError('too many absolute paths for bounded bundle transformation; bundle withheld')

    bundle_root = next((parent for parent in path.parents if (parent / "steps").is_dir()), None)
    if bundle_root is None:
        try:
            for line in _text_lines(path):
                record_paths(line)
        except UnicodeError:
            return 0, []
        return 0, sorted(remaining)
    relative_path = path.relative_to(bundle_root)
    if path.suffix.casefold() in DOCUMENT_SUFFIXES:
        destination = path.parent
    elif len(relative_path.parts) >= 3 and relative_path.parts[0] == 'steps':
        destination = bundle_root / 'steps' / relative_path.parts[1]
    else:
        destination = bundle_root
    job_owner, command = None, None
    if len(relative_path.parts) == 4 and relative_path.parts[0] == 'steps' and relative_path.parts[2] == 'jobs':
        job_owner = next((folder for sid, folder in workdirs if sid == relative_path.parts[1]), None)
        if job_owner is not None:
            expected = 'cd ' + shlex.quote(str(job_owner / 'outputs'))
            count, matched = 0, False
            try:
                for line in _text_lines(path):
                    if line.startswith('cd '):
                        count += 1
                        matched |= line.rstrip('\r\n') == expected
                        if count > 1:
                            break
            except UnicodeError:
                return 0, []
            if count == 1 and matched:
                command = expected
                destination = bundle_root / 'steps' / relative_path.parts[1] / 'outputs'
            else:
                job_owner = None
    patterns = []
    for step_id, workdir, is_outputs in [*((sid, folder, True) for sid, folder in workdirs),
                                        *((sid, folder, False) for sid, folder in data_inputs)]:
        target = bundle_root / 'steps' / step_id / 'outputs'
        relative = os.path.relpath(target, destination).replace('\\', '/')
        patterns.append((_path_prefix_pattern(workdir, outputs=is_outputs), relative))
        if not is_outputs and job_owner is not None:
            original_relative = Path(os.path.relpath(workdir, job_owner / 'outputs'))
            patterns.append((_path_prefix_pattern(original_relative, outputs=False), relative))

    def rewrite(line):
        nonlocal replacements
        if command and line.rstrip('\r\n') == command:
            line = 'cd outputs\n'
            replacements += 1
        for pattern, relative in patterns:
            def replace(match):
                nonlocal replacements
                replacements += 1
                tail = match.group('tail').replace('\\', '/')
                return f'{relative}/{tail}'
            line = pattern.sub(replace, line)
        record_paths(line)
        return line

    transformed = _transform_text(path, rewrite, newline=None)
    return (0, []) if transformed is None else (replacements, sorted(remaining))


def _public_hpc_text(settings, *, claim_refs=(), portable_refs=()):
    """Offline job-log policy for a configured remote site's bundle copies; never opens a session."""
    from .hpc_session import redact, redaction_words
    from .hpc_transfer import Transfer

    cfg = settings.hpc
    if not (cfg.site_profile or cfg.ssh_host):
        return None
    profile = None
    if cfg.site_profile:
        from .hpc_site import load_profile
        profile = load_profile(cfg.site_profile)  # an unreadable named profile fails closed
    words = set(redaction_words(["ssh", *shlex.split(cfg.ssh_host or "")]))
    if profile:
        words.update(redaction_words(profile.connect_argv))
    site_values = (cfg.user, cfg.default_queue, getattr(profile, "queue", None),
                   *(getattr(profile, "pinned_nodes", []) or []))
    words.update(redaction_words(["site", *(word for word in site_values if word)]))
    root = getattr(profile, "remote_workdir", None) or "/"

    def site_words(text):
        return redact(text, sorted(words, key=len, reverse=True))

    def no_remote_command(command):
        raise RuntimeError("bundle redaction cannot execute remote commands")

    transfer = Transfer(no_remote_command, root, redact=site_words)
    portable_count, portable_pattern = -1, None

    def hide(text, *, script=False):
        nonlocal portable_count, portable_pattern
        def quoted_operand(match):
            value = match.group('body')
            if _WINDOWS_ABS.search(value) or _UNC_ABS.search(value) or _PRIVATE_URI.search(value) \
                    or any(not script or not _SYSTEM_OPERAND.fullmatch(found.group('path'))
                           for found in _POSIX_ABS.finditer(value)):
                return match.group('quote') + '<remote-path>' + match.group('quote')
            return match.group(0)

        if script:
            text = _SCRIPT_QUOTED.sub(quoted_operand, text)
            sentinel = '\x01'
            while sentinel in text:
                sentinel *= 2
            systems = []

            def operand(match):
                raw = match.group(0)
                path = raw.rstrip('.,;:)')
                if _SYSTEM_OPERAND.fullmatch(path):
                    systems.append(raw)
                    return f'{sentinel}{len(systems) - 1}{sentinel}'
                return '<remote-path>' + raw[len(path):]

            # Keep only system interpreters/executables/devices; shared-site paths outside the configured root are private.
            text = re.sub(r'(?<=#!)/[^\s]+', operand, text)
            text = _SCRIPT_ABS.sub(operand, text)
            text = _WINDOWS_ABS.sub('<local-path>', transfer._hide(text))
            for index, value in enumerate(systems):
                text = text.replace(f'{sentinel}{index}{sentinel}', value)
            return text
        # Preserve only references to declared ledger IDs, never arbitrary relative-looking site text.
        sentinel = '\x01'
        while sentinel in text:
            sentinel *= 2
        references = []
        if portable_count != len(portable_refs):
            known = {token for ref in portable_refs for token in (ref, quote(ref, safe='/-._~'))}
            portable_pattern = re.compile('|'.join(re.escape(ref) for ref in sorted(known, key=len, reverse=True))) if known else None
            portable_count = len(portable_refs)

        def keep_reference(match):
            references.append(match.group(0))
            return f'{sentinel}{len(references) - 1}{sentinel}'

        if portable_pattern:
            text = portable_pattern.sub(keep_reference, text)
        for ref in claim_refs:
            for token in (f'[[claim:{ref}]]', f'`{ref}`'):
                if token in text:
                    references.append(token)
                    text = text.replace(token, f'{sentinel}{len(references) - 1}{sentinel}')
        text = _SCRIPT_QUOTED.sub(quoted_operand, text)
        text = _TEXT_ABS.sub('<remote-path>', text)
        text = _WINDOWS_ABS.sub("<local-path>", transfer.hide_log(text))
        for index, token in enumerate(references):
            text = text.replace(f'{sentinel}{index}{sentinel}', token)
        return text

    return hide


def _public_values(value, hide, *, redact_keys=True, preserve_fields=()):
    """Transform JSON values structurally so Windows escapes and quoted text stay valid JSON."""
    if isinstance(value, str):
        if re.fullmatch(r'[0-9a-fA-F]{64}', value):
            return value  # hashes are integrity evidence, even when a short account matches their digits
        return hide(value)
    if isinstance(value, Mapping):
        transformed = {}
        for key, item in value.items():
            preserve = key in preserve_fields
            if preserve and isinstance(item, str) and (
                    _WINDOWS_ABS.search(item) or _POSIX_ABS.search(item)
                    or _UNC_ABS.search(item) or _PRIVATE_URI.search(item)):
                preserve = False
            public_key = hide(key) if redact_keys and isinstance(key, str) else key
            transformed[public_key] = item if preserve else _public_values(
                item, hide, redact_keys=redact_keys, preserve_fields=preserve_fields)
        if len(transformed) != len(value):
            raise ValueError('site redaction would merge JSON keys; bundle withheld')
        return transformed
    if isinstance(value, list):
        return [_public_values(item, hide, redact_keys=redact_keys, preserve_fields=preserve_fields) for item in value]
    return value


def _redact_bundle_text(path, hide, *, hash_bound=False, record_fields=()):
    """Content-based UTF-8 detection includes suffixless configs and scheduler .out/.err files."""
    with path.open(mode='rb') as handle:
        prefix = handle.read(8192)
    if _binary_artifact(path, prefix):
        return False  # PDFs and other binary containers can start with entirely ASCII metadata
    text_candidate = path.suffix.casefold() in _TEXT_COPY_SUFFIXES or path.name.casefold() in _TEXT_COPY_NAMES
    if not text_candidate:
        return False  # opaque formats are never guessed to be text from an ASCII/UTF-8 prefix
    if b'\0' in prefix:
        if text_candidate:
            raise ValueError('text artifact contains NUL bytes; public bundle withheld')
        return False
    try:
        codecs.getincrementaldecoder('utf-8')().decode(prefix, final=False)
    except UnicodeError as exc:
        if text_candidate:
            raise ValueError('text artifact is not valid UTF-8; public bundle withheld') from exc
        return False
    if path.suffix.casefold() != '.json':
        transformed = _transform_text(path, lambda line: hide(line, script=path.suffix.casefold() in SCRIPT_SUFFIXES))
        if transformed is None:
            raise ValueError('text artifact is not valid UTF-8 or contains NUL bytes; public bundle withheld')
        return transformed
    with path.open(mode='rb') as handle:
        data = handle.read(_JSON_REDACTION_MAX_BYTES + 1)
    if len(data) > _JSON_REDACTION_MAX_BYTES:
        raise ValueError('JSON artifact exceeds bounded structural-redaction limit; bundle withheld')
    try:
        text = data.decode('utf-8')
    except UnicodeError as exc:
        raise ValueError('JSON artifact is not valid UTF-8; public bundle withheld') from exc
    if path.suffix.casefold() == '.json':
        try:
            value = json.loads(text)
        except ValueError:
            cleaned = hide(text)
        else:
            if hash_bound and isinstance(value, Mapping):
                # Canonical checkpoint metadata names the immutable request. Inspect only its payloads,
                # retaining the typed, path-free fields produced by final_checkpoint.freeze/decide.
                metadata = {'request_id', 'revision', 'target_sha256', 'report_sha256', 'audit_sha256',
                            'claims_sha256', 'created_at', 'deadline_at', 'gate', 'decision', 'decider',
                            'decided_at', 'delegation_scope'}
                value = {key: ({k: v for k, v in section.items() if k not in metadata}
                               if isinstance(section, Mapping) else section)
                         for key in ('draft', 'receipt') if (section := value.get(key)) is not None}
                transformed = _public_values(value, hide, redact_keys=False, preserve_fields=_PUBLIC_RECORD_FIELDS)
            else:
                transformed = _public_values(value, hide, redact_keys=not bool(record_fields),
                                             preserve_fields=record_fields)
            cleaned = json.dumps(transformed, ensure_ascii=False, indent=2) + '\n' if transformed != value else text
    else:
        cleaned = hide(text, script=path.suffix.casefold() in SCRIPT_SUFFIXES)
    if cleaned == text:
        return False
    if hash_bound:
        raise ValueError('hash-bound final checkpoint contains private site text; bundle withheld')
    path.write_text(cleaned, encoding='utf-8', newline='\n')
    return True


def _superseded(req: Mapping[str, Any], tasks: Mapping[str, Any]) -> list[str]:
    rid = req.get("id")
    current = {str(sid): str((result or {}).get("task_id") or "")
               for sid, result in (req.get("results") or {}).items() if isinstance(result, Mapping)}
    rows: set[str] = set()
    for entry in tasks.values():
        if not isinstance(entry, Mapping) or entry.get("request_id") != rid or not entry.get("completed"):
            continue
        result = entry.get("result") if isinstance(entry.get("result"), Mapping) else {}
        sid = str(entry.get("step_id") or entry.get("kind") or "")
        if sid in current and str(result.get("task_id") or "") != current[sid] and result.get("outputs"):
            rows.add(f"{sid}: {result.get('workdir_id') or 'unknown-workdir'} ({', '.join(map(str, result['outputs']))})")
    for history in req.get("replan_history") or []:
        if not isinstance(history, Mapping):
            continue
        prior = history.get("prior_results") if isinstance(history.get("prior_results"), Mapping) else {}
        for sid, result in prior.items():
            if isinstance(result, Mapping):
                rows.add(f"{sid}: {result.get('workdir_id') or 'unknown-workdir'} "
                         f"({', '.join(map(str, result.get('outputs') or [])) or 'outputs 없음'})")
    return sorted(rows)


def _topological_steps(steps: list[Mapping[str, Any]], results: Mapping[str, Any]) -> tuple[list[str], list[Mapping[str, Any]]]:
    """Return result step ids by DAG depth then id, with undeclared/direct results last."""
    by_id = {str(step.get("id")): step for step in steps if step.get("id") is not None}
    remaining = {step_id for step_id in by_id if step_id in results}
    ordered: list[str] = []
    completed: set[str] = set()
    while remaining:
        layer = sorted(step_id for step_id in remaining
                       if {str(dep) for dep in by_id[step_id].get("depends_on") or []
                           if str(dep) in by_id and str(dep) in results}
                       <= completed)
        if not layer:  # Invalid/cyclic stored plans remain deterministic; validation belongs to plan intake.
            layer = sorted(remaining)
        ordered.extend(layer)
        completed.update(layer)
        remaining.difference_update(layer)
    ordered.extend(sorted(str(step_id) for step_id in results if str(step_id) not in ordered))
    return ordered, [by_id[step_id] for step_id in ordered if step_id in by_id]


def _tool_calls_section(ordered_ids: list[str], results: Mapping[str, Any]) -> str:
    """Render aggregate tool names and counts only; raw inputs never enter the request bundle."""
    rows: list[str] = []
    for step_id in ordered_ids:
        result = results.get(step_id)
        if not isinstance(result, Mapping):
            continue
        agent = str(result.get("agent_id") or "-").replace("|", " ").replace("\n", " ")[:160]
        calls = result.get("tool_calls")
        if not isinstance(calls, Mapping):
            continue
        for tool, raw in sorted(calls.items(), key=lambda item: str(item[0])):
            if not isinstance(raw, Mapping):
                continue
            count, failures = raw.get("calls"), raw.get("failures")
            if (not isinstance(count, int) or isinstance(count, bool) or count < 0
                    or not isinstance(failures, int) or isinstance(failures, bool)
                    or failures < 0 or failures > count):
                continue
            name = str(tool).replace("|", " ").replace("\r", " ").replace("\n", " ")[:160]
            rows.append(f"| {step_id} | {agent} | {name} | {count} | {failures} |")
    if not rows:
        return ""
    return "\n".join(["## 직원별 도구 호출 수", "", "| 단계 | 직원 | 도구 | 호출 | 실패 |",
                      "|---|---|---|---:|---:|", *rows])


def _safe_script_path(path: PurePosixPath) -> bool:
    return all(_SAFE_SCRIPT_COMPONENT.fullmatch(part) for part in path.parts)


def _runner_python_command(result: Mapping[str, Any], tasks: Mapping[str, Any],
                           runner_capabilities: Mapping[str, Any]) -> str | None:
    task = tasks.get(str(result.get("task_id") or ""))
    runner_id = task.get("runner_id") if isinstance(task, Mapping) else None
    capabilities = runner_capabilities.get(str(runner_id)) if runner_id else None
    local = capabilities.get("local_software") if isinstance(capabilities, Mapping) else None
    python = local.get("python") if isinstance(local, Mapping) else None
    command = python.get("command") if isinstance(python, Mapping) else None
    return command if command in {"python3", "python", "py"} else None


def _grade(rows: list[dict[str, Any]], steps: list[Mapping[str, Any]],
           rewrite_by_file: Mapping[str, tuple[int, list[str]]], not_copied: int,
           rerun_receipt: Mapping[str, str] | None = None) -> tuple[str, list[str]]:
    """Reproducibility grade and reasons (#423, docs/research_protocol.md).

    replayable: every recorded output was copied; a step with data outputs ships a script; a step with scripts has an
    environment record (outputs/env/) of its own or from a step it depends on; no copied script keeps an absolute path.
    `rerun_verified` additionally needs a validated, recorded rerun receipt."""
    reasons = [f"기록 산출 {not_copied}개 미복사"] if not_copied else []
    files: dict[str, list[PurePosixPath]] = {}
    for row in rows:
        if row["status"] in ("copied", f"copied{UNREPORTED_TAG}"):
            parts = PurePosixPath(str(row["relative_path"])).parts
            if len(parts) >= 4 and parts[0] == "steps" and parts[2] == "outputs":
                files.setdefault(parts[1], []).append(PurePosixPath(*parts[3:]))
    deps = {str(step.get("id")): [str(dep) for dep in step.get("depends_on") or []] for step in steps}

    def lineage(step_id: str) -> set[str]:
        found, pending = {step_id}, [step_id]
        while pending:
            for dep in deps.get(pending.pop(), []):
                if dep not in found:
                    found.add(dep)
                    pending.append(dep)
        return found

    has_env = {step_id for step_id, paths in files.items() if any(path.parts[0] == "env" for path in paths)}
    no_script, no_env = [], []
    for step_id, paths in sorted(files.items()):
        scripts = [p for p in paths if p.parts[0] == "scripts" and p.suffix.casefold() in SCRIPT_SUFFIXES]
        data = [p for p in paths if p.parts[0] not in ("scripts", "env") and p.suffix.casefold() not in TEXT_ONLY]
        if data and not scripts:
            no_script.append(step_id)
        if scripts and not lineage(step_id) & has_env:
            no_env.append(step_id)
    absolute = sorted(path for path, (_count, left) in rewrite_by_file.items()
                      if left and ("/outputs/scripts/" in path or "/jobs/" in path))
    if no_script:
        reasons.append("스크립트 없이 데이터 산출만 있는 단계: " + ", ".join(no_script))
    if no_env:
        reasons.append("환경 기록(outputs/env/)이 없는 단계: " + ", ".join(no_env))
    if absolute:
        reasons.append("절대경로가 남은 스크립트: " + ", ".join(absolute))
    if reasons:
        return "documented", reasons
    return ("rerun_verified" if rerun_receipt else "replayable"), reasons


def _bundle_claims(record: dict[str, Any], results: Mapping[str, Any], copied: set[str]) -> dict[str, Any]:
    """The claims record with each artifact ref's place in this bundle (#497 review): a ledger cites `outputs/x` (its
    own step) or `<workdir_id>/outputs/x` (an ancestor), and the bundle keeps both under `steps/<step_id>/`. The
    original `path` stays; `bundle_path` is added only when that file was copied."""
    record = json.loads(json.dumps(record, default=str))  # the ledgers belong to the stored request
    owners = {str(result.get("workdir_id")): step_id for step_id, result in results.items()
              if isinstance(result, Mapping) and result.get("workdir_id")}
    for step_id, ledger in (record.get("ledgers") or {}).items():
        for ref in (ledger or {}).get("artifact_refs") or [] if isinstance(ledger, dict) else []:
            if not isinstance(ref, dict):
                continue
            path = normalize_artifact_path(str(ref.get("path") or ""))
            head, _, rest = path.partition("/")
            owner, inner = (owners[head], rest) if head in owners and rest else (step_id, path)
            if f"steps/{owner}/{inner}" in copied:
                ref["bundle_path"] = f"steps/{owner}/{inner}"
    return record


def _readme(req: Mapping[str, Any], steps: list[Mapping[str, Any]], scripts: list[str],
            unsafe_scripts: list[str], python_unknown: bool, linked: bool = False, claims: bool = False,
            job_scripts: list[str] | None = None, *, hide_front=None) -> str:
    stored = req.get("report_package")
    package = (ReportPackage.from_dict(dict(stored)) if isinstance(stored, Mapping)
               else build_report_package(dict(req), str(req.get("report") or ""),
                                         dict(req.get("results") or {})))
    front = render_report_front(package).rstrip()
    if hide_front:
        front = hide_front(front)
    lines = [
        "# 요청 묶음", "", front, "", "## 묶음 파일", "",
        f"- 요청: `{req.get('id')}`", "- `report.md`: PI용 본문",
        *(["- `claims.json`: 연구 요청의 단계별 claim·근거 원장과 CP2 기록. 보고서의 `[[claim:<단계>/<claim>]]`은 "
           "`ledgers.<단계>.claims`의 `id`입니다"] if claims else []),
        "- `report_appendix.md`: 과학 부록(점검표·리뷰·가정)",
        "- `execution_record.md`: 운전 기록과 묶음 변환 기록", "- `steps/<step_id>/`: 단계 workdir 사본",
        "- `MANIFEST.tsv`: 사본의 크기·sha256과 원래 위치",
        "- `INPUTS.tsv`: 단계가 읽을 수 있던 외부 입력의 크기·mtime·sha256 또는 생략 이유", "",
        FORMAT_MARKER, "", "## RO-Crate", "",
        "`ro-crate-metadata.json`은 RO-Crate 1.1·Process Run Crate 0.5 metadata입니다. "
        "INPUTS.tsv는 실제 사용 입력이 아니라 실행 전 입력 영역 scan으로 기록합니다.", "",
        "## 단계 순서", "",
    ]
    if steps:
        for step in steps:
            deps = ", ".join(map(str, step.get("depends_on") or [])) or "없음"
            lines.append(f"- `{step.get('id')}` (의존: {deps})")
    else:
        lines.append("- 선언된 단계 없음")
    lines += ["", "## 다시 실행", "", "아래 명령은 묶음 루트에서 시작해 각 단계 폴더에서 실행합니다.", ""]
    if linked:
        lines.append(f"- 먼저 `python {LINK_SCRIPT}`: 단계 폴더의 `inputs/<앞 단계>`를 `steps/<앞 단계>/outputs`로 잇습니다")
    lines += [f"- `{command}`" for command in scripts] if scripts else ["- 실행 스크립트 없음"]
    lines += [f"- 이름이 안전하지 않아 명령을 만들지 않음: `{path}`" for path in unsafe_scripts]
    if python_unknown:
        lines.append("- runner가 쓴 python 명령을 확인하지 못함: `python`을 사용")
    if job_scripts:
        lines += ["", "## HPC 잡 스크립트", "", *(f"- `{path}`" for path in job_scripts)]
    lines += ["", "묶음은 원본의 사본입니다. `labhq verify`와 claim anchor 검사는 원래 작업 폴더를 기준으로 합니다.", ""]
    return "\n".join(lines)


def build_request_bundle(req: Mapping[str, Any], settings: Any,
                         tasks: Mapping[str, Any] | None = None,
                         runner_capabilities: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Build ``<workspace_root>/requests/<request_id>`` from the request's final stored results.

    ``requests/`` is opened once, without following a link, and stays held for the whole build (#405): on Windows the
    open handle keeps that folder, and every folder above it, from being renamed or replaced; elsewhere each delete and
    the final replace first check that the path still names the held folder."""
    rid = str(req.get("id") or "")
    if not _SAFE_ID.fullmatch(rid):
        raise ValueError("unsafe request id for bundle")
    root = settings.path(settings.runner.workspace_root).resolve()
    requests_root = root / "requests"
    requests_root.mkdir(parents=True, exist_ok=True)
    from .adapters.owned import is_link
    if is_link(requests_root) or requests_root.resolve().parent != root:
        raise OSError("request bundle parent is not a plain workspace directory")
    with HeldDir.hold(requests_root) as requests_held:
        return _build_request_bundle(req, settings, tasks, runner_capabilities, requests_held)


def _build_request_bundle(req: Mapping[str, Any], settings: Any, tasks: Mapping[str, Any] | None,
                          runner_capabilities: Mapping[str, Any] | None, requests_held: HeldDir) -> dict[str, Any]:
    rid = str(req.get("id") or "")
    root = settings.path(settings.runner.workspace_root).resolve()
    requests_root = root / "requests"
    target = requests_root / rid
    temp = requests_root / f".{rid}.tmp"
    _clear_owned_dir(temp, requests_root, requests_held)
    temp.mkdir()
    rows: list[dict[str, Any]] = []
    input_rows: list[dict[str, Any]] = []
    run_records: dict[str, dict[str, Any]] = {}
    workdirs: list[tuple[str, Path]] = []
    data_inputs: list[tuple[str, Path]] = []
    script_commands: dict[str, dict[str, str]] = {}
    unsafe_scripts: dict[str, set[str]] = {}
    copied_job_scripts: list[str] = []
    python_unknown = False
    tasks = tasks or {}
    runner_capabilities = runner_capabilities or {}
    results = req.get("results") if isinstance(req.get("results"), Mapping) else {}
    rerun_receipt = _rerun_receipt(req)
    claim_refs = []
    for step_id, result in results.items():
        structured = result.get('structured') if isinstance(result, Mapping) else None
        if not _SAFE_ID.fullmatch(str(step_id)) or not isinstance(structured, Mapping):
            continue
        claim_refs.extend(f"{step_id}/{claim['id']}" for claim in structured.get('claims') or []
                          if isinstance(claim, Mapping) and _SAFE_ID.fullmatch(str(claim.get('id') or '')))
    portable_refs = set()
    public_text = _public_hpc_text(settings, claim_refs=claim_refs, portable_refs=portable_refs)
    # The report links a figure as <workdir_id>/outputs/<file>; the bundle names it steps/<step_id>/outputs/<file>, and
    # a linked figure is kept even when the bundle's total limits are full (#584). The per-file limit still applies.
    report_text = link_workdir_figures(str(req.get("report") or ""), figure_owners(results), output_exists(results))
    # A graphical abstract the writer linked but no step produced is no bundle file: the link goes, and the PDF and the
    # edition draw their own abstract (#608). Any other figure link stays as written.
    report_text = drop_dead_abstract_links(report_text, lambda path: path.startswith("steps/"))
    linked_figures = set(linked_figure_paths(report_text))
    plan_steps = [step for step in ((req.get("plan") or {}).get("steps") or []) if isinstance(step, Mapping)]
    ordered_ids, ordered_steps = _topological_steps(plan_steps, results)
    # Keep a later cancelled attempt separate from the published result, including
    # when both attempts wrote files with the same name in different workdirs.
    bundle_results = dict(results)
    bundle_ids = list(ordered_ids)
    additional_names = []
    additional = req.get("bundle_additional_results") or {}
    if isinstance(additional, Mapping):
        for index, (original_id, result) in enumerate(sorted(additional.items()), 1):
            if original_id not in results or not isinstance(result, Mapping):
                continue
            name = f"cancelled_revision_{index}"
            while name in bundle_ids or name in bundle_results:
                name += "_"
            bundle_results[name] = result
            bundle_ids.append(name)
            additional_names.append((original_id, name))
    key_figures_declared = any("key_figures" in step for step in ordered_steps)
    key_figures: list[str] = []
    for step in ordered_steps:
        step_id = str(step.get("id") or "")
        outputs = {path for raw in step.get("outputs") or [] if isinstance(raw, str)
                   and (path := _safe_output_path(raw)) is not None}
        for raw in step.get("key_figures") or []:
            path = _safe_output_path(raw) if isinstance(raw, str) else None
            if path is not None and path in outputs:
                key_figures.append(PurePosixPath("steps", step_id, *path.parts).as_posix())
    max_file_bytes = int(float(settings.runner.bundle_max_file_mb) * 1024 * 1024)
    max_total_bytes = int(float(settings.runner.bundle_max_total_mb) * 1024 * 1024)
    max_total_files = int(settings.runner.bundle_max_files)
    copied_bytes = 0
    copied_files = 0
    total_limit_hit = False
    unreported: list[tuple[str, HeldDir, Path, PurePosixPath, str | None]] = []

    def copy_output(step_id: str, outputs: HeldDir, workdir: Path, path: PurePosixPath,
                    expected_sha256: str | None, python_command: str | None) -> dict[str, Any]:
        """Copy one output. ``expected_sha256`` None: an unreported file, copied as it is now (no run hash)."""
        nonlocal copied_bytes, copied_files, total_limit_hit, python_unknown
        tag = "" if expected_sha256 is not None else UNREPORTED_TAG
        output_rel = PurePosixPath(*path.parts[1:])
        rel = PurePosixPath("steps", step_id, *path.parts)
        source = workdir / Path(*path.parts)
        row = {"relative_path": rel.as_posix(), "size": "", "sha256": "",
               "step_id": step_id, "original_path": str(source), "status": f"copied{tag}",
               "rewritten": 0, "remaining_absolute_paths": "", "rewritten_files": ""}
        destination = temp / Path(*rel.parts)
        try:
            with _open_recorded_output(outputs, path) as (stream, info):
                row["size"] = info.st_size
                if info.st_size > max_file_bytes:
                    row["status"] = f"not copied{tag}: size"
                elif ((copied_files >= max_total_files or copied_bytes + info.st_size > max_total_bytes)
                      and rel.as_posix() not in linked_figures):
                    row["status"] = f"not copied{tag}: total limit"
                    total_limit_hit = total_limit_hit or not tag  # its appendix note counts recorded outputs
                else:
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    try:
                        source_sha256 = _copy_held(stream, info, destination)
                    except OSError:
                        destination.unlink(missing_ok=True)
                        row["status"] = f"not copied{tag}: changed since run"
                        return row
                    if expected_sha256 is not None and source_sha256.casefold() != expected_sha256.casefold():
                        destination.unlink(missing_ok=True)
                        row["status"] = "not copied: changed since run"
                        return row
                    copied_files += 1
                    copied_bytes += info.st_size
                    row.update(source_sha256=source_sha256, source_size=info.st_size)
                    suffix = destination.suffix.casefold()
                    command = python_command if suffix == ".py" else SCRIPT_COMMANDS.get(suffix)
                    if suffix == ".py" and command is None:
                        command = "python"
                        python_unknown = True
                    if command and "scripts" in output_rel.parts:
                        if _safe_script_path(rel):
                            script_path = PurePosixPath("outputs", *output_rel.parts)
                            script_commands.setdefault(step_id, {})[rel.as_posix()] = shlex.join(
                                ("cd", f"steps/{step_id}")) + " && " + shlex.join((command, script_path.as_posix()))
                        else:
                            unsafe_scripts.setdefault(step_id, set()).add(rel.as_posix())
        except FileNotFoundError:
            row["status"] = f"not copied{tag}: missing"
        except OSError:
            row["status"] = f"not copied{tag}: changed since run"
        return row

    def copy_job_script(step_id: str, jobs: HeldDir, workdir: Path, name: str,
                        expected_sha256: str) -> dict[str, Any]:
        nonlocal copied_bytes, copied_files, total_limit_hit
        relative = PurePosixPath("steps", step_id, "jobs", name)
        row = {"relative_path": relative.as_posix(), "size": "", "sha256": "",
               "step_id": step_id, "original_path": str(workdir / "jobs" / name), "status": "copied",
               "rewritten": 0, "remaining_absolute_paths": "", "rewritten_files": ""}
        destination = temp / Path(*relative.parts)
        try:
            with _open_recorded_output(jobs, PurePosixPath("jobs", name)) as (stream, info):
                row["size"] = info.st_size
                if info.st_size > max_file_bytes:
                    row["status"] = "not copied: size"
                elif copied_files >= max_total_files or copied_bytes + info.st_size > max_total_bytes:
                    row["status"] = "not copied: total limit"
                    total_limit_hit = True
                else:
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    current = _copy_held(stream, info, destination)
                    if current.casefold() != expected_sha256.casefold():
                        destination.unlink(missing_ok=True)
                        row["status"] = "not copied: changed since submission"
                    else:
                        copied_files += 1
                        copied_bytes += info.st_size
                        row.update(source_sha256=current, source_size=info.st_size)
                        copied_job_scripts.append(relative.as_posix())
        except (FileNotFoundError, NotPlainFolder):
            row["status"] = "not copied: missing"
        except OSError:
            destination.unlink(missing_ok=True)
            row["status"] = "not copied: changed since submission"
        return row

    try:
        found_workdirs = 0
        with ExitStack() as stack:
            for step_id in bundle_ids:
                if not _SAFE_ID.fullmatch(step_id):
                    raise ValueError(f"unsafe step id for bundle: {step_id!r}")
                result = bundle_results.get(step_id)
                if not isinstance(result, Mapping):
                    continue
                # A local WSL worker shares this exact drive root; unrelated remote hosts still refuse.
                from .wsl_paths import message_paths, shared_path
                task = (tasks or {}).get(str(result.get("task_id") or ""), {})
                caps = (runner_capabilities or {}).get(str(task.get("runner_id") or ""), {})
                shared_wsl = False
                if caps.get("runtime_env") == "wsl":
                    path_target = "windows" if os.name == "nt" else "wsl"
                    try:
                        shared_root = shared_path(str(caps.get("workspace_root") or ""), path_target)
                        shared_wsl = Path(shared_root).resolve() == root
                        if shared_wsl:
                            result = message_paths(result, path_target)
                    except ValueError:
                        raise RemoteRunnerBundle(REMOTE_RUNNER_NOTE)
                recorded = _recorded_outputs(result)
                held, workdir_or_reason, manifest = _held_workdir(stack, root, result, shared_wsl=shared_wsl)
                if held is None:
                    for index, (raw, safe, _expected) in enumerate(recorded, 1):
                        relative = (PurePosixPath("steps", step_id, *safe.parts).as_posix() if safe is not None
                                    else f"steps/{step_id}/<invalid-output-{index}>")
                        rows.append({"relative_path": relative, "size": "", "sha256": "",
                                     "step_id": step_id, "original_path": str(result.get("workdir") or raw),
                                     "status": "not copied: missing", "rewritten": 0,
                                     "remaining_absolute_paths": "", "rewritten_files": ""})
                    continue
                if shared_wsl:
                    manifest = message_paths(manifest, path_target)
                workdir = Path(workdir_or_reason)
                found_workdirs += 1
                workdirs.append((step_id, workdir))
                task_id = str(result.get("task_id") or "")
                run = (manifest.get("runs") or {}).get(task_id) if task_id else None
                run = run if isinstance(run, Mapping) else {}
                for producer, snapshot in (run.get("hpc_data_inputs") or {}).items():
                    source_task = (bundle_results.get(producer) or {}).get("task_id")
                    if (_SAFE_ID.fullmatch(str(producer)) and _SAFE_ID.fullmatch(str(source_task))
                            and isinstance(snapshot, str)
                            and Path(snapshot) == workdir.parent.parent / "data" / producer / source_task):
                        data_inputs.append((producer, Path(snapshot)))
                run_records[step_id] = {
                    key: value for key, value in {
                        "agent_id": run.get("agent_id", result.get("agent_id")),
                        "engine": run.get("engine", manifest.get("engine")),
                        "model": run.get("model", manifest.get("model")),
                        "engine_cli_version": run.get("engine_cli_version"),
                        "started_at": run.get("started_at"),
                        "ended_at": run.get("ended_at"),
                        "environment_record": run.get("environment_record"),
                    }.items() if value not in (None, "")
                }
                records = run.get("input_files") if isinstance(run, Mapping) else None
                for record in records or []:
                    if not isinstance(record, Mapping) or not isinstance(record.get("path"), str):
                        continue
                    protected = str(record.get("skipped") or "").casefold() in {"private", "restricted"}
                    input_path = "" if protected else record["path"]
                    if input_path == "inputs" or input_path.startswith("inputs/"):
                        input_path = PurePosixPath("steps", step_id, input_path).as_posix()
                    input_rows.append({
                        "step_id": step_id, "path": input_path,
                        "size": "" if record.get("size") is None else record.get("size"),
                        "mtime_ns": "" if record.get("mtime_ns") is None else record.get("mtime_ns"),
                        "sha256": "" if protected else record.get("sha256") or "",
                        "skipped": record.get("skipped") or "",
                        "cached": "yes" if record.get("cached") is True else "",
                    })
                try:
                    outputs = stack.enter_context(held.child("outputs"))
                except (NotPlainFolder, OSError):
                    for index, (raw, safe, _expected) in enumerate(recorded, 1):
                        relative = (PurePosixPath("steps", step_id, *safe.parts).as_posix() if safe is not None
                                    else f"steps/{step_id}/<invalid-output-{index}>")
                        source = workdir / Path(*safe.parts) if safe is not None else workdir / raw
                        rows.append({"relative_path": relative, "size": "", "sha256": "",
                                     "step_id": step_id, "original_path": str(source),
                                     "status": "not copied: missing", "rewritten": 0,
                                     "remaining_absolute_paths": "", "rewritten_files": ""})
                    continue
                python_command = _runner_python_command(result, tasks, runner_capabilities)
                for index, (raw, path, expected_sha256) in enumerate(recorded, 1):
                    if path is None:
                        rows.append({"relative_path": f"steps/{step_id}/<invalid-output-{index}>",
                                     "size": "", "sha256": "", "step_id": step_id,
                                     "original_path": raw, "status": "not copied: unsafe path", "rewritten": 0,
                                     "remaining_absolute_paths": "", "rewritten_files": ""})
                        continue
                    rows.append(copy_output(step_id, outputs, workdir, path, expected_sha256, python_command))
                listed = {raw for raw, _path, _sha in recorded}
                unreported += [(step_id, outputs, workdir, path, python_command)
                               for raw in result.get("unreported_outputs") or []
                               if isinstance(raw, str) and raw not in listed
                               and (path := _safe_output_path(raw)) is not None]
                submitted = _job_scripts(held, workdir)
                if submitted:
                    try:
                        jobs = stack.enter_context(held.child("jobs"))
                    except (NotPlainFolder, OSError):
                        jobs = None
                    for name, expected_sha256 in submitted:
                        if jobs is None:
                            rows.append({"relative_path": f"steps/{step_id}/jobs/{name}", "size": "", "sha256": "",
                                         "step_id": step_id, "original_path": str(workdir / "jobs" / name),
                                         "status": "not copied: missing", "rewritten": 0,
                                         "remaining_absolute_paths": "", "rewritten_files": ""})
                        else:
                            rows.append(copy_job_script(step_id, jobs, workdir, name, expected_sha256))
            # Files a step wrote without reporting them (a gene-set copy, a figure) can be what its scripts read
            # (#423). They are copied after every recorded output, so they never push one past the total limit.
            for step_id, outputs, workdir, path, python_command in unreported:
                rows.append(copy_output(step_id, outputs, workdir, path, None, python_command))
        if not found_workdirs:
            raise NoStepWorkdirs("단계 작업 폴더를 하나도 찾지 못했습니다")

        with (temp / "INPUTS.tsv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=INPUT_FIELDS, delimiter="\t", lineterminator="\n")
            writer.writeheader()
            writer.writerows(input_rows)
        input_hashed = sum(bool(row["sha256"]) for row in input_rows)
        input_skipped = sum(bool(row["skipped"]) for row in input_rows)
        skipped_by_reason = Counter(str(row["skipped"]) for row in input_rows if row["skipped"])
        input_summary = f"입력 {input_hashed}개 hash, {input_skipped}개 생략"
        if skipped_by_reason:
            input_summary += " (" + ", ".join(
                f"{reason} {count}" for reason, count in sorted(skipped_by_reason.items())) + ")"

        stored_package = req.get("report_package")
        package = (ReportPackage.from_dict(dict(stored_package)) if isinstance(stored_package, Mapping)
                   else build_report_package(dict(req), report_text, dict(results)))
        report = reader_markdown(with_report_front(report_text, package))
        from .report_format import split_appendix
        appendix, legacy = split_appendix(str(req.get("report_appendix") or ""))
        tool_calls = _tool_calls_section(ordered_ids, results)
        execution = "\n\n".join(p for p in [str(req.get("execution_record") or ""), legacy, tool_calls] if p)
        if additional_names:
            execution += "\n\n## Cancelled revision artifacts\n\n" + "\n".join(
                f"- {original}: steps/{name}/ (partial revision outputs)"
                for original, name in additional_names if _SAFE_ID.fullmatch(str(original)))
        replaced = _superseded(req, tasks)
        if replaced:
            execution += ("\n\n## 요청 묶음: 대체됨\n\n" +
                         "\n".join(f"- {item}" for item in replaced))
        if total_limit_hit:
            execution += ("\n\n- 누적 상한으로 기록 산출 일부를 복사하지 않음 "
                         f"(복사 {copied_files}개, {copied_bytes} bytes; "
                         f"상한 {max_total_files}개, {max_total_bytes} bytes)")
        not_copied = sum(str(row["status"]).startswith("not copied:") for row in rows)
        bundle_status = "incomplete" if not_copied else "complete"
        unreported_missed = sum(str(row["status"]).startswith(f"not copied{UNREPORTED_TAG}:") for row in rows)
        if unreported_missed:
            execution += f"\n\n- 보고하지 않은 산출 {unreported_missed}개 미복사(크기·누적 상한 등; MANIFEST.tsv 확인)"
        if not_copied:
            execution += f"\n\n- 요청 묶음 상태: incomplete (기록 산출 {not_copied}개 미복사; MANIFEST.tsv 확인)"
        (temp / "report.md").write_text(report, encoding="utf-8", newline="\n")
        (temp / "report_appendix.md").write_text(appendix, encoding="utf-8", newline="\n")
        (temp / "execution_record.md").write_text(execution, encoding="utf-8", newline="\n")
        from .recruit.paper_skills import records as paper_skill_records
        paper_records = paper_skill_records(req.get("plan") or {}, results)
        if paper_records:
            (temp / "paper_skills.json").write_text(json.dumps(paper_records, ensure_ascii=False, indent=2),
                                                  encoding="utf-8", newline="\n")
        # The report's claim anchors resolve against these ledgers; without them a colleague holding only the bundle
        # could not follow a number to its evidence (readiness R24, 2026-10-08).
        contract = req.get("research_contract")
        final_checkpoint = isinstance(contract, Mapping) and contract.get("final_checkpoint") is True
        if final_checkpoint:
            (temp / "final_checkpoint.json").write_text(json.dumps({
                "contract_version": contract.get("schema_version"), "request_id": req.get("id"),
                "draft": contract.get("final_draft"), "receipt": contract.get("final_receipt")},
                ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
        claims = isinstance(contract, Mapping) and bool(contract.get("plan_sha256"))
        if claims:
            copied_paths = {str(row["relative_path"]) for row in rows if str(row.get("status", "")).startswith("copied")}
            record = _bundle_claims(claims_record(req, report_check=contract.get("report_check")), results,
                                    copied_paths)
            if final_checkpoint and isinstance(record.get("final_draft"), dict):
                # The canonical pre-rewrite text lives in final_checkpoint.json.
                record["final_draft"].pop("content", None)
            (temp / "claims.json").write_text(json.dumps(record, ensure_ascii=False, indent=2, default=str),
                                              encoding="utf-8", newline="\n")
        commands =[command for step_id in ordered_ids
                    for _path, command in sorted(script_commands.get(step_id, {}).items())]
        unsafe = [path for step_id in ordered_ids for path in sorted(unsafe_scripts.get(step_id, set()))]
        bundled = {step_id for step_id, _workdir in workdirs}
        links = {str(step.get("id")): deps for step in ordered_steps
                 if str(step.get("id")) in bundled
                 and (deps := [str(dep) for dep in step.get("depends_on") or [] if str(dep) in bundled])}
        if links:
            (temp / LINK_SCRIPT).write_text(LINK_SCRIPT_BODY.replace("__LINKS__", json.dumps(links, sort_keys=True)),
                                            encoding="utf-8", newline="\n")
        if rerun_receipt:
            (temp / RERUN_RECEIPT_FILE).write_text(
                json.dumps(rerun_receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8", newline="\n")
        if key_figures_declared:
            (temp / FIGURE_PLAN_FILE).write_text(
                json.dumps({"key_figures": list(dict.fromkeys(key_figures))}, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8", newline="\n")
        portable_refs.update(str(row['relative_path']) for row in rows
                             if str(row.get('status', '')).startswith('copied'))
        portable_refs.update(name for name in ('report.md', 'report_appendix.md', 'execution_record.md', 'INPUTS.tsv',
                                              'claims.json', 'paper_skills.json', LINK_SCRIPT,
                                              RERUN_RECEIPT_FILE, FIGURE_PLAN_FILE) if (temp / name).is_file())
        redacted_files = set()

        def hide_readme_front(front):
            cleaned = public_text(front)
            if cleaned != front:
                redacted_files.add('README.md')
            return cleaned

        (temp / "README.md").write_text(_readme(req, ordered_steps, commands, unsafe, python_unknown, bool(links),
                                                 claims, copied_job_scripts,
                                                 hide_front=hide_readme_front if public_text else None),
                                          encoding="utf-8", newline="\n")

        rewritten_files = 0
        remaining: set[str] = set()
        rewrite_by_file: dict[str, tuple[int, list[str]]] = {}
        for path in sorted(p for p in temp.rglob("*") if p.is_file() and p.suffix.casefold() in TEXT_SUFFIXES):
            if path == temp / "final_checkpoint.json":
                continue  # hash-bound PI draft: portable report.md may rewrite paths, this record never does
            count, paths = _rewrite_text(path, workdirs, data_inputs)
            if count:
                rewritten_files += 1
            relative = path.relative_to(temp).as_posix()
            rewrite_by_file[relative] = (count, paths)
            remaining.update(paths)

        if public_text:
            for path in sorted(p for p in temp.rglob('*') if p.is_file()):
                if path in (temp / 'README.md', temp / 'INPUTS.tsv', temp / LINK_SCRIPT, temp / RERUN_RECEIPT_FILE,
                            temp / FIGURE_PLAN_FILE):
                    continue  # typed inventory, path-free receipt and generated relative-path helpers
                record_fields = ()
                if path.parent == temp and path.name in ('claims.json', 'paper_skills.json'):
                    record_fields = _PUBLIC_RECORD_FIELDS
                    if path.name == 'claims.json' and final_checkpoint:
                        record_fields |= {'final_draft', 'final_receipt'}  # checked by the canonical checkpoint
                if _redact_bundle_text(path, public_text, hash_bound=path == temp / 'final_checkpoint.json',
                                       record_fields=record_fields):
                    redacted_files.add(path.relative_to(temp).as_posix())
            for row in rows:
                row['redacted'] = 'yes' if row['relative_path'] in redacted_files else ''
                # Inventory names identify the unchanged files; hide the descriptive fields only.
                for key in ('original_path', 'remaining_absolute_paths'):
                    if key in row:
                        row[key] = public_text(row[key])
            for row in input_rows:
                row['path'] = public_text(row['path'])
            inputs_path = temp / 'INPUTS.tsv'
            previous_inputs = inputs_path.read_bytes()
            with inputs_path.open('w', encoding='utf-8', newline='') as handle:
                writer = csv.DictWriter(handle, fieldnames=INPUT_FIELDS, delimiter='\t', lineterminator='\n')
                writer.writeheader()
                writer.writerows(input_rows)
            if inputs_path.read_bytes() != previous_inputs:
                redacted_files.add('INPUTS.tsv')
            run_records = {step_id: _public_values(run, public_text, redact_keys=False,
                                                 preserve_fields=_PUBLIC_RECORD_FIELDS)
                           for step_id, run in run_records.items()}
            rewrite_by_file = {name: (count, [public_text(p) for p in paths])
                               for name, (count, paths) in rewrite_by_file.items()}
            remaining = {public_text(p) for p in remaining}
            input_summary = public_text(input_summary)

        # The figure check reads the rewritten report: an image linked by its absolute workdir path now points inside the
        # bundle. It is the same check `labhq verify` runs later.
        figure_check = bundle_figure_check(temp)
        figure_findings = [*figure_check["problems"], *(f"WARN: {warning}" for warning in figure_check["warnings"])]
        if public_text:
            figure_findings = _public_values(figure_findings, public_text)
        if figure_findings:
            with (temp / "execution_record.md").open("a", encoding="utf-8", newline="\n") as handle:
                handle.write("\n\n## 보고서 그림 링크\n\n" + "\n".join(f"- {finding}" for finding in figure_findings))

        grade, grade_reasons = _grade(rows, ordered_steps, rewrite_by_file, not_copied, rerun_receipt)
        if any('/jobs/' in name or '/outputs/scripts/' in name for name in redacted_files):
            grade = 'documented'
            grade_reasons.append('Site-specific script operands were redacted; review before rerunning.')
        with (temp / "README.md").open("a", encoding="utf-8", newline="\n") as handle:
            handle.write("\n".join(["## 재현 등급", "", f"- `{grade}`", *(f"- {reason}" for reason in grade_reasons),
                                    f"- {input_summary}",
                                    "- `replayable`: 기록 산출이 모두 있고, 데이터를 낸 단계마다 스크립트, 스크립트를 쓴 "
                                    "단계마다 환경 기록이 있으며 스크립트에 절대경로가 없음",
                                    "- `rerun_verified`는 다른 곳에서 다시 돌린 기록이 있을 때만 줍니다. 유효한 receipt"
                                    + ("가 있습니다." if rerun_receipt else "는 이 묶음에 없습니다."), ""]))
        bundle_note = ["", "## 요청 묶음 변환", "", f"- 절대경로를 바꾼 파일: {rewritten_files}개",
                       f"- 재현 등급: {grade}" + (f" ({'; '.join(grade_reasons)})" if grade_reasons else "")]
        bundle_note.append(f"- {input_summary}")
        if public_text:
            bundle_note.append(f"- Public HPC copies: {len(redacted_files)} files redacted with the job-log policy; "
                               "source artifacts unchanged (MANIFEST: redacted, source_sha256, source_size).")
        bundle_note.append("- 남은 절대경로: " + (", ".join(sorted(remaining)) if remaining else "없음"))
        if replaced:
            bundle_note.append(f"- 대체되어 제외한 판: {len(replaced)}개")
        if public_text:
            bundle_note = _public_values(bundle_note, public_text)
        appendix_path = temp / "execution_record.md"
        with appendix_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write("\n".join(bundle_note) + "\n")

        generated = [temp / "README.md", temp / "report.md", temp / "report_appendix.md", appendix_path, temp / "INPUTS.tsv",
                      *([temp / FIGURE_PLAN_FILE] if (temp / FIGURE_PLAN_FILE).is_file() else []),
                      *([temp / "paper_skills.json"] if paper_records else []),
                      *([temp / "claims.json"] if claims else []),
                      *([temp / "final_checkpoint.json"] if final_checkpoint else []),
                      *([temp / LINK_SCRIPT] if (temp / LINK_SCRIPT).is_file() else []),
                      *([temp / RERUN_RECEIPT_FILE] if (temp / RERUN_RECEIPT_FILE).is_file() else [])]
        for path in generated:
            relative = path.relative_to(temp).as_posix()
            count, paths = rewrite_by_file.get(relative, (0, []))
            rows.append({"relative_path": relative, "size": path.stat().st_size, "sha256": _sha256(path),
                         "step_id": "", "original_path": "", "status": "generated", "rewritten": count,
                         "remaining_absolute_paths": " | ".join(paths), "rewritten_files": ""})
            rows[-1]['redacted'] = 'yes' if relative in redacted_files else ''
        for row in rows:
            if row["status"] in ("copied", f"copied{UNREPORTED_TAG}"):
                copied = temp / str(row["relative_path"])
                count, paths = rewrite_by_file.get(str(row["relative_path"]), (0, []))
                row.update(size=copied.stat().st_size, sha256=_sha256(copied), rewritten=count,
                           remaining_absolute_paths=" | ".join(paths))
        crate_warning = None
        try:
            crate_path = temp / METADATA_FILE
            # RO-Crate selects request identifiers/status/engine metadata; descriptive run values are sanitized above.
            write_ro_crate(crate_path, req, rows, input_rows, run_records, grade, grade_reasons, rerun_receipt)
            rows.append({"relative_path": METADATA_FILE, "size": crate_path.stat().st_size,
                         "sha256": _sha256(crate_path), "step_id": "", "original_path": "",
                         "status": "generated", "rewritten": 0, "remaining_absolute_paths": "",
                         "rewritten_files": ""})
        except Exception as exc:
            crate_warning = f"RO-Crate metadata를 만들지 못했습니다: {exc}"
            if public_text:
                crate_warning = public_text(crate_warning)
            for path in (temp / "README.md", appendix_path):
                with path.open("a", encoding="utf-8", newline="\n") as handle:
                    handle.write(f"\n- {crate_warning}\n")
                relative = path.relative_to(temp).as_posix()
                row = next(item for item in rows if item["relative_path"] == relative)
                row.update(size=path.stat().st_size, sha256=_sha256(path))
        environment_records = {step_id: run["environment_record"] for step_id, run in run_records.items()
                               if isinstance(run.get("environment_record"), Mapping)}
        rows.append({"relative_path": ".", "size": "", "sha256": "", "step_id": "",
                      "original_path": "", "status": "summary", "rewritten": "",
                      "remaining_absolute_paths": " | ".join(sorted(remaining)),
                      "rewritten_files": rewritten_files, "reproducibility_grade": grade,
                      "reproducibility_reasons": json.dumps(grade_reasons, ensure_ascii=False),
                      "environment_records": json.dumps(environment_records, ensure_ascii=False, sort_keys=True),
                      "rerun_receipt": json.dumps(rerun_receipt, ensure_ascii=False, sort_keys=True)
                      if rerun_receipt else ""})
        with (temp / "MANIFEST.tsv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=MANIFEST_FIELDS, delimiter="\t", lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)

        _clear_owned_dir(target, requests_root, requests_held)
        if not requests_held.same_as(requests_root):
            raise OSError("request bundle parent changed while the bundle was being built")
        _replace_dir(temp, target)
        return {"path": str(target), "status": bundle_status, "grade": grade, "grade_reasons": grade_reasons,
                "not_copied": not_copied,
                "rewritten_files": rewritten_files,
                "remaining_absolute_paths": sorted(remaining), "superseded": replaced,
                "conversion_record": "\n".join(bundle_note) + ("\n\n## 보고서 그림 링크\n" + "\n".join(figure_findings)
                                                                  if figure_findings else ""),
                **({"crate_warning": crate_warning} if crate_warning else {})}
    except Exception:
        if temp.exists():
            try:
                _clear_owned_dir(temp, requests_root, requests_held)
            except OSError:
                pass
        raise
