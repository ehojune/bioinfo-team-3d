"""Bounded, text-only handoff for a bioinfo-agent pipeline contribution."""

from __future__ import annotations

import json
import re
from pathlib import Path, PurePosixPath
from typing import Any

from .adapters.owned import read_owned
from .settings import Settings

PIPELINE_MANIFEST = "manifest.json"
PIPELINE_SCHEMA = 1
PIPELINE_MAX_FILES = 100
PIPELINE_MAX_BYTES = 1_000_000
PIPELINE_NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,62}")
PIPELINE_DATA_SUFFIXES = {
    ".bam", ".bai", ".cram", ".crai", ".sam", ".fastq", ".fq", ".fasta", ".fa", ".fna",
    ".vcf", ".bcf", ".bed", ".bigwig", ".bw", ".h5", ".h5ad", ".parquet", ".zip", ".gz", ".tar",
}
LOCAL_ABSOLUTE_PATH = re.compile(
    r"(?i)(?<![A-Za-z0-9])(?:[A-Za-z]:[\\/]|\\\\[^\\\s]+\\[^\\\s]+|~[\\/]|"
    r"/(?:home|users|bio|scratch|private|mnt/[a-z])/)"
)


def collect_pipeline_submission(workdir: Path, outputs: list[str], scan_note: str | None = None) -> dict | None:
    """Read one declared pipeline bundle without following links. Nothing outside its output folder is copied."""
    manifests = []
    for output in outputs:
        parts = PurePosixPath(output).parts
        if len(parts) == 4 and parts[:2] == ("outputs", "pipeline") and parts[-1] == PIPELINE_MANIFEST:
            manifests.append(output)
    if not manifests:
        return None
    if len(manifests) != 1:
        return {"state": "rejected", "reason": "pipeline manifest must be unique"}
    if scan_note:
        return {"state": "rejected", "reason": f"pipeline output scan incomplete: {scan_note}"}

    manifest_path = PurePosixPath(manifests[0])
    folder_name = manifest_path.parts[2]
    try:
        raw = read_owned(workdir, manifest_path.as_posix())
        manifest = json.loads(raw or "")
    except (OSError, UnicodeError, ValueError):
        return {"state": "rejected", "name": folder_name, "reason": "pipeline manifest is unreadable"}
    if not isinstance(manifest, dict) or manifest.get("schema") != PIPELINE_SCHEMA:
        return {"state": "rejected", "name": folder_name, "reason": "pipeline manifest schema must be 1"}
    name = str(manifest.get("name") or "")
    if name != folder_name or not PIPELINE_NAME.fullmatch(name):
        return {"state": "rejected", "name": folder_name, "reason": "pipeline manifest name is invalid"}

    prefix = PurePosixPath("outputs", "pipeline", name)
    files: list[dict[str, str]] = []
    total = 0
    for output in outputs:
        path = PurePosixPath(output)
        if path == manifest_path or path.parts[:3] != prefix.parts:
            continue
        relative = PurePosixPath(*path.parts[3:])
        if not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
            return {"state": "rejected", "name": name, "reason": "pipeline file path is invalid"}
        if len(files) >= PIPELINE_MAX_FILES:
            return {"state": "rejected", "name": name, "reason": "pipeline has too many files"}
        try:
            content = read_owned(workdir, path.as_posix())
        except (OSError, UnicodeError):
            content = None
        if content is None:
            return {"state": "rejected", "name": name, "reason": f"pipeline file is not plain UTF-8 text: {relative}"}
        total += len(content.encode("utf-8"))
        if total > PIPELINE_MAX_BYTES:
            return {"state": "rejected", "name": name, "reason": "pipeline text exceeds 1 MB"}
        files.append({"path": f"pipelines/{name}/{relative.as_posix()}", "content": content})
    return {
        "state": "ready", "schema": PIPELINE_SCHEMA, "name": name,
        "title": str(manifest.get("title") or name).strip(),
        "summary": str(manifest.get("summary") or "").strip(), "files": files,
    }


def pipeline_rejection(submission: dict, settings: Settings) -> str | None:
    """Why a bundle cannot cross the runner/gateway boundary into a public repository."""
    from .integrations.github import sanitize  # shared public scanner; delayed to avoid an import cycle

    if submission.get("state") == "rejected":
        return str(submission.get("reason") or "pipeline bundle rejected by runner")
    name = str(submission.get("name") or "")
    files = submission.get("files")
    if not PIPELINE_NAME.fullmatch(name) or not isinstance(files, list) or not files:
        return "pipeline manifest or files are invalid"
    required = {f"pipelines/{name}/main.nf", f"pipelines/{name}/nextflow.config",
                f"pipelines/{name}/README.md"}
    paths: set[str] = set()
    texts = [str(submission.get("title") or ""), str(submission.get("summary") or "")]
    for item in files:
        if not isinstance(item, dict):
            return "pipeline file record is invalid"
        path, content = str(item.get("path") or ""), item.get("content")
        pure = PurePosixPath(path)
        if (not isinstance(content, str) or path in paths or pure.is_absolute() or "\\" in path
                or any(part in {"", ".", ".."} for part in pure.parts)
                or pure.parts[:2] != ("pipelines", name)):
            return "pipeline file path or content is invalid"
        if pure.suffix.casefold() in PIPELINE_DATA_SUFFIXES:
            return f"data file is not allowed in pipeline PR: {path}"
        paths.add(path)
        texts.append(content)
    if not required.issubset(paths):
        return "pipeline must include main.nf, nextflow.config and README.md"
    for text in texts:
        if re.search(r"\bYuan\b", text, re.IGNORECASE):
            return "private knowledge-store references are not allowed"
        if LOCAL_ABSOLUTE_PATH.search(text):
            return "local absolute path is not allowed"
        if sanitize(text, settings.policy, [settings.gateway.client_token, settings.gateway.runner_token],
                    limit=None) != text:
            return "secret or restricted-zone text is not allowed"
    return None
