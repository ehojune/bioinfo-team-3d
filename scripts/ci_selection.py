"""Versioned Windows cohorts and shared-host scheduling (static analysis only)."""
import ast
from fnmatch import fnmatchcase
from functools import lru_cache
import hashlib
import json
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Retain the policy path; its schema/profile version, not its filename, is identity.
PROFILE = "config/ci/windows-boundary-v1.json"
WINDOWS_BOUNDARY = "windows-boundary-v2"
PROFILES = ("linux-full-v1", WINDOWS_BOUNDARY, "windows-full-v1")
SHA = re.compile(r"[0-9a-f]{40}\Z")
PATH = re.compile(r"(?:[\w.-]+/)*[\w.-]+\Z")


def source_digest(text: str) -> str:
    return hashlib.sha256(text.replace("\r\n", "\n").encode("utf-8")).hexdigest()


@lru_cache(maxsize=512)
def _syntax(text):
    # Cache by content, not pathname: rewriting a fixture must invalidate it.
    return ast.parse(text, feature_version=(3, 10))


def _unique(pairs):
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError("duplicate selection policy key")
    return result


def policy(root=ROOT) -> dict:
    data = json.loads((root / PROFILE).read_text(encoding="utf-8"), object_pairs_hook=_unique)
    if (set(data) != {"version", "profile", "default", "reviewed_pure", "boundary_cohorts"}
            or data["default"] != "include" or type(data["version"]) is not int
            or data["version"] != 2 or data["profile"] != WINDOWS_BOUNDARY
            or not isinstance(data["reviewed_pure"], dict)
            or not isinstance(data["boundary_cohorts"], dict) or not data["boundary_cohorts"]):
        raise ValueError("unknown Windows selection policy")
    for pattern, reason in data["boundary_cohorts"].items():
        if (not isinstance(pattern, str) or not re.fullmatch(r"tests/test_[\w*]+\.py", pattern)
                or not isinstance(reason, str) or not reason.strip()):
            raise ValueError("invalid boundary cohort")
    for name, row in data["reviewed_pure"].items():
        if (not isinstance(row, dict) or set(row) != {"sha256", "reason", "helpers"}
                or not re.fullmatch(r"tests/test_[\w]+\.py", name)
                or not isinstance(row["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", row["sha256"])
                or not isinstance(row["reason"], str) or not row["reason"].strip()
                or not isinstance(row["helpers"], dict)):
            raise ValueError("invalid reviewed exclusion")
        for helper, digest in row["helpers"].items():
            if (not isinstance(helper, str) or not re.fullmatch(r"tests/(?:[\w]+/)*[\w]+\.py", helper)
                    or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)):
                raise ValueError("invalid reviewed helper")
    return data


def aliases(tree):
    result = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for item in node.names:
                result[item.asname or item.name.split(".")[0]] = item.name if item.asname else item.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom):
            for item in node.names:
                result[item.asname or item.name] = ".".join(filter(None, (node.module, item.name)))
    # Follow simple capability/module aliases, never infer from identifier names.
    bindings = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    bindings.setdefault(target.id, {})[ast.dump(node.value)] = node.value
    roots = {name.split(".")[0] for name in result.values()}
    for _ in range(len(bindings)):
        changed = False
        for target, choices in bindings.items():
            if target in result or len(choices) != 1:
                continue
            value = next(iter(choices.values()))
            if not isinstance(value, (ast.Name, ast.Attribute)):
                continue
            name = qualified(value, result)
            if name.split(".")[0] in roots:
                result[target] = name
                changed = True
        if not changed:
            break
    return result


def qualified(node, names):
    if isinstance(node, ast.Name):
        return names.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        base = qualified(node.value, names)
        return base + "." + node.attr if base else ""
    return ""


def intrinsic_boundary(text):
    """Backstop for explicit platform/OS APIs, not arbitrary words in strings."""
    tree = _syntax(text)
    names = aliases(tree)
    modules = ("ctypes", "msvcrt", "ntpath", "_winapi", "winreg", "sqlite3", "filelock",
               "labhq.runner", "labhq.cli", "labhq.proc_tree", "labhq.windows", "labhq.durable",
               "labhq.store", "labhq.doctor", "labhq.wsl", "labhq.hooks", "mcp")
    if any(any(name == mod or name.startswith(mod + ".") for mod in modules) for name in names.values()):
        return True
    calls = {"os.fsync", "os.replace", "os.rename", "os.symlink", "os.link", "os.chmod",
             "os.kill", "os.killpg", "os.fork", "subprocess.Popen", "subprocess.run", "subprocess.call",
             "subprocess.check_call", "subprocess.check_output", "asyncio.create_subprocess_exec",
             "asyncio.create_subprocess_shell", "threading.Thread", "threading.Timer"}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Name, ast.Attribute)) and qualified(node, names) in {"sys.platform", "os.name"}:
            return True
        if isinstance(node, ast.Call) and qualified(node.func, names) in calls:
            return True
    return False


def include_windows(path, text, reviewed, *, root=ROOT, cohorts=None, changed=frozenset()):
    if path in changed:
        return True, "changed test in validated event range"
    if cohorts is None:
        cohorts = policy(root)["boundary_cohorts"]
    if any(fnmatchcase(path, pattern) for pattern in cohorts):
        return True, "reviewed OS/runtime/safety boundary cohort"
    row = reviewed.get(path)
    if not row or source_digest(text) != row["sha256"]:
        return True, "new, changed or unreviewed source"
    if intrinsic_boundary(text):
        return True, "explicit OS/runtime API in source"
    for helper, digest in row["helpers"].items():
        p = root / helper
        if helper in changed or p.is_symlink() or source_digest(p.read_text(encoding="utf-8")) != digest:
            return True, "changed reviewed test helper"
    return False, row["reason"]


def change_range(event_name, event, checkout, git):
    """Immutable local Git + event snapshot. Missing/drifted range runs full.

    This evidence selects tests only; it never authorizes artifact reuse.
    git is injected in tests; this function never fetches or contacts GitHub.
    """
    if not isinstance(checkout, str) or not SHA.fullmatch(checkout) or git("rev-parse", "HEAD") != checkout:
        raise ValueError("unbound selection checkout")
    if event_name == "pull_request":
        pr = event["pull_request"]
        base, head = pr["base"]["sha"], pr["head"]["sha"]
        if (not isinstance(base, str) or not isinstance(head, str) or not SHA.fullmatch(base)
                or not SHA.fullmatch(head) or base == head
                or git("rev-list", "--parents", "-n", "1", checkout).split() != [checkout, base, head]):
            raise ValueError("unbound selection merge parents")
    elif event_name == "push":
        base = event["before"]
        if (event["after"] != checkout or not isinstance(base, str) or not SHA.fullmatch(base)
                or base == "0" * 40 or git("merge-base", base, checkout) != base):
            raise ValueError("unbound selection push range")
    else:
        raise ValueError("full event profile")
    raw = git("diff", "--name-only", "-z", "--no-renames", base, checkout)
    if raw and not raw.endswith("\0"):
        raise ValueError("truncated changed path evidence")
    paths = raw.split("\0")[:-1] if raw else []
    if len(paths) != len(set(paths)) or any(not PATH.fullmatch(p) or any(x in {".", ".."} for x in p.split("/")) for p in paths):
        raise ValueError("invalid changed path evidence")
    # Shared collection fixtures, policy and dependencies require initial full
    # validation. An ordinary product edit no longer expires the entire cohort.
    from scripts.ci_skip import POLICY_FILES
    if any(p in POLICY_FILES or p.startswith("tests/") and p.endswith(".py") and not re.fullmatch(r"tests/test_[\w]+\.py", p)
           for p in paths):
        raise ValueError("shared fixture/policy changed; full validation")
    return {"version": 1, "checkout": checkout, "base": base, "paths": sorted(paths)}


def read_changes(directory, checkout):
    data = json.loads((Path(directory) / "changes.json").read_text(encoding="utf-8"), object_pairs_hook=_unique)
    if (set(data) != {"version", "checkout", "base", "paths"} or type(data["version"]) is not int
            or data["version"] != 1 or not isinstance(checkout, str) or not SHA.fullmatch(checkout)
            or data["checkout"] != checkout or not isinstance(data["base"], str)
            or not SHA.fullmatch(data["base"]) or data["base"] == "0" * 40
            or not isinstance(data["paths"], list) or not all(isinstance(p, str) and PATH.fullmatch(p)
                and all(x not in {".", ".."} for x in p.split("/")) for p in data["paths"])
            or data["paths"] != sorted(set(data["paths"]))):
        raise ValueError("invalid selection change receipt")
    return set(data["paths"])


def _local_symbol(name, root):
    if not name:
        return None
    parts = name.split(".")
    if parts[0] == "tests":
        parts = parts[1:]
    # Resolve a module or a from-imported symbol without executing/importing it.
    for count in range(len(parts), 0, -1):
        path = root / "tests" / Path(*parts[:count]).with_suffix(".py")
        if path.is_file():
            return path, parts[count:]
    return None


def host_resource(path: Path, root=ROOT, seen=None, symbols=None) -> bool:
    """Only shared host capabilities. Owned children/ephemeral binds are local.

    Follow referenced helper symbols (including imported fixture arguments), not
    every import's module body. Unknown bind/server port expressions stay locked.
    The protocol wrapper retains setup-through-teardown lock ownership.
    """
    path = path.resolve()
    if not path.is_relative_to(root.resolve()):
        return True
    seen = set() if seen is None else seen
    # A class reference analyzes the whole class, regardless of attribute tail.
    key = (path, tuple((symbols or ())[:1]))
    if key in seen:
        return False
    seen.add(key)
    try:
        tree = _syntax(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, SyntaxError):
        return True
    names = aliases(tree)
    if symbols:
        definitions = {n.name: n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
        values = {target.id: n.value for n in tree.body if isinstance(n, ast.Assign)
                  for target in n.targets if isinstance(target, ast.Name)}
        selected = definitions.get(symbols[0], values.get(symbols[0]))
        if symbols == ["<module>"]:
            selected = ast.Module(body=[], type_ignores=[])
        if selected is None:
            return True  # unresolved referenced helper, never certify it as local
        nodes = list(ast.walk(selected))
        # Import-time statements execute even when a helper is unused; unused
        # function bodies do not. Constant imports must not pull every test in.
        for statement in tree.body:
            if not isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                nodes.extend(ast.walk(statement))
        # Follow sibling helpers referenced by the selected function/class too.
        for node in nodes[:]:
            if isinstance(node, ast.Name) and node.id in definitions and node.id != symbols[0]:
                if host_resource(path, root, seen, [node.id]):
                    return True
    else:
        nodes = list(ast.walk(tree))
    for imported in names.values():
        local = _local_symbol(imported, root)
        if local and host_resource(local[0], root, seen, ["<module>"]):
            return True
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    def scope(node):
        while node in parents:
            node = parents[node]
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)):
                return node
        return tree
    assignments = {}
    classes = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and not n.bases}
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            parent, owner = parents.get(node), scope(node)
            # Conditional/unpacked/rebound values are not an isolation proof.
            value = parent.value if isinstance(parent, ast.Assign) and parents.get(parent) is owner else None
            assignments.setdefault((owner, node.id), []).append(value)
        elif isinstance(node, ast.arg):
            assignments.setdefault((scope(node), node.arg), []).append(None)
    def values(node):
        if not isinstance(node, ast.Name):
            return [node]
        owner = scope(node)
        while True:
            if (owner, node.id) in assignments:
                return assignments[owner, node.id]
            if owner is tree:
                return []
            owner = scope(owner)
            # Python methods do not close over their class's local namespace.
            while isinstance(owner, ast.ClassDef):
                owner = scope(owner)
    def ephemeral(node, visiting=frozenset()):
        if isinstance(node, ast.Constant):
            return type(node.value) is int and node.value == 0
        if isinstance(node, ast.Name) and (scope(node), node.id) not in visiting:
            bound = values(node)
            return bool(bound) and all(ephemeral(v, visiting | {(scope(node), node.id)}) for v in bound)
        return (isinstance(node, ast.Call) and qualified(node.func, names) == "labhq.util.free_port")
    def local_method(receiver, method):
        if not isinstance(receiver, ast.Name):
            return False
        bound = values(receiver)
        return bool(bound) and all(isinstance(v, ast.Call) and isinstance(v.func, ast.Name)
            and v.func.id in classes and any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                and n.name == method for n in classes[v.func.id].body) for v in bound)
    def config_is_ephemeral(node):
        configs = values(node)
        return bool(configs) and all(isinstance(c, ast.Call) and qualified(c.func, names) == "uvicorn.Config"
            and any(k.arg == "port" and ephemeral(k.value) for k in c.keywords) for c in configs)
    def starts_shared_server(node):
        if not isinstance(node.func, ast.Attribute):
            return False
        for value in values(node.func.value):
            if not isinstance(value, ast.Call):
                continue
            kind = qualified(value.func, names)
            if node.func.attr == "serve" and kind == "uvicorn.Server":
                configs = value.args[:1] + [k.value for k in value.keywords if k.arg == "config"]
                if len(configs) != 1 or not config_is_ephemeral(configs[0]):
                    return True
            if node.func.attr == "serve" and kind == "labhq.runner.approvals.Broker":
                ports = value.args[:1] + [k.value for k in value.keywords if k.arg == "port"]
                if len(ports) != 1 or not ephemeral(ports[0]):
                    return True
            if node.func.attr == "run_forever" and kind == "labhq.runner.daemon.Runner":
                # This actual API starts Broker.serve (daemon.py), whose mutable
                # Settings port is not proved isolated by a constructor call.
                return True
        return False
    global_caps = {"psutil.process_iter", "psutil.pids", "psutil.net_connections", "os.killpg", "os.system"}
    # Reviewed implementation entrypoints, not identifier-name heuristics:
    # proc_tree._table uses CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS);
    # adapter snapshots enumerate Win32_Process, /proc or the system ps table.
    # Only referenced capabilities count; importing the module/Proc model does not.
    global_caps.update("labhq.proc_tree." + name for name in
                       ("_table", "_snapshot", "child_pids", "kill_tree", "pin_ancestors"))
    global_caps.update("labhq.adapters.base." + name for name in
                       ("_windows_process_snapshot", "_procfs_process_snapshot", "_ps_process_snapshot",
                        "_process_snapshot", "_idle_process_tree"))
    servers = {"asyncio.start_server", "asyncio.start_unix_server", "socket.create_server",
               "uvicorn.run", "websockets.serve", "websockets.asyncio.server.serve"}
    address_servers = {"socketserver.TCPServer", "socketserver.ThreadingTCPServer", "socketserver.UDPServer",
                       "socketserver.ThreadingUDPServer", "http.server.HTTPServer", "http.server.ThreadingHTTPServer"}
    children = {"subprocess.Popen", "subprocess.run", "subprocess.call", "subprocess.check_call",
                "subprocess.check_output", "asyncio.create_subprocess_exec", "asyncio.create_subprocess_shell"}
    for node in nodes:
        if isinstance(node, (ast.Name, ast.Attribute)) and isinstance(node.ctx, ast.Load):
            name = qualified(node, names)
            if name in global_caps:
                return True
            if name in servers | address_servers and not (isinstance(parents.get(node), ast.Call)
                    and parents[node].func is node):
                return True  # an actual listener capability escapes with unknown arguments
            local = _local_symbol(name, root)
            if local and local[1] and host_resource(local[0], root, seen, local[1]):
                return True
            if (local and not local[1] and not isinstance(parents.get(node), ast.Attribute)
                    and host_resource(local[0], root, seen)):
                return True  # a module escapes to a callback rather than a known symbol
        if isinstance(node, ast.arg) and node.arg in names:
            local = _local_symbol(names[node.arg], root)
            if local and host_resource(local[0], root, seen, local[1]):
                return True
        if not isinstance(node, ast.Call):
            continue
        name = qualified(node.func, names)
        if starts_shared_server(node):
            return True
        if name in servers:
            ports = [k.value for k in node.keywords if k.arg == "port"]
            if name in {"asyncio.start_server", "websockets.serve", "websockets.asyncio.server.serve"} and len(node.args) >= 3:
                ports.append(node.args[2])
            if name == "socket.create_server" and node.args and isinstance(node.args[0], (ast.Tuple, ast.List)):
                ports.append(node.args[0].elts[-1])
            if not ports or not all(ephemeral(p) for p in ports):
                return True
        if name in address_servers:
            addresses = node.args[:1] + [k.value for k in node.keywords if k.arg == "server_address"]
            if (len(addresses) != 1 or not isinstance(addresses[0], (ast.Tuple, ast.List))
                    or len(addresses[0].elts) != 2 or not ephemeral(addresses[0].elts[1])):
                return True
        if isinstance(node.func, ast.Attribute) and node.func.attr == "bind":
            if local_method(node.func.value, "bind"):
                continue  # its local method body is analyzed separately above
            address = node.args[0] if len(node.args) == 1 else None
            if not isinstance(address, (ast.Tuple, ast.List)) or len(address.elts) != 2 or not ephemeral(address.elts[1]):
                return True
        if name in children:
            # Owned children are independent. Commands which scan shared host
            # state are not: retain a conservative lock even with PID filters.
            command = " ".join(n.value for arg in node.args for n in ast.walk(arg)
                               if isinstance(n, ast.Constant) and isinstance(n.value, str))
            if re.search(r"(?i)(?:\b(?:tasklist|pgrep|pkill|netstat|lsof|Get-Process)\b|(?:^|\s)ps(?:\s|$))", command):
                return True
    return False


def main():
    # Invoked only in normal CI. Local tests inject git into change_range.
    from scripts.ci_skip import git
    destination = Path(os.environ["CI_SUITE_ARTIFACT_DIR"]) / "changes.json"
    try:
        event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
        result = change_range(os.environ["GITHUB_EVENT_NAME"], event, os.environ["GITHUB_SHA"], git)
    except Exception as exc:
        # Missing evidence is explicitly not a reusable/eligible receipt.
        result = {"full": True, "reason": type(exc).__name__}
        print("ci_selection: invalid/unavailable change range; full Windows")
    destination.write_text(json.dumps(result, sort_keys=True), encoding="utf-8")


if __name__ == "__main__":
    main()
