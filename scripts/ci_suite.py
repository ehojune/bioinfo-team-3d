"""Explicit CI pytest plugin: deterministic coverage, isolation and measurements.

Loaded only with -p scripts.ci_suite; no global addopts or child-pytest parallelism.
"""
import argparse
from collections import defaultdict
from datetime import datetime
import faulthandler
import gc
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import re
import sys
import threading
import time

import pytest

from scripts.ci_selection import ROOT, PROFILES, WINDOWS_BOUNDARY, WINDOWS_PREVIEW, host_resource, include_windows, policy, read_changes
from scripts import ci_windows_phases as windows


def pytest_addoption(parser):
    group = parser.getgroup("ci-coverage")
    group.addoption("--ci-profile", choices=PROFILES)
    group.addoption("--ci-stall", action="store_true", default=False)
    group.addoption("--ci-phase", choices=('critical', 'parallel'))


def pytest_configure(config):
    if config.getoption("ci_profile"):
        config._labhq_ci = Observation(config)
        config.pluginmanager.register(config._labhq_ci, "labhq-ci-observation")


def safe_worker(value):
    if not re.fullmatch(r"master|gw[0-9]+", value):
        raise ValueError("invalid CI worker identity")
    return value


def select(items, profile, root=ROOT, *, changed=None):
    if profile in {"linux-full-v1", "windows-full-v1"}:
        return list(items), [], {}
    if profile not in {WINDOWS_BOUNDARY, WINDOWS_PREVIEW}:
        raise pytest.UsageError("unknown CI selection profile")
    try:
        data = policy(root)
        if changed is None:
            changed = read_changes(os.environ["CI_SUITE_ARTIFACT_DIR"], os.environ.get("GITHUB_SHA"),
                                   preview=profile == WINDOWS_PREVIEW)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise pytest.UsageError("invalid Windows selection evidence; refusing full fallback") from exc
    chosen, omitted, reasons = [], [], {}
    cache, sources = {}, {}
    for item in items:
        original = Path(item.path)
        path = original.resolve()
        try:
            relative = path.relative_to(root.resolve()).as_posix()
        except ValueError:
            raise pytest.UsageError("Windows selection source is outside checkout") from None
        symbol = item.nodeid.split('::', 1)[1] if '::' in item.nodeid else None
        key = (relative, symbol.split('[', 1)[0] if symbol else None)
        if key not in cache:
            try:
                if original.is_symlink():
                    raise ValueError("unreviewed symlink")
                if relative not in sources:
                    sources[relative] = path.read_text(encoding="utf-8")
                cache[key] = include_windows(relative, sources[relative], data,
                                            root=root, changed=changed, node=symbol)
            except (OSError, UnicodeError, ValueError, KeyError, SyntaxError) as exc:
                raise pytest.UsageError("invalid Windows selection source: " + relative) from exc
        included, reason = cache[key]
        if included:
            chosen.append(item)
        else:
            omitted.append(item)
            reasons[relative] = reason
    return chosen, omitted, reasons


class PhaseCheckpoints:
    """Bounded hook observations, deliberately separate from coverage/PASS receipts.

    Each process has its own monotonic origin after identity validation. Missing
    hooks remain absent; these times cannot measure pre-plugin imports or be
    summed across workers. Pool snapshots refresh at most once per five seconds
    after the first stage observations; serial critical stages flush immediately.
    """
    NAMES = {'configured', 'session_started', 'collection_started', 'selection_finished',
             'collection_finished', 'first_test_started', 'session_finished'}
    STAGES = ('setup', 'call', 'teardown')

    def __init__(self, path, context, phase, worker, *, clock=None):
        self.path, self.clock = path, clock or time.monotonic
        self.origin = self.clock()
        self.last_write = self.origin
        self.interval = 0 if phase == 'critical' else 5
        self.seen_stages = set()
        self.warned = False
        self.data = dict(diagnostic_only=True, reusable=False, phase=phase, worker=worker,
            context=context, clock='process-local monotonic elapsed seconds',
            checkpoints_s={}, current=None, completed_reports=dict.fromkeys(self.STAGES, 0),
            refresh_interval_s=self.interval)
        self.mark('configured')

    def mark(self, name):
        if name not in self.NAMES:
            raise ValueError('unknown diagnostic checkpoint')
        if name not in self.data['checkpoints_s']:
            self.data['checkpoints_s'][name] = self.clock() - self.origin
            self.flush()

    def stage(self, nodeid, stage, state):
        if stage not in self.STAGES or state not in ('started', 'completed'):
            raise ValueError('unknown diagnostic stage')
        now = self.clock()
        self.data['current'] = dict(nodeid=nodeid[:512], nodeid_truncated=len(nodeid) > 512,
                                   stage=stage, state=state, elapsed_s=now - self.origin)
        if state == 'completed':
            self.data['completed_reports'][stage] += 1
        first = state == 'started' and stage not in self.seen_stages
        if first:
            self.seen_stages.add(stage)
        if first or now - self.last_write >= self.interval:
            self.flush()

    def flush(self):
        try:
            self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
            self.last_write = self.clock()
        except OSError:
            # A diagnostic write must not mask the original test/receipt failure.
            if not self.warned:
                print('CI phase checkpoint write unavailable (diagnostic only)', file=sys.stderr)
                self.warned = True


def collection_arguments(config, phase):
    """Bind real pytest discovery, not a caller's claim of complete coverage."""
    expected = list(windows.TARGETS) if phase == 'critical' else ['tests']
    windows.require(Path(config.rootpath).resolve() == ROOT.resolve(), 'unexpected phase collection root')
    windows.require(list(config.args) == expected, 'unexpected phase collection arguments')
    for flag in ('keyword', 'markexpr', 'deselect', 'ignore', 'ignore_glob', 'lf',
                 'collectonly', 'pyargs', 'keepduplicates', 'override_ini', 'stepwise', 'stepwise_skip'):
        windows.require(not getattr(config.option, flag, None), 'phase collection filter: ' + flag)
    windows.require(not os.environ.get('PYTEST_ADDOPTS'), 'phase collection filter: PYTEST_ADDOPTS')
    for key, value in dict(python_files=['test_*.py', '*_test.py'],
                           python_classes=['Test'], python_functions=['test']).items():
        windows.require(config.getini(key) == value, 'phase collection filter: ' + key)
    return expected


class Observation:
    def __init__(self, config):
        self.config = config
        self.profile = config.getoption("ci_profile")
        self.worker = safe_worker(os.environ.get("PYTEST_XDIST_WORKER", "master"))
        self.directory = Path(os.environ["CI_SUITE_ARTIFACT_DIR"])
        self.phase = config.getoption('ci_phase')
        self.context_identity = None
        if self.phase:
            self.collection_args = collection_arguments(config, self.phase)
            self.context_identity = windows.validate_identity(windows.read(self.directory / 'identity.json'))
            expected = self.context_identity
            windows.require(expected['profile'] == self.profile
                and expected['checkout_sha'] == os.environ.get('GITHUB_SHA')
                and expected['workflow_sha'] == os.environ.get('GITHUB_WORKFLOW_SHA')
                and expected['workflow_ref'] == os.environ.get('GITHUB_WORKFLOW_REF')
                and expected['repository'] == os.environ.get('GITHUB_REPOSITORY')
                and expected['run_id'] == int(os.environ['GITHUB_RUN_ID'])
                and expected['attempt'] == int(os.environ['GITHUB_RUN_ATTEMPT'])
                and expected['python_version'] == platform.python_version()
                and expected['python_executable'] == os.path.normcase(os.path.abspath(sys.executable)),
                'stale phase environment/interpreter')
            if self.phase == 'parallel':
                windows.check_ready(self.directory, expected)
            else:
                windows.require(self.worker == 'master' and os.environ.get('LABHQ_REQUIRE_RESIZABLE_BROWSER') == '1',
                                'critical must be serial with required browser')
            self.directory = self.directory / self.phase
        self.directory.mkdir(parents=True, exist_ok=True)
        self.started = time.time()
        self.worker_count = int(os.environ.get("PYTEST_XDIST_WORKER_COUNT", "0"))
        self.files = defaultdict(float)
        self.results = defaultdict(set)
        self.stage_reports = []
        self.context = "collection"
        self.stall = None
        self.serial = {}
        self.shared_files = {}
        self.resource_waits = defaultdict(float)
        self.checkpoints = (PhaseCheckpoints(self.directory / f'checkpoints-{self.worker}.json',
                            self.context_identity, self.phase, self.worker) if self.phase else None)

    @pytest.hookimpl(wrapper=True, tryfirst=True)
    def pytest_collection(self, session):
        if self.checkpoints:
            self.checkpoints.mark('collection_started')
        try:
            return (yield)
        finally:
            if self.checkpoints:
                self.checkpoints.mark('collection_finished')

    @pytest.hookimpl(optionalhook=True)
    def pytest_xdist_setupnodes(self, config, specs):
        if self.phase and len(specs) != (0 if self.phase == 'critical' else 4):
            raise pytest.UsageError('critical must be serial; parallel must have exactly four workers')
        self.worker_count = len(specs)  # actual xdist count, never assume auto == 4

    def pytest_collection_modifyitems(self, session, config, items):
        full = [i.nodeid for i in items]
        if self.phase == 'critical':
            try:
                # Collect the two complete files and exact browser parameter.
                # No global inventory or eligibility is invented for this run.
                for item in items:
                    source = Path(item.path)
                    windows.require(not source.is_symlink() and source.resolve().relative_to(ROOT.resolve()).as_posix()
                                    == item.nodeid.split('::', 1)[0], 'foreign critical source')
                windows.require(windows.critical(full, full) == full, 'foreign critical collection')
            except ValueError as exc:
                self.write('selection-error', {'reusable': False, 'status': 'invalid-critical-collection'})
                raise pytest.UsageError(str(exc)) from exc
            self.write('collection', dict(context=self.context_identity, phase=self.phase,
                profile=self.profile, collection_scope='critical-targets-v2',
                collection_args=self.collection_args, collected_nodes=full, selected_nodes=full))
            self.checkpoints.mark('selection_finished')
            return
        try:
            chosen, omitted, reasons = select(items, self.profile)
            if self.profile == WINDOWS_BOUNDARY:
                data = policy()
                from fnmatch import fnmatchcase
                paths = {Path(i.path).resolve().relative_to(ROOT).as_posix() for i in items}
                if any(not any(fnmatchcase(p, pattern) for p in paths)
                       for pattern in data['boundary_cohorts']):
                    raise pytest.UsageError("missing essential Windows cohort in collection")
                selected = {i.nodeid.split('[', 1)[0] for i in chosen}
                if not set(data['mandatory_nodes']) <= selected:
                    raise pytest.UsageError("missing mandatory native Windows regression")
                if not chosen:
                    raise pytest.UsageError("empty essential Windows selection")
        except pytest.UsageError:
            self.write("selection-error", {"profile": self.profile, "status": "invalid-selection", "reusable": False})
            raise
        if omitted:
            config.hook.pytest_deselected(items=omitted)
            items[:] = chosen
        eligible = [i.nodeid for i in chosen]
        if self.phase:
            try:
                critical = set(windows.critical(full, eligible))
                phase_items = [i for i in chosen if (i.nodeid in critical) == (self.phase == 'critical')]
                removed = [i for i in chosen if (i.nodeid in critical) != (self.phase == 'critical')]
                config.hook.pytest_deselected(items=removed)
                items[:] = chosen = phase_items
            except ValueError as exc:
                self.write('selection-error', {'reusable': False, 'status': 'invalid-phase'})
                raise pytest.UsageError(str(exc)) from exc
        self.write("collection", {"profile": self.profile,
                                  **({'context': self.context_identity, 'phase': self.phase,
                                      'eligible_nodes': eligible, 'collection_scope': 'global-v2',
                                      'collection_args': self.collection_args} if self.phase else {}),
                                  **({"reusable": False} if self.profile == WINDOWS_PREVIEW else {}), "full_nodes": full,
                                  "selected_nodes": [i.nodeid for i in chosen],
                                  "excluded_files": reasons})
        if self.checkpoints:
            self.checkpoints.mark('selection_finished')

    def pytest_sessionstart(self):
        if self.checkpoints:
            self.checkpoints.mark('session_started')
        # Keep the existing controller diagnostics in the workflow. Each 3.10
        # worker also needs its own real thread/task stacks, outside fd capture.
        if self.config.getoption("ci_stall") and hasattr(self.config, "workerinput"):
            self.stall = WorkerStall(self.directory / f"stacks-{self.worker}.log", lambda: self.context)
            self.stall.start()

    @pytest.hookimpl(wrapper=True)
    def pytest_runtest_protocol(self, item, nextitem):
        path = Path(item.path)
        symbol = item.nodeid.split('::')[1:] if '::' in item.nodeid else None
        if symbol:
            symbol[-1] = symbol[-1].split('[', 1)[0]
        key = (path, tuple(symbol or ()))
        if key not in self.serial:
            # Whole-file analysis is a conservative superset. A clear file
            # avoids repeating helper traversal for each ordinary pure node.
            if path not in self.shared_files:
                self.shared_files[path] = host_resource(path)
            self.serial[key] = self.shared_files[path] and host_resource(path, symbols=symbol)
        if self.serial[key] and self.worker_count:
            from filelock import FileLock
            # Shared only by workers of this CI job. Covers fixture setup and
            # teardown as well as the free-port -> bind window and owned cleanup.
            started = time.perf_counter()
            with FileLock(str(self.directory / "host-resources.lock"), timeout=1500):
                self.resource_waits[path.relative_to(ROOT).as_posix()] += time.perf_counter() - started
                return (yield)
        return (yield)

    def pytest_runtest_logstart(self, nodeid, location):
        self.context = f"{nodeid} [setup]"
        if self.checkpoints:
            self.checkpoints.mark('first_test_started')

    @pytest.hookimpl(tryfirst=True)
    def pytest_runtest_setup(self, item):
        if self.checkpoints:
            self.checkpoints.stage(item.nodeid, 'setup', 'started')

    @pytest.hookimpl(tryfirst=True)
    def pytest_runtest_call(self, item):
        if self.checkpoints:
            self.checkpoints.stage(item.nodeid, 'call', 'started')

    @pytest.hookimpl(tryfirst=True)
    def pytest_runtest_teardown(self, item):
        if self.checkpoints:
            self.checkpoints.stage(item.nodeid, 'teardown', 'started')

    def pytest_runtest_logreport(self, report):
        if self.checkpoints:
            self.checkpoints.stage(report.nodeid, report.when, 'completed')
        self.stage_reports.append([report.nodeid, report.when, report.outcome])
        self.context = f"{report.nodeid} [after {report.when}]"
        self.files[report.nodeid.split("::", 1)[0]] += report.duration
        if report.failed:
            self.results["failed"].add(report.nodeid)
        elif report.skipped:
            self.results["skipped"].add(report.nodeid)
        elif report.when == "call" and report.passed:
            self.results["passed"].add(report.nodeid)

    def pytest_collectreport(self, report):
        if report.failed or report.skipped:
            self.results["failed" if report.failed else "skipped"].add(report.nodeid + " [collection]")

    def pytest_sessionfinish(self, session, exitstatus):
        if self.stall:
            self.stall.close()
        self.write("metrics", {"profile": self.profile, "worker": self.worker,
                  **({'context': self.context_identity, 'phase': self.phase,
                      'started_at': self.started, 'finished_at': time.time(),
                      'stage_reports': self.stage_reports} if self.phase else {}),
                  **({"reusable": False} if self.profile == WINDOWS_PREVIEW else {}),
                  "workers": self.worker_count, "cpu_logical": os.cpu_count(),
                  "python_version": platform.python_version(),
                  "platform": platform.platform(), "runner_image": os.environ.get("ImageVersion"),
                  "pytest_xdist": importlib.metadata.version("pytest-xdist") if self.worker_count else None,
                  "setup_s": max(0, self.started - float(os.environ.get("CI_SETUP_STARTED", self.started))),
                  "pytest_s": time.time() - self.started, "exitstatus": int(exitstatus),
                  "results": {k: len(v) for k, v in self.results.items()},
                  "host_resource_wait_s": dict(sorted(self.resource_waits.items())),
                  "longest_files": sorted(self.files.items(), key=lambda p: (-p[1], p[0]))[:20]})
        if self.checkpoints:
            self.checkpoints.mark('session_finished')

    def write(self, name, value):
        (self.directory / f"{name}-{self.worker}.json").write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class WorkerStall:
    def __init__(self, path, context):
        self.stream = path.open("w", encoding="utf-8", buffering=1)
        self.context = context
        self.stop = threading.Event()
        self.worker = threading.Thread(target=self.watch, name="ci-stall-diagnostics", daemon=True)

    def start(self):
        self.worker.start()

    def close(self):
        self.stop.set()
        self.worker.join(timeout=1)
        # Leave the stream open if a diagnostic is still using it.
        if not self.worker.is_alive():
            self.stream.close()

    def watch(self):
        import asyncio
        while not self.stop.wait(300):
            try:
                print(f"\nCI snapshot: {self.context()}", file=self.stream, flush=True)
                faulthandler.dump_traceback(file=self.stream, all_threads=True)
                shown = 0
                for value in gc.get_objects():
                    if isinstance(value, asyncio.Task) and not value.done():
                        if shown == 50:
                            print("Further pending tasks omitted", file=self.stream)
                            break
                        value.print_stack(limit=20, file=self.stream)
                        shown += 1
                print(f"Pending task stacks shown: {shown}", file=self.stream, flush=True)
            except Exception as error:
                print(f"CI snapshot failed: {type(error).__name__}", file=self.stream, flush=True)


def summarize(directory):
    if (directory / 'identity.json').exists():
        proof = windows.combine(directory)
        windows.write(directory / 'windows-proof.json', proof)
        result = dict(proof, profile=proof['context']['profile'], workers=4,
                      results={name: sum(p[name] for p in proof['phases'].values())
                               for name in ('passed', 'skipped', 'failed')})
        result.update(windows.measurements(directory))
        return result
    metrics = json.loads((directory / "metrics-master.json").read_text(encoding="utf-8"))
    collections = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(directory.glob("collection-*.json"))]
    if not collections:
        raise ValueError("missing node manifests")
    if any(c != collections[0] for c in collections):
        raise ValueError("inconsistent worker coverage manifests")
    coverage = collections[0]
    metrics.update(full_node_count=len(coverage["full_nodes"]), selected_node_count=len(coverage["selected_nodes"]),
                   excluded_files=coverage["excluded_files"])
    waits = defaultdict(float)
    for path in sorted(directory.glob("metrics-gw*.json")):
        worker = json.loads(path.read_text(encoding="utf-8"))
        if worker["profile"] != metrics["profile"]:
            raise ValueError("inconsistent worker execution profile")
        for name, duration in worker.get("host_resource_wait_s", {}).items():
            waits[name] += duration
    metrics["host_resource_wait_s"] = dict(sorted(waits.items(), key=lambda p: (-p[1], p[0])))
    return metrics


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["report", "critical-ready"])
    args = parser.parse_args()
    directory = Path(os.environ["CI_SUITE_ARTIFACT_DIR"])
    if args.command == 'critical-ready':
        windows.ready(directory)
        return
    try:
        result = summarize(directory)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "report-error.json").write_text(json.dumps({
            "status": "incomplete", "reusable": False, "error": type(exc).__name__}), encoding="utf-8")
        raise
    preview = result["profile"] == WINDOWS_PREVIEW
    if preview:
        result.update(reusable=False, timing_scope="warm preview after mandatory full suite; not cold PR timing")
    result["over_ten_minutes"] = result["setup_s"] + result["pytest_s"] > 600
    result["duration_scope"] = "setup + pytest; final report/upload tail requires completed GitHub job duration"
    # Save measured local receipts before the optional timing metadata lookup.
    # Even an API/report failure must leave the upload step something to retain.
    summary = directory / ("preview-summary.json" if preview else "summary.json")
    summary.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    if 'execution' in result:
        with Path(os.environ['GITHUB_OUTPUT']).open('a', encoding='utf-8') as stream:
            stream.write('execution=' + windows.EXECUTION + '\nreceipt=' + windows.digest(windows.read(directory / 'windows-proof.json')) + '\n')
    # Read current run scheduling metadata once; this does not wait on any CI.
    from scripts.ci_skip import api
    jobs = api(f"actions/runs/{os.environ['GITHUB_RUN_ID']}/attempts/{os.environ['GITHUB_RUN_ATTEMPT']}/jobs?per_page=100")
    rows = [j for j in jobs["jobs"] if j["name"] == os.environ["CI_JOB_LABEL"]]
    if jobs["total_count"] != len(jobs["jobs"]) or len(rows) != 1:
        raise ValueError("ambiguous job timing evidence")
    stamp = lambda value: datetime.fromisoformat(value.replace("Z", "+00:00"))
    result["job_queue_s"] = (stamp(rows[0]["started_at"]) - stamp(rows[0]["created_at"])).total_seconds()
    result["queue_scope"] = "job created to runner start, separate from setup and pytest"
    result["over_five_minutes"] = result["setup_s"] + result["pytest_s"] > 300
    summary.write_text(
        json.dumps(result, indent=2) + "\n", encoding="utf-8")
    text = json.dumps(result, indent=2)
    print(text)
    with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a", encoding="utf-8") as stream:
        heading = "Boundary preview (diagnostic; reusable=false)" if preview else "CI coverage and timing (measured)"
        stream.write("### " + heading + "\n\n```json\n" + text + "\n```\n")


if __name__ == "__main__":
    main()
