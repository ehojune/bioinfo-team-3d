"""Verify runner-recorded HPC receipts for explicitly pinned PC inputs."""
from __future__ import annotations

import re

from ..hpc_transfer import TransferError, local_copy_conflicts, validate_remote_manifest
from ..util import INPUT_STEP_ID, output_relpath
from .hpc_fetch import missing_received_outputs


def mixed_step_input_refusal(task, agent, runner_id):
    location = task.meta.get('execution_location') or {}
    if location.get('scope') != 'step':
        return None
    kind = task.meta.get('kind')
    step_kinds = {'step', 'result_correction', 'slot_repair', 'wrap_up', 'facilities_fix'}
    compact_handoff = kind in {'analysis_draft', 'audit_repair'} and location.get('mode') == 'local'
    if (location.get('runner_id') != runner_id or location.get('workspace') != task.request_id
            or location.get('mode') not in {'local', 'hpc'}
            or not (kind in step_kinds or compact_handoff)):
        return 'invalid mixed step runner or request binding'
    if location['mode'] == 'hpc' and (
            agent.project_dirs or any(task.meta.get(key) for key in (
                'reference_dirs', 'project_dirs', 'references', 'precedent_texts', 'hpc_input_approval'))
            or any((task.meta.get('reuse_input_request') or {}).values())
            or any((task.meta.get('reuse_input_meta') or {}).values())):
        return 'mixed HPC steps cannot read PC reference or project inputs'
    return None


def artifact_contract_for(task):
    kind = task.meta.get("kind")
    verified_wrap_up = kind == "wrap_up" and "hpc_ancestor_revisions" in task.meta
    return ((verified_wrap_up or kind in {"step", "result_correction", "slot_repair", "analysis_draft", "audit_repair"})
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


def mixed_hpc_step(task):
    return (task.meta.get('kind') == 'step'
            and (task.meta.get('execution_location') or {}).get('scope') == 'step')


def _declared(path, declarations):
    return any(path == name or path.startswith(name + '/') for name in declarations)


def _optional(path, required, optional):
    return _declared(path, optional) and not _declared(path, required)


def remote_output_name(name, declarations):
    """Map a job-relative name to the output root when the plan declares the shorter path."""
    nested = name.removeprefix('outputs/')
    if (nested != name and not _declared('outputs/' + name, declarations)
            and _declared('outputs/' + nested, declarations)):
        return nested
    return name


def _local_source(name, manifest, declarations):
    output = 'outputs/' + remote_output_name(name, declarations)
    # An unchanged input was uploaded from the step rather than fetched back.
    source = output if name in manifest.get('unchanged_inputs', []) else 'outputs/' + name
    return output, source


def project_hpc_outputs(manifests, declarations):
    """Build a local view while keeping the verified remote inventory in its original namespace."""
    sources, projected = {}, []
    for manifest in manifests:
        files = []
        for row in manifest['files']:
            output, source = _local_source(row['path'], manifest, declarations)
            if output in sources:
                raise ValueError(f'{output}: ambiguous HPC output')
            sources[output] = source
            files.append({**row, 'path': output.removeprefix('outputs/')})
        hashes = {remote_output_name(name, declarations): digest
                  for name, digest in manifest.get('_local_copy_sha256', {}).items()}
        skipped = [{**row, 'path': remote_output_name(row['path'], declarations)}
                   for row in manifest.get('_fetch_skipped', [])]
        projected.append({**manifest, 'files': files, '_local_copy_sha256': hashes, '_fetch_skipped': skipped})
    _check_paths(sources)
    return projected, sources


def local_hpc_output_receipt(ws, manifests, hashes, output_records):
    """Confirm local copies using the raw fetch paths and record their declared input names."""
    declared = ws.task.meta.get('outputs', [])
    required = ws.task.meta.get('required_outputs', declared)
    optional = ws.task.meta.get('optional_outputs', [])
    layout = {}
    local_view = manifests
    sources = {}
    if mixed_hpc_step(ws.task):
        layout = {
            'required_outputs': [rel for name in required if (rel := output_relpath(name))],
            'optional_outputs': [rel for name in optional if (rel := output_relpath(name))],
        }
        declarations = layout['required_outputs'] + layout['optional_outputs']
        local_view, sources = project_hpc_outputs(manifests, declarations)

    paths = ['outputs/' + row['path'] for manifest in local_view for row in manifest['files']]
    receipts = {'outputs/' + name: digest for manifest in local_view
                for name, digest in manifest.get('_local_copy_sha256', {}).items()}
    conflicts = local_copy_conflicts(ws.dir, paths, hashes, receipts)
    nested = {path: source for path, source in sources.items() if source != path}
    if nested:
        scan = {row['path']: row['sha256'] for row in output_records if isinstance(row.get('sha256'), str)}
        nested_conflicts = set(local_copy_conflicts(
            ws.dir, list(nested.values()),
            {source: scan[source] for source in nested.values() if source in scan},
            {source: receipts[path] for path, source in nested.items() if path in receipts}))
        conflicts.extend(path for path, source in nested.items() if source in nested_conflicts)
        for path, source in nested.items():
            if source in scan and path not in conflicts:
                hashes[path] = scan[source]

    declared_rels = [rel for name in [*declared, *required, *optional] if (rel := output_relpath(name))]
    missing = missing_received_outputs(declared_rels, local_view, hashes)
    for manifest in manifests:
        manifest.pop('_local_copy_sha256', None)
        manifest.pop('_fetch_skipped', None)
    return paths, {'sha256': receipts, 'omissions': missing, **layout}, conflicts


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


def verified_hpc_inputs(run, artifact, request_id, revision):
    """Match collected paths to their verified sizes and step-relative copy sources."""
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
    required = receipt.get('required_outputs', [])
    optional = receipt.get('optional_outputs', [])
    for declarations in (required, optional):
        if (not isinstance(declarations, list) or any(not isinstance(name, str)
                or output_relpath(name) != name for name in declarations)):
            raise ValueError('invalid HPC receipt declarations')
    declarations = required + optional
    missing = {}
    over_limit = set()
    for row in omissions:
        if not isinstance(row, dict) or not isinstance(row.get('path'), str) or not isinstance(row.get('reason'), str):
            raise ValueError('invalid HPC output omission')
        path = row['path']
        if path in missing:
            raise ValueError('duplicate HPC output omission')
        size = f" ({row['bytes']} bytes)" if type(row.get('bytes')) is int else ''
        missing[path] = f"{path}{size}: {row['reason']}"
        if (row['reason'] == 'fetch size limit' and path not in received and path not in hashes
                and _optional(path, required, optional)):
            over_limit.add(path)

    files = {}
    for manifest in manifests:
        try:
            validate_remote_manifest(manifest, request_id)
        except TransferError as exc:
            raise ValueError('invalid HPC output manifest') from exc
        if manifest.get('skipped'):
            raise ValueError('HPC output manifest is incomplete')
        for row in manifest['files']:
            path, source = _local_source(row['path'], manifest, declarations)
            if path in files or path not in paths:
                raise ValueError(f'{path}: ambiguous or uncollected HPC output')
            files[path] = row['bytes'], source
            digest = row.get('sha256')
            if not isinstance(digest, str) or not re.fullmatch('[a-f0-9]{64}', digest):
                raise ValueError(f'{path}: missing remote SHA-256')
            if path in over_limit:
                missing.pop(path, None)
                continue
            if received.get(path) != digest or hashes.get(path) != digest:
                missing.setdefault(path, f'{path}: local copy not verified')
    # Drop only optional files present in the remote inventory and explicitly left there for size.
    for path in over_limit.intersection(files):
        del files[path]
    for path in outputs:
        if path not in files and not _optional(path, required, optional):
            missing.setdefault(path, f'{path}: absent from the verified HPC inventory')
    if not files and not missing:
        raise ValueError('no collected output in the verified HPC inventory')
    if missing:
        detail = '; '.join(list(missing.values())[:5])
        raise ValueError(detail + (f'; {len(missing) - 5} more' if len(missing) > 5 else ''))
    if set(received) != set(files):
        raise ValueError('HPC receipt contains files outside the selected inventory')
    return files
