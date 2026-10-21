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

from scripts.ci_selection import ROOT, PROFILES, WINDOWS_BOUNDARY, host_resource, include_windows, policy, read_changes


def pytest_addoption(parser):
    group = parser.getgroup("ci-coverage")
    group.addoption("--ci-profile", choices=PROFILES)
    group.addoption("--ci-stall", action="store_true", default=False)


def pytest_configure(config):
    if config.getoption("ci_profile"):
        config._labhq_ci = Observation(config)
        config.pluginmanager.register(config._labhq_ci, "labhq-ci-observation")


def safe_worker(value):
    if not re.fullmatch(r"master|gw[0-9]+", value):
        raise ValueError("invalid CI worker identity")
    return value


def select(items, profile, root=ROOT, *, changed=None):
    if profile != WINDOWS_BOUNDARY:
        return list(items), [], {}
    try:
        data = policy(root)
        if changed is None:
            changed = read_changes(os.environ["CI_SUITE_ARTIFACT_DIR"], os.environ.get("GITHUB_SHA"))
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        print("ci_suite: selection policy unavailable; include all nodes", file=sys.stderr)
        return list(items), [], {}
    chosen, omitted, reasons = [], [], {}
    cache, sources = {}, {}
    for item in items:
        original = Path(item.path)
        path = original.resolve()
        try:
            relative = path.relative_to(root.resolve()).as_posix()
        except ValueError:
            chosen.append(item)  # external/unclassified collection is never omitted
            continue
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
            except (OSError, UnicodeError, ValueError, KeyError, SyntaxError):
                cache[key] = (True, "unreadable source included")
        included, reason = cache[key]
        if included:
            chosen.append(item)
        else:
            omitted.append(item)
            reasons[relative] = reason
    return chosen, omitted, reasons


class Observation:
    def __init__(self, config):
        self.config = config
        self.profile = config.getoption("ci_profile")
        self.worker = safe_worker(os.environ.get("PYTEST_XDIST_WORKER", "master"))
        self.directory = Path(os.environ["CI_SUITE_ARTIFACT_DIR"])
        self.directory.mkdir(parents=True, exist_ok=True)
        self.started = time.time()
        self.worker_count = int(os.environ.get("PYTEST_XDIST_WORKER_COUNT", "0"))
        self.files = defaultdict(float)
        self.results = defaultdict(set)
        self.context = "collection"
        self.stall = None
        self.serial = {}
        self.shared_files = {}
        self.resource_waits = defaultdict(float)

    @pytest.hookimpl(optionalhook=True)
    def pytest_xdist_setupnodes(self, config, specs):
        self.worker_count = len(specs)  # actual xdist count, never assume auto == 4

    def pytest_collection_modifyitems(self, session, config, items):
        full = [i.nodeid for i in items]
        chosen, omitted, reasons = select(items, self.profile)
        if omitted:
            config.hook.pytest_deselected(items=omitted)
            items[:] = chosen
        self.write("collection", {"profile": self.profile, "full_nodes": full,
                                  "selected_nodes": [i.nodeid for i in chosen],
                                  "excluded_files": reasons})

    def pytest_sessionstart(self):
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

    def pytest_runtest_logreport(self, report):
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
                  "workers": self.worker_count, "cpu_logical": os.cpu_count(),
                  "python_version": platform.python_version(),
                  "platform": platform.platform(), "runner_image": os.environ.get("ImageVersion"),
                  "pytest_xdist": importlib.metadata.version("pytest-xdist") if self.worker_count else None,
                  "setup_s": max(0, self.started - float(os.environ.get("CI_SETUP_STARTED", self.started))),
                  "pytest_s": time.time() - self.started, "exitstatus": int(exitstatus),
                  "results": {k: len(v) for k, v in self.results.items()},
                  "host_resource_wait_s": dict(sorted(self.resource_waits.items())),
                  "longest_files": sorted(self.files.items(), key=lambda p: (-p[1], p[0]))[:20]})

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
    parser.add_argument("command", choices=["report"])
    args = parser.parse_args()
    directory = Path(os.environ["CI_SUITE_ARTIFACT_DIR"])
    result = summarize(directory)
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
    (directory / "summary.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    text = json.dumps(result, indent=2)
    print(text)
    with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a", encoding="utf-8") as stream:
        stream.write("### CI coverage and timing (measured)\n\n```json\n" + text + "\n```\n")


if __name__ == "__main__":
    main()
