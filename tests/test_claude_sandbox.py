from copy import deepcopy

import pytest

from labhq.runner.claude_sandbox import (
    SandboxUnavailable, baseline_policy, build_launch, validate_policy,
)


def check(managed=None, **overrides):
    sources = {name: {} for name in ("user", "project", "local", "cli")}
    sources.update(overrides)
    validate_policy(managed=baseline_policy() if managed is None else managed, **sources)


def test_isolated_baseline_and_copies():
    first = baseline_policy()
    first["sandbox"]["excludedCommands"].append("curl")
    check()
    assert baseline_policy()["sandbox"]["excludedCommands"] == []


@pytest.mark.parametrize("source", ["user", "project", "local", "cli"])
@pytest.mark.parametrize("setting", [
    {"sandbox": {"enabled": False}},
    {"sandbox": {"excludedCommands": ["curl"]}},
    {"sandbox": {"filesystem": {"disabled": True}}},
    {"sandbox": {"network": {"allowedDomains": ["example.com"]}}},
    {"sandbox": {"network": {"allowUnixSockets": ["/tmp/socket"]}}},
    {"permissions": {"additionalDirectories": ["/"]}},
])
def test_conflicting_sources_are_rejected(source, setting):
    with pytest.raises(SandboxUnavailable):
        check(**{source: setting})


@pytest.mark.parametrize("path,value", [
    (("sandbox", "enabled"), False),
    (("sandbox", "failIfUnavailable"), False),
    (("sandbox", "allowUnsandboxedCommands"), True),
    (("sandbox", "excludedCommands"), ["curl"]),
    (("sandbox", "enableWeakerNestedSandbox"), True),
    (("sandbox", "enableWeakerNetworkIsolation"), True),
    (("sandbox", "filesystem", "disabled"), True),
    (("sandbox", "network", "allowAllUnixSockets"), True),
    (("sandbox", "network", "allowLocalBinding"), True),
    (("sandbox", "enabled"), 1),
])
def test_weakened_managed_policy_rejected(path, value):
    policy = deepcopy(baseline_policy())
    target = policy
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(SandboxUnavailable):
        check(managed=policy)


def test_never_launches_without_runtime_proof():
    check()
    with pytest.raises(SandboxUnavailable, match="unsupported"):
        build_launch()
