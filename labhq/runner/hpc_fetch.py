"""Describe declared HPC output files that were not verified as local copies."""


def missing_received_outputs(required, manifests, local_hashes):
    """List omissions without changing remote output admission or task success."""
    def declared(path):
        return any(path == rel or path.startswith(rel.rstrip('/') + '/') for rel in required)

    missing = {}
    current_names = {row['path'] for manifest in manifests for row in manifest.get('files', [])}
    for manifest in manifests:
        receipts = manifest.get('_local_copy_sha256', {})
        skipped = {row['path']: row for row in manifest.get('_fetch_skipped', [])
                   if isinstance(row, dict) and isinstance(row.get('path'), str)}
        for row in manifest.get('files', []):
            name = row['path']
            path = 'outputs/' + name
            if not declared(path):
                continue
            if path in local_hashes and receipts.get(name) == local_hashes[path]:
                continue
            reason = skipped.get(name, {}).get('reason', 'local copy not verified')
            if reason == 'retained in request remote data':
                reason = 'fetch size limit'
            missing[path] = {'path': path, 'bytes': row.get('bytes'), 'reason': reason}
        for name, row in skipped.items():
            path = 'outputs/' + name
            if declared(path) and name not in current_names:
                missing[path] = {'path': path, 'bytes': row.get('bytes'),
                                 'reason': row.get('reason', 'not fetched')}
    return list(missing.values())
