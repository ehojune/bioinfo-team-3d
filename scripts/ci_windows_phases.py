"""Pure Windows phase receipt validation; never launch or download anything."""
import hashlib
import json
import math
from pathlib import Path
import re

EXECUTION = 'windows-critical-serial-v1'
PROFILE = 'windows-boundary-v6'
FILES = ('tests/test_windows_job_ci.py', 'tests/test_web_shutdown_process.py')
BROWSER = 'tests/test_web_3d.py::test_office_in_node[web_resizable_panels.cjs-fail 0]'
MANDATORY = (
    FILES[0] + '::test_wmi_escapes_owned_outer_nonbreakaway_inner_silent_job',
    FILES[0] + '::test_real_wmi_bootstrap_never_enters_fake_role_without_verified_ack',
    FILES[1] + '::test_web_shutdown_ends_a_real_gateway_through_its_own_shutdown',
    FILES[1] + '::test_forced_web_shutdown_with_running_work_also_ends_gracefully_and_keeps_the_record',
    FILES[1] + '::test_the_watcher_stops_a_gateway_that_never_finishes_its_shutdown',
)


def require(value, message):
    if not value:
        raise ValueError(message)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode('utf-8')).hexdigest()


def read(path):
    path = Path(path)
    require(not path.is_symlink() and path.stat().st_size <= 64 * 1024 * 1024, 'invalid receipt file')
    def unique(pairs):
        result = dict(pairs)
        require(len(result) == len(pairs), 'duplicate receipt key')
        return result
    return json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=unique,
                      parse_constant=lambda value: require(False, 'nonfinite JSON'))


def write(path, value):
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def nodes(value):
    require(isinstance(value, list) and all(isinstance(n, str) and n.startswith('tests/')
            and '::' in n and '\x00' not in n for n in value)
            and len(value) == len(set(value)), 'duplicate or malformed node inventory')
    return value


def critical(full, eligible):
    nodes(full)
    nodes(eligible)
    require(set(eligible) <= set(full), 'selection outside full inventory')
    native = [n for n in full if n.split('::', 1)[0] in FILES]
    # A changed native collection requires explicit contract review, never loss.
    require(len(native) == 25 and all(any(n.startswith(p + '::') for n in native) for p in FILES),
            'expected all 25 native cases')
    result = [n for n in full if n in native or n == BROWSER]
    require(len(result) == 26 and set(result) <= set(eligible), 'missing critical selection')
    require(set(MANDATORY) <= {n.split('[', 1)[0] for n in result}, 'missing mandatory callable')
    return result


def validate_identity(value):
    require(set(value) == {'execution', 'repository', 'run_id', 'attempt', 'checkout_sha', 'head_sha',
        'tree', 'policy', 'workflow_sha', 'workflow_ref', 'profile', 'python_version',
        'python_executable', 'invocation', 'setup_started'}, 'identity schema')
    require(value['execution'] == EXECUTION and value['repository'] == 'ehojune/bioinfo-team-3d'
            and value['profile'] in {PROFILE, 'windows-full-v1'}, 'identity execution policy')
    require(all(type(value[k]) is int and value[k] > 0 for k in ('run_id', 'attempt')), 'identity run')
    require(all(isinstance(value[k], str) and re.fullmatch('[0-9a-f]{40}', value[k])
                for k in ('checkout_sha', 'head_sha', 'tree', 'workflow_sha')), 'identity commit')
    require(value['workflow_sha'] == value['checkout_sha'] and isinstance(value['workflow_ref'], str)
            and value['workflow_ref'].startswith(value['repository'] + '/.github/workflows/test.yml@refs/'),
            'identity workflow')
    require(isinstance(value['policy'], dict) and value['policy'] and all(isinstance(k, str)
        and isinstance(v, str) and re.fullmatch('[0-9a-f]{40}', v) for k, v in value['policy'].items()),
        'identity policy')
    require(isinstance(value['python_version'], str) and re.fullmatch(r'3\.12\.\d+', value['python_version'])
            and isinstance(value['python_executable'], str) and value['python_executable'], 'identity interpreter')
    require(isinstance(value['invocation'], str) and re.fullmatch('[0-9a-f]{32}', value['invocation']),
            'identity invocation')
    number(value['setup_started'])
    return value


def number(value):
    require(type(value) in (int, float) and math.isfinite(value) and value >= 0, 'invalid phase timing')
    return value


def phase(directory, name, identity):
    folder = Path(directory) / name
    require(not list(folder.glob('*error*.json')), 'phase reported error')
    master = read(folder / 'metrics-master.json')
    count = 0 if name == 'critical' else 4
    workers = ['master'] if count == 0 else ['gw' + str(i) for i in range(count)]
    require({p.name for p in folder.glob('collection-*.json')} ==
            {'collection-' + w + '.json' for w in workers}, 'missing/unexpected phase collection')
    require({p.name for p in folder.glob('metrics-*.json')} ==
            {'metrics-' + w + '.json' for w in set(workers) | {'master'}}, 'missing/unexpected worker result')
    collections = [read(folder / ('collection-' + w + '.json')) for w in workers]
    coverage = collections[0]
    require(all(c == coverage for c in collections), 'worker collection drift')
    require(coverage['context'] == identity and coverage['phase'] == name
            and coverage['profile'] == identity['profile'], 'stale collection identity')
    full, eligible, selected = [nodes(coverage[k]) for k in ('full_nodes', 'eligible_nodes', 'selected_nodes')]
    initial = critical(full, eligible)
    expected = initial if name == 'critical' else [n for n in eligible if n not in set(initial)]
    require(selected == expected, 'phase selection mismatch')
    require(selected, 'empty phase')
    for w in set(workers) | {'master'}:
        m = read(folder / ('metrics-' + w + '.json'))
        require(m['context'] == identity and m['phase'] == name and m['worker'] == w
                and m['profile'] == identity['profile'] and type(m['workers']) is int and m['workers'] == count
                and type(m['exitstatus']) is int and m['exitstatus'] == 0, 'incomplete/stale worker receipt')
        require(m['started_at'] >= identity['setup_started'] and
                number(m['finished_at']) >= number(m['started_at']), 'phase time order')
        if name == 'parallel':
            require(m['started_at'] >= read(Path(directory) / 'critical-success.json')['finished_at'],
                    'worker overlapped critical phase')
            require(master['started_at'] <= m['started_at'] <= m['finished_at'] <= master['finished_at'],
                    'worker outside controller execution interval')
    records = master['stage_reports']
    validate_reports(records, selected, strict=name == 'critical')
    if count:
        combined = []
        for w in workers:
            combined.extend(read(folder / ('metrics-' + w + '.json'))['stage_reports'])
        require(sorted(combined) == sorted(records), 'lost/duplicate worker execution')
        validate_reports(combined, selected, strict=False)
    return coverage, master


def validate_reports(records, selected, *, strict):
    require(isinstance(records, list), 'missing execution reports')
    selected = set(selected)
    grouped = {}
    for r in records:
        require(isinstance(r, list) and len(r) == 3 and r[0] in selected
                and r[1] in ('setup', 'call', 'teardown') and r[2] in ('passed', 'skipped'),
                'failed/foreign execution report')
        stages = grouped.setdefault(r[0], {})
        require(r[1] not in stages, 'duplicate node execution')
        stages[r[1]] = r[2]
    require(set(grouped) == set(selected), 'lost selected execution')
    for stages in grouped.values():
        expected = {'setup', 'call', 'teardown'} if stages.get('setup') == 'passed' else {'setup', 'teardown'}
        require(set(stages) == expected and stages['teardown'] == 'passed', 'incomplete node lifecycle')
        if strict:
            require(set(stages) == {'setup', 'call', 'teardown'} and set(stages.values()) == {'passed'},
                    'critical skip/incomplete lifecycle')


def ready(directory):
    identity = validate_identity(read(Path(directory) / 'identity.json'))
    coverage, master = phase(directory, 'critical', identity)
    result = {'context': identity, 'critical_digest': digest([coverage, master]),
              'finished_at': master['finished_at']}
    write(Path(directory) / 'critical-success.json', result)
    return result


def check_ready(directory, identity):
    coverage, master = phase(directory, 'critical', identity)
    result = read(Path(directory) / 'critical-success.json')
    require(result == {'context': identity, 'critical_digest': digest([coverage, master]),
                       'finished_at': master['finished_at']}, 'stale/missing critical barrier')
    return result


def combine(directory):
    identity = validate_identity(read(Path(directory) / 'identity.json'))
    check_ready(directory, identity)
    a, am = phase(directory, 'critical', identity)
    b, bm = phase(directory, 'parallel', identity)
    require(a['full_nodes'] == b['full_nodes'] and a['eligible_nodes'] == b['eligible_nodes']
            and a['excluded_files'] == b['excluded_files'], 'phase full/eligible collection drift')
    require(not set(a['selected_nodes']) & set(b['selected_nodes']) and
            set(a['selected_nodes']) | set(b['selected_nodes']) == set(a['eligible_nodes']), 'phase union/overlap')
    require(bm['started_at'] >= am['finished_at'], 'overlapping execution phases')
    phases = {}
    for name, c, m in (('critical', a, am), ('parallel', b, bm)):
        passed = {r[0] for r in m['stage_reports'] if r[1:] == ['call', 'passed']}
        phases[name] = dict(selected=len(c['selected_nodes']), passed=len(passed),
            skipped=len(c['selected_nodes']) - len(passed), failed=0, workers=m['workers'],
            started_at=m['started_at'], finished_at=m['finished_at'],
            pytest_s=m['finished_at'] - m['started_at'])
    result = dict(execution=EXECUTION, context=identity, reusable=True, phases=phases,
        full_node_count=len(a['full_nodes']), selected_node_count=len(a['eligible_nodes']),
        full_digest=digest(a['full_nodes']), selected_digest=digest(a['eligible_nodes']),
        critical_digest=digest(a['selected_nodes']), parallel_digest=digest(b['selected_nodes']),
        setup_s=am['started_at'] - identity['setup_started'],
        pytest_s=bm['finished_at'] - am['started_at'])
    validate_proof(result)
    return result


def measurements(directory):
    """Sum actual lock wait only once per executing worker, never controller duplicates."""
    waits, longest = {}, {}
    for name in ('critical', 'parallel'):
        rows = [read(p) for p in (Path(directory)/name).glob('metrics-*.json')
                if name == 'critical' or p.name != 'metrics-master.json']
        for row in rows:
            for path, duration in row['host_resource_wait_s'].items():
                waits[path] = waits.get(path, 0) + number(duration)
            for path, duration in row['longest_files']:
                longest[path] = longest.get(path, 0) + number(duration)
    return dict(excluded_files=read(Path(directory)/'critical/collection-master.json')['excluded_files'],
                host_resource_wait_s=waits, longest_files=sorted(longest.items(), key=lambda p: -p[1])[:20])


def validate_proof(value):
    require(set(value) == {'execution', 'context', 'reusable', 'phases', 'full_node_count',
        'selected_node_count', 'full_digest', 'selected_digest', 'critical_digest', 'parallel_digest',
        'setup_s', 'pytest_s'}, 'execution proof schema')
    validate_identity(value['context'])
    require(value['execution'] == EXECUTION and value['reusable'] is True, 'nonreusable execution')
    require(set(value['phases']) == {'critical', 'parallel'}, 'missing phase proof')
    a, b = value['phases']['critical'], value['phases']['parallel']
    for p in (a, b):
        require(set(p) == {'selected', 'passed', 'skipped', 'failed', 'workers', 'started_at',
                          'finished_at', 'pytest_s'}, 'phase proof schema')
        require(all(type(p[k]) is int and p[k] >= 0 for k in ('selected', 'passed', 'skipped', 'failed', 'workers'))
                and p['selected'] > 0 and p['failed'] == 0
                and p['passed'] + p['skipped'] == p['selected'], 'phase outcomes')
        require(number(p['finished_at']) - number(p['started_at']) == number(p['pytest_s']), 'phase duration')
    require(a['selected'] == a['passed'] == 26 and a['skipped'] == a['workers'] == 0
            and b['workers'] == 4 and b['started_at'] >= a['finished_at'], 'critical proof / barrier')
    require(type(value['full_node_count']) is int and type(value['selected_node_count']) is int
            and value['full_node_count'] >= value['selected_node_count'] == a['selected'] + b['selected'],
            'proof coverage counts')
    for key in ('full_digest', 'selected_digest', 'critical_digest', 'parallel_digest'):
        require(isinstance(value[key], str) and re.fullmatch('[0-9a-f]{64}', value[key]), 'proof inventory digest')
    require(number(value['setup_s']) == a['started_at'] - value['context']['setup_started']
            and number(value['pytest_s']) == b['finished_at'] - a['started_at'], 'proof elapsed duration')
    return value
