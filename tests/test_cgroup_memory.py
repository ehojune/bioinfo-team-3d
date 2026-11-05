"""Read-only cgroup metadata fixtures; no real cgroup or process operations."""
import builtins
import io
from types import SimpleNamespace

import pytest

from labhq.runner import memory_limit

GIB = memory_limit.GIB


@pytest.fixture
def cgroup(monkeypatch):
    files = {}
    reads = []
    def read(path, *args, **kwargs):
        name = str(path).replace("\\", "/")
        reads.append(name)
        value = files.get(name)
        if isinstance(value, Exception):
            raise value
        if value is None:
            raise FileNotFoundError(name)
        return io.StringIO(value)
    monkeypatch.setattr(builtins, "open", read)
    monkeypatch.setattr(memory_limit, "_physical_memory_bytes", lambda: 32 * GIB)
    monkeypatch.setattr(memory_limit, "os", SimpleNamespace(name="posix"))
    return files, reads


def mount(root="/", target="/sys/fs/cgroup", fs="cgroup2"):
    return f"29 23 0:26 {root} {target} rw,nosuid shared:4 - {fs} cgroup rw\n"


def test_nested_service_uses_current_limit(cgroup):
    files, _ = cgroup
    files.update({"/proc/self/cgroup": "0::/system.slice/labhq.service\n",
                  "/proc/self/mountinfo": mount(),
                  "/sys/fs/cgroup/memory.max": "max\n",
                  "/sys/fs/cgroup/system.slice/memory.max": str(8 * GIB),
                  "/sys/fs/cgroup/system.slice/labhq.service/memory.max": str(2 * GIB)})
    assert memory_limit.usable_memory_bytes() == 2 * GIB


@pytest.mark.parametrize("leaf", ["max", str(12 * GIB), "not-a-limit", "", None, PermissionError("denied")])
def test_smaller_parent_survives_unlimited_invalid_missing_leaf(cgroup, leaf):
    files, _ = cgroup
    files.update({"/proc/self/cgroup": "0::/services/nested/worker\n",
                  "/proc/self/mountinfo": mount(),
                  "/sys/fs/cgroup/memory.max": "max",
                  "/sys/fs/cgroup/services/memory.max": str(3 * GIB),
                  "/sys/fs/cgroup/services/nested/memory.max": "max",
                  "/sys/fs/cgroup/services/nested/worker/memory.max": leaf})
    assert memory_limit.usable_memory_bytes() == 3 * GIB


def test_container_bind_mount_root_and_mount_boundary(cgroup):
    files, reads = cgroup
    files.update({"/proc/self/cgroup": "0::/containers/demo/child\n",
                  "/proc/self/mountinfo": mount("/containers/demo", "/cg"),
                  "/cg/child/memory.max": str(9 * GIB),
                  "/cg/memory.max": str(4 * GIB),
                  "/memory.max": "1"})
    assert memory_limit.usable_memory_bytes() == 4 * GIB
    assert "/memory.max" not in reads
    assert "/cg/containers/demo/child/memory.max" not in reads


def test_namespaced_root_membership(cgroup):
    files, _ = cgroup
    files.update({"/proc/self/cgroup": "0::/\n", "/proc/self/mountinfo": mount("/", "/cg"),
                  "/cg/memory.max": str(5 * GIB)})
    assert memory_limit.usable_memory_bytes() == 5 * GIB


def test_escaped_mount_and_all_visible_ancestors(cgroup):
    files, reads = cgroup
    files.update({"/proc/self/cgroup": "0::/services/worker\n",
                  "/proc/self/mountinfo": mount("/services", "/cg\\040alias") + mount("/", "/cg-full"),
                  "/cg alias/worker/memory.max": "max", "/cg alias/memory.max": str(6 * GIB),
                  "/cg-full/services/worker/memory.max": "max", "/cg-full/services/memory.max": str(6 * GIB),
                  "/cg-full/memory.max": str(2 * GIB)})
    assert memory_limit.usable_memory_bytes() == 2 * GIB
    assert "/cg alias/worker/memory.max" in reads


@pytest.mark.parametrize("membership", ["0::/../outside", "0::/a/../../outside", "0::relative", "broken"])
def test_invalid_membership_does_not_escape_mount(cgroup, membership):
    files, reads = cgroup
    files.update({"/proc/self/cgroup": membership, "/proc/self/mountinfo": mount("/", "/cg"),
                  "/outside/memory.max": "1"})
    assert memory_limit.usable_memory_bytes() == 32 * GIB
    assert not any(name.startswith("/cg/") or name == "/outside/memory.max" for name in reads)


def test_unrelated_mount_and_malformed_lines_are_ignored(cgroup):
    files, reads = cgroup
    files.update({"/proc/self/cgroup": "7:memory:/legacy\n0::/service/worker\n",
                  "/proc/self/mountinfo": "broken\n" + mount("/service-other", "/unrelated")
                  + mount("/", "/not-cgroup", "tmpfs") + mount("/", "/cg"),
                  "/unrelated/memory.max": "1", "/not-cgroup/memory.max": "1",
                  "/cg/service/worker/memory.max": str(7 * GIB)})
    assert memory_limit.usable_memory_bytes() == 7 * GIB
    assert not any(name.startswith(("/unrelated/", "/not-cgroup/")) for name in reads)


@pytest.mark.parametrize("root,target", [("/../service", "/cg"), ("/", "/cg/../outside"),
                                         ("relative", "/cg"), ("/", "relative"), ("/", "/cg\\000bad")])
def test_invalid_mount_paths_are_not_read(cgroup, root, target):
    files, reads = cgroup
    files.update({"/proc/self/cgroup": "0::/service/worker", "/proc/self/mountinfo": mount(root, target)})
    assert memory_limit.usable_memory_bytes() == 32 * GIB
    assert reads == ["/proc/self/cgroup", "/proc/self/mountinfo", "/sys/fs/cgroup/memory.max",
                     "/sys/fs/cgroup/memory/memory.limit_in_bytes"]


def test_all_unlimited_keeps_physical_memory(cgroup):
    files, _ = cgroup
    files.update({"/proc/self/cgroup": "0::/service/worker", "/proc/self/mountinfo": mount(),
                  "/sys/fs/cgroup/service/worker/memory.max": "max",
                  "/sys/fs/cgroup/service/memory.max": "max", "/sys/fs/cgroup/memory.max": "max"})
    assert memory_limit.usable_memory_bytes() == 32 * GIB


@pytest.mark.parametrize("metadata", [None, "broken", PermissionError("denied")])
def test_legacy_v1_and_fixed_v2_fallbacks_remain(cgroup, metadata):
    files, _ = cgroup
    files.update({"/proc/self/cgroup": metadata, "/proc/self/mountinfo": metadata,
                  "/sys/fs/cgroup/memory.max": "max",
                  "/sys/fs/cgroup/memory/memory.limit_in_bytes": str(8 * GIB)})
    assert memory_limit.usable_memory_bytes() == 8 * GIB
    files["/sys/fs/cgroup/memory.max"] = str(4 * GIB)
    assert memory_limit.usable_memory_bytes() == 4 * GIB


def test_unlimited_or_invalid_limits_keep_physical_ceiling(cgroup):
    files, _ = cgroup
    files.update({"/proc/self/cgroup": "0::/a/b", "/proc/self/mountinfo": mount(),
                  "/sys/fs/cgroup/memory.max": "max", "/sys/fs/cgroup/a/memory.max": "-1",
                  "/sys/fs/cgroup/a/b/memory.max": "invalid"})
    assert memory_limit.usable_memory_bytes() == 32 * GIB
    files["/sys/fs/cgroup/a/b/memory.max"] = str(64 * GIB)
    assert memory_limit.usable_memory_bytes() == 32 * GIB


def test_unknown_host_keeps_conservative_fallback(cgroup, monkeypatch):
    monkeypatch.setattr(memory_limit, "_physical_memory_bytes", lambda: 0)
    assert memory_limit.usable_memory_bytes() == GIB


def test_windows_does_not_probe_cgroup(cgroup, monkeypatch):
    _, reads = cgroup
    monkeypatch.setattr(memory_limit, "os", SimpleNamespace(name="nt"))
    assert memory_limit.usable_memory_bytes() == 32 * GIB
    assert not reads
