"""Offline, declarative fixture-plan review; never provisions a fixture."""
import argparse
import datetime as dt
import json
import re
from pathlib import Path


def validate(plan, now=None):
    errors = []

    def obj(value, path, keys):
        if not isinstance(value, dict):
            errors.append(f"{path}: expected object")
            return {}
        for key in sorted(keys - value.keys()):
            errors.append(f"{path}.{key}: missing")
        for key in sorted(value.keys() - keys):
            errors.append(f"{path}.{key}: unknown; review required")
        return value

    def string(value, path):
        if not isinstance(value, str) or not value.strip():
            errors.append(f"{path}: expected nonempty string")
            return False
        return True

    def exact(value, path, expected):
        if value != expected or type(value) is not type(expected):
            errors.append(f"{path}: expected {expected!r}")

    def items(value, path, required):
        if not isinstance(value, list) or any(not isinstance(x, str) for x in value):
            errors.append(f"{path}: expected string array")
            return
        for x in sorted(required - set(value)):
            errors.append(f"{path}: missing {x}")
        for x in sorted(set(value) - required):
            errors.append(f"{path}: unknown {x}; review required")
        if len(value) != len(set(value)):
            errors.append(f"{path}: duplicate item")

    p = obj(plan, '$', {'version', 'fixture_id', 'image_digest', 'owner', 'expires_at',
                         'principals', 'isolation', 'teardown', 'observations'})
    exact(p.get('version'), '$.version', 1)
    for key in ('fixture_id', 'owner'):
        string(p.get(key), f'$.{key}')
    digest = p.get('image_digest')
    if not isinstance(digest, str) or not re.fullmatch(r'sha256:[0-9a-f]{64}', digest):
        errors.append('$.image_digest: expected lowercase sha256 digest')
    expiry = p.get('expires_at')
    try:
        if not isinstance(expiry, str) or not re.fullmatch(
                r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z', expiry):
            raise ValueError
        expires = dt.datetime.fromisoformat(expiry[:-1] + '+00:00')
        if expires <= (now or dt.datetime.now(dt.timezone.utc)):
            errors.append('$.expires_at: expired')
    except ValueError:
        errors.append('$.expires_at: expected UTC ISO 8601 timestamp ending in Z')
    principals = obj(p.get('principals'), '$.principals', {'supervisor', 'engine', 'writer'})
    names = [principals.get(k) for k in ('supervisor', 'engine', 'writer')]
    for key, name in zip(('supervisor', 'engine', 'writer'), names):
        string(name, f'$.principals.{key}')
    if all(isinstance(x, str) and x.strip() for x in names) and len(set(names)) != 3:
        errors.append('$.principals: supervisor, engine and writer must be distinct')
    isolation = obj(p.get('isolation'), '$.isolation', {'storage', 'shared_paths', 'interop', 'network'})
    exact(isolation.get('storage'), '$.isolation.storage', 'supervisor_controlled_linux')
    for key in ('shared_paths', 'interop', 'network'):
        exact(isolation.get(key), f'$.isolation.{key}', False)
    items(p.get('teardown'), '$.teardown', {'vm', 'run_storage', 'credentials'})
    items(p.get('observations'), '$.observations', {'identity', 'writer_denial', 'parent_replacement',
          'links', 'bytes_during_run', 'network_denial', 'interop_denial', 'cleanup'})
    return {'plan_valid': not errors, 'protection': 'UNVERIFIED', 'issues': errors}


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'duplicate JSON key: {key}')
        result[key] = value
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('plan', help='one explicit JSON file')
    args = parser.parse_args()
    try:
        with Path(args.plan).open(encoding='utf-8') as f:
            plan = json.load(f, object_pairs_hook=unique_object)
        result = validate(plan)
    except (OSError, UnicodeError, ValueError) as exc:
        result = {'plan_valid': False, 'protection': 'UNVERIFIED', 'issues': [f'input: {exc}']}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['plan_valid'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
