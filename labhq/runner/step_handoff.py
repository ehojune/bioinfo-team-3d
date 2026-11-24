"""Verify runner-recorded HPC receipts for explicitly pinned PC inputs."""
from __future__ import annotations

import re

from ..hpc_transfer import TransferError, validate_remote_manifest
from ..util import INPUT_STEP_ID, output_relpath


def artifact_contract_for(task):
    return (task.meta.get("kind") in {"step", "result_correction", "slot_repair", "analysis_draft"}
            and isinstance(task.meta.get("upstream_artifacts"), dict))


def required_hpc_revisions(task, whole_hpc, artifact_contract):
    """Read the dispatcher-provided producer revisions. Ordinary tasks have no handoff."""
    if 'hpc_ancestor_revisions' not in task.meta:
        return {}
    revisions = task.meta['hpc_ancestor_revisions']
    if (whole_hpc or (task.meta.get('execution_location') or {}).get('mode') != 'local'
            or not artifact_contract or not isinstance(revisions, dict)
            or any(not isinstance(step, str) or not INPUT_STEP_ID.fullmatch(step) or step.endswith('.')
                   or type(revision) is not int or revision < 0 for step, revision in revisions.items())):
        raise ValueError('invalid HPC-to-PC ancestor mapping')
    return revisions


def require_hpc_snapshots(revisions, unavailable):
    failures = [unavailable[step] for step in revisions if step in unavailable]
    if failures:
        raise ValueError('HPC-to-PC input unavailable: ' + '; '.join(failures))


def _check_paths(outputs):
    """Reject file/folder conflicts and aliases, including case aliases on a PC."""
    seen = {}
    for path in outputs:
        if not isinstance(path, str) or output_relpath(path) != path or not path.startswith('outputs/'):
            raise ValueError('invalid collected HPC output path')
        prefix = 'outputs'
        parts = path.split('/')[1:]
        for index, part in enumerate(parts):
            prefix += '/' + part
            entry = prefix, index == len(parts) - 1
            if seen.setdefault(prefix.casefold(), entry) != entry:
                raise ValueError(f'{path}: colliding HPC output path')


def verified_hpc_sizes(run, artifact, request_id, revision):
    """Return the remote sizes after matching the selected successful run and its local receipts."""
    if (type(run.get('revision')) is not int or run['revision'] != revision
            or run.get('kind') != 'step' or run.get('pending_jobs') != []):
        raise ValueError('HPC receipt is not from the selected completed step revision')
    manifests = run.get('hpc_remote_outputs')
    receipt = run.get('hpc_local_receipt')
    if not isinstance(manifests, list) or not manifests or not isinstance(receipt, dict):
        raise ValueError('HPC output receipt is missing for the selected task')
    outputs, hashes = artifact.get('outputs'), artifact.get('output_sha256')
    if not isinstance(outputs, list) or not isinstance(hashes, dict):
        raise ValueError('collected HPC output mapping is missing')
    _check_paths(outputs)
    paths = set(outputs)
    if (len(outputs) != len(paths) or outputs != run.get('outputs')
            or hashes != run.get('output_sha256')):
        raise ValueError('collected HPC outputs differ from the selected task')

    received = receipt.get('sha256')
    omissions = receipt.get('omissions')
    if not isinstance(received, dict) or not isinstance(omissions, list):
        raise ValueError('HPC output receipt is incomplete')
    missing = {}
    for row in omissions:
        if not isinstance(row, dict) or not isinstance(row.get('path'), str) or not isinstance(row.get('reason'), str):
            raise ValueError('invalid HPC output omission')
        size = f" ({row['bytes']} bytes)" if type(row.get('bytes')) is int else ''
        missing[row['path']] = f"{row['path']}{size}: {row['reason']}"

    sizes = {}
    for manifest in manifests:
        try:
            validate_remote_manifest(manifest, request_id)
        except TransferError as exc:
            raise ValueError('invalid HPC output manifest') from exc
        if manifest.get('skipped'):
            raise ValueError('HPC output manifest is incomplete')
        for row in manifest['files']:
            path = 'outputs/' + row['path']
            if path in sizes or path not in paths:
                raise ValueError(f'{path}: ambiguous or uncollected HPC output')
            sizes[path] = row['bytes']
            digest = row.get('sha256')
            if not isinstance(digest, str) or not re.fullmatch('[a-f0-9]{64}', digest):
                raise ValueError(f'{path}: missing remote SHA-256')
            if received.get(path) != digest or hashes.get(path) != digest:
                missing.setdefault(path, f'{path}: local copy not verified')
    for path in outputs:
        if path not in sizes:
            missing.setdefault(path, f'{path}: absent from the verified HPC inventory')
    if missing:
        detail = '; '.join(list(missing.values())[:5])
        raise ValueError(detail + (f'; {len(missing) - 5} more' if len(missing) > 5 else ''))
    if set(received) != set(sizes):
        raise ValueError('HPC receipt contains files outside the selected inventory')
    return sizes
