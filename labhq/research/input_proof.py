"""Bounded, fail-closed local input proof. Never grants permission to read an input."""
from __future__ import annotations

import hashlib
import json
import os
import stat
from itertools import islice
from pathlib import Path


def _logical_path(path):
    from ..wsl_paths import shared_path
    try:
        return shared_path(str(path), 'windows')
    except ValueError:
        return str(path).replace('\\', '/')


IDENTIFIER_KINDS = ("doi", "pmid")  # name one work; github and url name a place whose content can change


def _identifier(ref):
    """`doi:10.x/y` or `pmid:123` for a reference that is only an identifier, else None."""
    value = ref.get('value')
    if ref.get('kind') not in IDENTIFIER_KINDS or not isinstance(value, str) or not value.strip():
        return None
    value = value.strip()
    return f"{ref['kind']}:{value.casefold() if ref['kind'] == 'doi' else value}"


def capture(request, settings, meta=None, *, allow_request_projects=False):
    """Return a content manifest, or None when any input cannot be proved on this host.

    Reference paths must already be within configured reference roots. For reuse evidence only,
    explicitly attached request project directories may be scanned within their own boundaries.
    Links, special files, private
    paths, controlled zones and incomplete scans are declined before reading file contents.
    DOI and PMID references carry no content receipt: they are listed under `unreceipted`, so reuse needs the
    same identifiers but is not proof that the cited work is unchanged (#1061). Other references decline.
    """
    from ..intake import reference_roots, overlaps_restricted, overlaps_zone
    from ..private_paths import resolve_private_paths, credential_path

    try:
        refs = request.get('references') or []
        paths = []
        unreceipted = []
        for ref in refs:
            if not isinstance(ref, dict) or not ref.get('value'):
                return None
            if ref.get('kind') == 'path':
                paths.append((ref['value'], False))
            elif (identifier := _identifier(ref)) is not None:
                unreceipted.append(identifier)
            else:
                return None  # URLs/repositories have no local content receipt.
        projects = {str(p) for p in request.get('project_dirs') or []} if allow_request_projects else set()
        for source in (request, meta or {}):
            paths.extend((p, False) for p in source.get('reference_dirs') or [])
            paths.extend((p, str(p) in projects) for p in source.get('project_dirs') or [])
        roots = ([Path(p).resolve(strict=True) for p in reference_roots(settings)]
                 if any(not project for _, project in paths) else [])
        zones = [settings.path(z.path).resolve() for z in settings.policy.data_zones if z.level == 'restricted']
        private = resolve_private_paths(settings)
        maximum = settings.runner.reference_scan_max_entries
        depth_limit = settings.runner.reference_scan_max_depth
        budget = 256 * 1024 * 1024
        count = 0
        rows = []

        def checked(path, project_root=None):
            # Check each ancestor too: resolve() must not hide junctions or symlink roots.
            for ancestor in (path, *path.parents):
                info = ancestor.lstat()
                if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
                    raise ValueError('link')
            resolved = path.resolve(strict=True)
            if (not any(resolved == r or resolved.is_relative_to(r) for r in ([project_root] if project_root else roots))
                    or overlaps_restricted(str(path), settings) or overlaps_zone(resolved, zones)
                    or credential_path(str(resolved))
                    or any(resolved == Path(p) or resolved.is_relative_to(Path(p))
                           or Path(p).is_relative_to(resolved) for p in private.paths)):
                raise ValueError('guard')
            return resolved, path.lstat()

        def walk(path, relative, depth, project_root=None):
            nonlocal count, budget
            count += 1
            if count > maximum or depth > depth_limit:
                raise ValueError('scan limit')
            _, before = checked(path, project_root)
            if stat.S_ISDIR(before.st_mode):
                if os.path.ismount(path):
                    raise ValueError('mount')
                rows.append([_logical_path(root), relative, 'directory'])
                with os.scandir(path) as entries:
                    children = [path / entry.name for entry in islice(entries, maximum - count + 1)]
                if count + len(children) > maximum:
                    raise ValueError('scan limit')
                for child in sorted(children, key=lambda p: p.name):
                    walk(child, relative + '/' + child.name, depth + 1, project_root)
            elif stat.S_ISREG(before.st_mode) and before.st_nlink == 1:
                budget -= before.st_size
                if budget < 0:
                    raise ValueError('byte limit')
                digest = hashlib.sha256()
                read_size = 0
                with path.open(mode='rb') as stream:
                    opened = os.fstat(stream.fileno())
                    if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                        raise ValueError('changed identity')
                    for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                        read_size += len(chunk)
                        if read_size > before.st_size:
                            raise ValueError('growing input')
                        digest.update(chunk)
                after = path.stat()
                if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
                        after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
                    raise ValueError('changed input')
                rows.append([_logical_path(root), relative, digest.hexdigest()])
            else:
                raise ValueError('special file or hard link')

        exposed = {}
        for raw, project in paths:
            path = Path(os.path.expanduser(str(raw))).absolute()
            project_root = path.resolve(strict=True) if project else None
            checked(path, project_root)  # do not hide a link by replacing it with its parent
            if project and not path.is_dir():
                raise ValueError('project input is not a directory')
            # Runner._reference_dirs exposes the parent of a file reference, including siblings.
            exposed[path.parent if path.is_file() else path] = project_root
        for root in sorted(exposed, key=_logical_path):
            walk(root, '.', 0, exposed[root])
        descriptor = {k: request.get(k) or [] for k in ('references', 'reference_dirs', 'project_dirs')}
        from ..wsl_paths import message_paths
        descriptor = message_paths(descriptor, 'windows')
        body = {'version': 1, 'descriptor': descriptor, 'files': rows}
        if unreceipted:  # absent for path-only requests, so their recorded proofs keep the same hash
            body['unreceipted'] = sorted(set(unreceipted))
        body['sha256'] = hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        return body
    except (OSError, ValueError, TypeError, RuntimeError, AttributeError):
        return None
