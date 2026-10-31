"""Versioned Windows cohorts and shared-host scheduling (static analysis only)."""
import ast
from fnmatch import fnmatchcase
from functools import lru_cache
import hashlib
import functools
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Retain the policy path; its schema/profile version, not its filename, is identity.
PROFILE = "config/ci/windows-boundary-v1.json"
WINDOWS_BOUNDARY = "windows-boundary-v6"
WINDOWS_PREVIEW = "windows-boundary-preview-v4"
PROFILES = ("linux-full-v1", WINDOWS_BOUNDARY, "windows-full-v1", WINDOWS_PREVIEW)
# The preview may cross these policy edits only; shared fixtures/dependencies
# still require full selection. It cannot produce reusable validation evidence.
PREVIEW_POLICY = frozenset({".github/workflows/test.yml", "scripts/ci_skip.py",
    "scripts/ci_selection.py", "scripts/ci_suite.py", PROFILE})
SHA = re.compile(r"[0-9a-f]{40}\Z")
PATH = re.compile(r"(?:[\w.-]+/)*[\w.-]+\Z")


@lru_cache(maxsize=512)
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
    if (set(data) != {"version", "profile", "default", "known_tests", "mixed_nodes", "boundary_cohorts", "mandatory_nodes"}
            or data["default"] != "boundary" or type(data["version"]) is not int
            or data["version"] != 6 or data["profile"] != WINDOWS_BOUNDARY
            or not isinstance(data["mixed_nodes"], dict)
            or not isinstance(data["known_tests"], list) or not data["known_tests"]
            or not all(isinstance(p, str) and re.fullmatch(r'tests/test_[\w]+\.py', p) for p in data['known_tests'])
            or data['known_tests'] != sorted(set(data['known_tests']))
            or not isinstance(data["boundary_cohorts"], dict) or not data["boundary_cohorts"]
            or not isinstance(data['mandatory_nodes'], list)
            or not all(isinstance(n, str) and re.fullmatch(r'tests/test_[\w]+\.py::[\w]+', n)
                       for n in data['mandatory_nodes'])
            or data['mandatory_nodes'] != sorted(set(data['mandatory_nodes']))):
        raise ValueError("unknown Windows selection policy")
    for pattern, reason in data["boundary_cohorts"].items():
        if (not isinstance(pattern, str) or not re.fullmatch(r"tests/test_[\w*]+\.py", pattern)
                or not isinstance(reason, str) or not reason.strip()):
            raise ValueError("invalid boundary cohort")
    for name, row in data["mixed_nodes"].items():
        if (not isinstance(row, dict) or set(row) != {"sha256", "reason", "nodes"}
                or not re.fullmatch(r"tests/test_[\w]+\.py", name)
                or not isinstance(row["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", row["sha256"])
                or not isinstance(row["reason"], str) or not row["reason"].strip()
                or not isinstance(row["nodes"], dict) or not row['nodes']):
            raise ValueError("invalid reviewed mixed module")
        for node, reason in row['nodes'].items():
            if (not isinstance(node, str) or not re.fullmatch(r'[\w]+(?:::[\w]+)*', node)
                    or not isinstance(reason, str) or not reason.strip()):
                raise ValueError('invalid protected node')
    return data


def validate_profile(root=ROOT):
    """Fail before pytest startup for missing/stale maintained contracts."""
    data = policy(root)
    files = {p.relative_to(root).as_posix() for p in (root / 'tests').glob('test_*.py')}
    for pattern in data['boundary_cohorts']:
        if not any(fnmatchcase(path, pattern) for path in files):
            raise ValueError('missing essential Windows file contract: ' + pattern)
    for path in files - set(data['known_tests']):
        if not any(fnmatchcase(path, pattern) for pattern in data['boundary_cohorts']):
            raise ValueError('new test requires Windows inventory review: ' + path)
    for path, row in data['mixed_nodes'].items():
        source = (root / path).read_text(encoding='utf-8')
        if source_digest(source) != row['sha256']:
            raise ValueError('stale mixed Windows contract: ' + path)
        tree = _syntax(source)
        for node in row['nodes']:
            _boundary_scope(tree, node, aliases(tree))
    for contract in data['mandatory_nodes']:
        path, node = contract.split('::', 1)
        tree = _syntax((root / path).read_text(encoding='utf-8'))
        _boundary_scope(tree, node, aliases(tree))
    return data


def aliases(tree):
    # _syntax keys trees by exact source content. Return a private mutable copy:
    # source audits may augment relative-import bindings without poisoning CI.
    return dict(_alias_bindings(tree))


@lru_cache(maxsize=512)
def _alias_bindings(tree):
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
    return tuple(result.items())


def qualified(node, names):
    if isinstance(node, ast.Name):
        return names.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        base = qualified(node.value, names)
        return base + "." + node.attr if base else ""
    return ""


def _definitions(tree, names):
    definitions = {n.name: n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
    for definition in list(definitions.values()):
        for d in getattr(definition, 'decorator_list', []):
            if isinstance(d, ast.Call) and qualified(d.func, names) in {'pytest.fixture', 'pytest_asyncio.fixture'}:
                for k in d.keywords:
                    if k.arg == 'name' and isinstance(k.value, ast.Constant) and isinstance(k.value.value, str):
                        definitions[k.value.value] = definition
    return definitions


def _boundary_scope(tree, symbol, names):
    """A test, its referenced local helpers/fixtures and import-time code.

    Classes stay whole. No inference about arbitrary product implementations.
    """
    if not symbol:
        return list(ast.walk(tree))
    definitions, autouse, module_nodes, module_refs, bodies = _scope_index(tree, tuple(names.items()))
    first = symbol.split('::', 1)[0]
    if first not in definitions:
        raise ValueError('unknown collected symbol')
    pending, visited, result = [first, *autouse, *module_refs], set(), list(module_nodes)
    while pending:
        name = pending.pop()
        if name in visited:
            continue
        visited.add(name)
        nodes, dependencies = bodies[name]
        result.extend(nodes)
        pending.extend(dependencies)
    return result


@lru_cache(maxsize=512)
def _scope_index(tree, bindings):
    """Content-bound module work shared by all symbols, never selection results.

    Keep original traversal/dependency order and a fresh result list per caller.
    Neither changed-path evidence nor filesystem/helper decisions are cached here.
    """
    names = dict(bindings)
    definitions = _definitions(tree, names)
    autouse, module_nodes, module_refs, bodies = [], [], [], {}
    for name, definition in definitions.items():
        if any(isinstance(d, ast.Call) and qualified(d.func, names) in {'pytest.fixture', 'pytest_asyncio.fixture'}
               and any(k.arg == 'autouse' and isinstance(k.value, ast.Constant) and k.value.value is True
                       for k in d.keywords) for d in getattr(definition, 'decorator_list', [])):
            autouse.append(name)
    for statement in tree.body:
        if not isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            nodes = list(ast.walk(statement))
            module_nodes.extend(nodes)
            module_refs.extend(n.id for n in nodes if isinstance(n, ast.Name) and n.id in definitions)
    for name, definition in definitions.items():
        nodes = tuple(ast.walk(definition))
        dependencies = []
        for n in nodes:
            dependency = n.id if isinstance(n, ast.Name) else n.arg if isinstance(n, ast.arg) else None
            if dependency in definitions:
                dependencies.append(dependency)
            if isinstance(n, ast.Call) and qualified(n.func, names) == 'pytest.mark.usefixtures':
                dependencies.extend(a.value for a in n.args if isinstance(a, ast.Constant)
                               and isinstance(a.value, str) and a.value in definitions)
        bodies[name] = nodes, tuple(dependencies)
    return definitions, tuple(autouse), tuple(module_nodes), tuple(module_refs), bodies


def intrinsic_boundary(text, *, node=None, root=None, seen=None):
    """Backstop for explicit platform/OS APIs, not arbitrary words in strings."""
    tree = _syntax(text)
    names = aliases(tree)
    nodes = _boundary_scope(tree, node, names)
    seen = set() if seen is None else seen
    modules = ("ctypes", "msvcrt", "ntpath", "_winapi", "winreg", "sqlite3", "filelock",
               "socket", "socketserver", "uvicorn", "psutil",
               "labhq.proc_tree", "labhq.windows", "labhq.durable",
               "labhq.doctor", "labhq.wsl", "labhq.hooks")
    calls = {"os.fsync", "os.replace", "os.rename", "os.symlink", "os.link", "os.chmod",
             "os.kill", "os.killpg", "os.fork", "subprocess.Popen", "subprocess.run", "subprocess.call",
             "subprocess.check_call", "subprocess.check_output", "asyncio.create_subprocess_exec",
             "asyncio.create_subprocess_shell", "threading.Thread", "threading.Timer"}
    for node in nodes:
        if isinstance(node, ast.arg) or isinstance(node, (ast.Name, ast.Attribute)) and isinstance(node.ctx, ast.Load):
            name = names.get(node.arg, '') if isinstance(node, ast.arg) else qualified(node, names)
            if name in calls:
                return True  # escaping an actual native capability is a boundary too
            if any(name == mod or name.startswith(mod + '.') for mod in modules):
                return True
            local = _local_symbol(name, root) if root is not None else None
            if local:
                path, symbols = local
                key = (path, tuple(symbols[:1]))
                if key not in seen:
                    seen.add(key)
                    source = path.read_text(encoding='utf-8')
                    # Constants/module callbacks use conservative whole-module
                    # analysis; function/class helpers use their actual bodies.
                    helper_tree = _syntax(source)
                    helper = symbols[0] if symbols and any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                                and n.name == symbols[0] for n in helper_tree.body) else None
                    if intrinsic_boundary(source, node=helper, root=root, seen=seen):
                        return True
        if isinstance(node, (ast.Name, ast.Attribute)) and qualified(node, names) in {"sys.platform", "os.name"}:
            return True
        if isinstance(node, ast.Call) and qualified(node.func, names) in calls:
            return True
        if isinstance(node, ast.Call) and qualified(node.func, names) == 'pytest.mark.usefixtures':
            nodes.extend(ast.Name(id=a.value, ctx=ast.Load()) for a in node.args
                         if isinstance(a, ast.Constant) and isinstance(a.value, str))
    return False


@lru_cache(maxsize=512)
def _test_imports(tree):
    """Immutable direct import candidates, independent of the changed range."""
    imports = []
    for statement in ast.walk(tree):
        if isinstance(statement, ast.ImportFrom):
            module = (statement.module or '').replace('.', '/')
            candidates = {module + '.py', module + '/__init__.py'}
            candidates.update(module + '/' + alias.name + '.py' for alias in statement.names)
            if statement.level or not module.startswith('tests/'):
                candidates |= {'tests/' + name for name in tuple(candidates)}
        elif isinstance(statement, ast.Import):
            candidates = {alias.name.replace('.', '/') + '.py' for alias in statement.names}
        else:
            continue
        imports.append(frozenset(candidates))
    return tuple(imports)


FIXTURE_ROOTS = ("tests/fixtures/",)  # data and programs a test runs or reads by name; imports do not reach them
_GENERIC_FIXTURE_NAMES = {"__init__", "conftest", "README"}


def changed_fixtures(changed):
    """The changed paths that are fixtures (#1242)."""
    return sorted(path for path in changed if path.startswith(FIXTURE_ROOTS))


def references_fixture(text, fixture):
    """Does this test source name the fixture, by path, by file name or by module name?

    A fixture is often run as a subprocess (`FAKE_CLI = ROOT / "tests/fixtures/x.py"`) or read from disk, so no import
    leads to it. Matching the strings the source spells is wide on purpose: a test that merely mentions the name is
    selected too, never the other way round. A fixture that is a common file name (`README.md`) is matched by its path
    only, since its bare name appears in tests that have nothing to do with it. A test that takes a whole folder
    (`copytree("tests/fixtures/wiki")`) names the folders above the fixture, so those count too."""
    name = fixture.rsplit("/", 1)[-1]
    stem = name.rsplit(".", 1)[0] if "." in name else name
    short = fixture[len("tests/"):] if fixture.startswith("tests/") else fixture
    names = {fixture, short}
    if stem not in _GENERIC_FIXTURE_NAMES:
        names.add(name)
        if len(stem) >= 4:
            names.add(stem)
    parent = fixture.rsplit("/", 1)[0]
    while parent.count("/") >= 2:  # tests/fixtures/<folder>[/...]: every folder below tests/fixtures
        names |= {parent, parent[len("tests/"):]}
        parent = parent.rsplit("/", 1)[0]
    if any(candidate and candidate in text for candidate in names):
        return True
    # pathlib joins spell the folders one by one: `Path(__file__).parent / "fixtures" / "wiki"`
    folders = fixture.split("/")[2:-1]
    quoted = lambda part: ('"' + part + '"') in text or ("'" + part + "'") in text
    return bool(folders) and quoted("fixtures") and all(quoted(folder) for folder in folders)


@functools.lru_cache(maxsize=64)
def _fixture_named_anywhere(root, fixture):
    """Does any collected test file (tests/test_*.py) name this fixture? One scan per root and fixture per process:
    the changed range does not move while a job runs."""
    for test in sorted((Path(root) / "tests").glob("test_*.py")):
        try:
            if references_fixture(test.read_text(encoding="utf-8", errors="replace"), fixture):
                return True
        except OSError:
            continue
    return False


def _fixture_reason(text, changed, root):
    """Why this test runs for a changed fixture, else None. A fixture no collected test names cannot be tied to a
    test, so every test is chosen rather than guessed (#1242)."""
    for fixture in changed_fixtures(changed):
        if references_fixture(text, fixture):
            return 'changed fixture this test refers to'
        if not _fixture_named_anywhere(str(Path(root).resolve()), fixture):
            return 'changed fixture no collected test names; all routine Windows tests'
    return None


def include_windows(path, text, data, *, root=ROOT, changed=frozenset(), node=None):
    """Maintained essential contracts + direct OS APIs, never product transitivity.

    Changed Windows-relevant tests stay included. Changed ordinary logic is
    covered by both full Linux suites and nightly/manual full Windows.
    Missing inventory/scope evidence fails, rather than silently expanding full.
    """
    row = data['mixed_nodes'].get(path)
    symbol = node.split('[', 1)[0] if node is not None else None
    if any(n == path + '::' + symbol if symbol is not None else n.startswith(path + '::')
           for n in data['mandatory_nodes']):
        return True, 'mandatory native regression contract'
    if row:
        if source_digest(text) != row['sha256']:
            raise ValueError('mixed-module contract drift; update reviewed profile')
        if path in changed or node is None:
            return True, 'changed Windows-relevant mixed test or file contract'
        reason = _fixture_reason(text, changed, root)
        if reason:
            return True, reason
        symbol = node.split('[', 1)[0]
        if symbol in row['nodes']:
            return True, row['nodes'][symbol]
        try:
            if intrinsic_boundary(text, node=symbol):
                return True, 'native API backstop in mixed-module test/helper'
        except (OSError, UnicodeError, ValueError, SyntaxError) as exc:
            raise ValueError('unresolved mixed-module contract') from exc
        parts = symbol.split('::')
        body = _syntax(text).body
        for part in parts:
            found = next((n for n in body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                          and n.name == part), None)
            if found is None:
                raise ValueError('unknown mixed-module node')
            body = found.body
        return False, row['reason']
    if any(fnmatchcase(path, pattern) for pattern in data['boundary_cohorts']):
        return True, "maintained essential Windows/OS contract"
    if path not in data['known_tests']:
        raise ValueError('unlisted collection; update Windows policy inventory')
    if node is not None:
        _boundary_scope(_syntax(text), node.split('[', 1)[0], aliases(_syntax(text)))
    if path in changed and intrinsic_boundary(text, node=node.split('[', 1)[0] if node else None):
        return True, "direct OS/runtime API in test/local fixture scope"
    reason = _fixture_reason(text, changed, root)
    if reason:
        return True, reason
    # Direct helper edits retain their consumers, without a speculative
    # transitive walk through arbitrary product/runtime implementations.
    for imports in _test_imports(_syntax(text)):
        for dependency in imports.intersection(changed):
            if dependency.startswith('tests/'):
                helper = root / dependency
                helper.resolve().relative_to(root.resolve())
                if helper.is_symlink():
                    raise ValueError('unreviewed linked test helper')
                if intrinsic_boundary(helper.read_text(encoding='utf-8')):
                    return True, 'directly imported changed OS test helper'
    return False, 'ordinary logic/schema/text; full Linux and nightly/manual Windows'


def change_range(event_name, event, checkout, git, *, preview=False):
    """Bind changed paths to the checked-out commit and its tested base.

    This evidence selects tests only; it never authorizes artifact reuse.
    git is injected in tests; this function never fetches or contacts GitHub.
    """
    if not isinstance(checkout, str) or not SHA.fullmatch(checkout) or git("rev-parse", "HEAD") != checkout:
        raise ValueError("unbound selection checkout")
    if preview:
        from scripts.ci_skip import pr_snapshot
        if event_name != "pull_request":
            raise ValueError("preview requires a trusted PR")
        pr_snapshot(event["pull_request"])
    if event_name == "pull_request":
        pr = event["pull_request"]
        event_base, head = pr["base"]["sha"], pr["head"]["sha"]
        parents = git("rev-list", "--parents", "-n", "1", checkout).split()
        if (not isinstance(event_base, str) or not isinstance(head, str)
                or not SHA.fullmatch(event_base) or not SHA.fullmatch(head) or event_base == head
                or len(parents) != 3 or parents[0] != checkout or parents[2] != head
                or not SHA.fullmatch(parents[1]) or parents[1] == head
                or (parents[1] != event_base and (preview
                    or git("merge-base", event_base, parents[1]) != event_base))):
            raise ValueError("unbound selection merge parents")
        base = parents[1]
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
    # The retired preview command still accepts only its original narrow range.
    # Routine policy/fixture edits run maintained essentials, not full Windows.
    from scripts.ci_skip import POLICY_FILES
    if preview and any(p in POLICY_FILES and p not in PREVIEW_POLICY
           or p.startswith("tests/") and not re.fullmatch(r"tests/test_[\w]+\.py", p)
           for p in paths):
        raise ValueError("shared fixture/policy changed; full validation")
    result = {"version": 1, "checkout": checkout, "base": base, "paths": sorted(paths)}
    if preview:
        if not PREVIEW_POLICY.intersection(paths):
            raise ValueError("no policy change to preview")
        result.update(mode="preview", reusable=False)
    return result


def preview_range(event_name, event, checkout, git, environ):
    """Exact same-repository merge/workflow, with the real changed paths retained."""
    from scripts.ci_skip import REPOSITORY, WORKFLOW
    result = change_range(event_name, event, checkout, git, preview=True)
    number = event["pull_request"]["number"]
    if (environ.get("GITHUB_REPOSITORY") != REPOSITORY
            or environ.get("GITHUB_WORKFLOW_SHA") != checkout
            or environ.get("GITHUB_WORKFLOW_REF") != f"{REPOSITORY}/{WORKFLOW}@refs/pull/{number}/merge"):
        raise ValueError("unbound preview workflow")
    return result


def read_changes(directory, checkout, *, preview=False):
    name = "preview-changes.json" if preview else "changes.json"
    data = json.loads((Path(directory) / name).read_text(encoding="utf-8"), object_pairs_hook=_unique)
    keys = {"version", "checkout", "base", "paths"} | ({"mode", "reusable"} if preview else set())
    if (set(data) != keys or (preview and (data["mode"] != "preview" or data["reusable"] is not False))
            or type(data["version"]) is not int
            or data["version"] != 1 or not isinstance(checkout, str) or not SHA.fullmatch(checkout)
            or data["checkout"] != checkout or not isinstance(data["base"], str)
            or not SHA.fullmatch(data["base"]) or data["base"] in {"0" * 40, checkout}
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
        definitions = _definitions(tree, names)
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
            dependency = node.id if isinstance(node, ast.Name) else node.arg if isinstance(node, ast.arg) else None
            if dependency in definitions and dependency != symbols[0]:
                if host_resource(path, root, seen, [dependency]):
                    return True
        # Autouse fixtures execute even without a test argument. Their teardown
        # is inside the protocol lock just like explicitly requested fixtures.
        for name, definition in definitions.items():
            if any(isinstance(d, ast.Call) and qualified(d.func, names) in {'pytest.fixture', 'pytest_asyncio.fixture'}
                   and any(k.arg == 'autouse' and isinstance(k.value, ast.Constant) and k.value.value is True
                           for k in d.keywords) for d in getattr(definition, 'decorator_list', [])):
                if host_resource(path, root, seen, [name]):
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
    def runner_is_ephemeral(constructor):
        # Reviewed daemon.__init__ snapshots settings.runner.broker_port into
        # Broker. Accept only a local Settings with one unconditional zero
        # assignment before construction and no intervening settings escape.
        args = constructor.args[:1] + [k.value for k in constructor.keywords if k.arg == 'settings']
        if len(args) != 1 or not isinstance(args[0], ast.Name):
            return False
        setting = args[0]
        bound = values(setting)
        if (len(bound) != 1 or not isinstance(bound[0], ast.Call)
                or qualified(bound[0].func, names) != 'labhq.settings.Settings'):
            return False
        owner = scope(constructor)
        if scope(bound[0]) is not owner:
            return False
        stores = []
        for n in ast.walk(owner):
            if getattr(n, 'lineno', 0) >= constructor.lineno:
                continue
            if isinstance(n, ast.Name) and n.id == setting.id and isinstance(n.ctx, ast.Load):
                parent = parents.get(n)
                # Only field assignment receivers are allowed; calls, aliases,
                # conditional writes and passing settings elsewhere are unknown.
                while isinstance(parent, ast.Attribute):
                    n, parent = parent, parents.get(parent)
                if not (isinstance(parent, ast.Assign) and n in parent.targets and parents.get(parent) is owner):
                    return False
                field = qualified(n, {})
                if field == setting.id + '.runner':
                    return False
                if field == setting.id + '.runner.broker_port':
                    stores.append(parent.value)
        return len(stores) == 1 and ephemeral(stores[0])
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
                if not runner_is_ephemeral(value):
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
    # shutdown.watch_gateway_exit -> cli._terminate_tree -> proc_tree.kill_tree
    # (including the system snapshot). Verified wrappers, not name heuristics.
    global_caps.update({'labhq.gateway.shutdown.watch_gateway_exit', 'labhq.cli._terminate_tree'})
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
        if name == 'pytest.mark.usefixtures':
            for arg in node.args:
                if not isinstance(arg, ast.Constant) or not isinstance(arg.value, str):
                    return True
                fixture = arg.value
                local = _local_symbol(names.get(fixture, ''), root)
                if fixture in _definitions(tree, names) and host_resource(path, root, seen, [fixture]):
                    return True
                if local and host_resource(local[0], root, seen, local[1]):
                    return True
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
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("command", nargs="?", choices=["preview-plan", "preview"])
    args = parser.parse_args()
    preview = args.command is not None
    try:
        event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
        if preview:
            result = preview_range(os.environ["GITHUB_EVENT_NAME"], event, os.environ["GITHUB_SHA"], git, os.environ)
        else:
            result = change_range(os.environ["GITHUB_EVENT_NAME"], event, os.environ["GITHUB_SHA"], git)
        validate_profile()
    except Exception as exc:
        # Our ValueError messages name validation checks; never emit raw event,
        # environment, command output or an arbitrary exception message.
        check = str(exc).split(':', 1)[0] if type(exc) is ValueError else type(exc).__name__
        if os.environ.get("CI_SUITE_ARTIFACT_DIR"):
            directory = Path(os.environ["CI_SUITE_ARTIFACT_DIR"])
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "selection-error.json").write_text(json.dumps({
                "status": "invalid-selection", "reusable": False,
                "error": type(exc).__name__, "check": check}), encoding="utf-8")
        print("ci_selection: invalid selection; refusing routine Windows (" + check + ")", file=sys.stderr)
        raise SystemExit(2) from exc
    if args.command == "preview-plan":
        print("enabled=" + str(result.get("mode") == "preview").lower())
        return
    if preview and result.get("mode") != "preview":
        raise ValueError("preview range unavailable; compulsory full validation is unchanged")
    directory = Path(os.environ["CI_SUITE_ARTIFACT_DIR"])
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / ("preview-changes.json" if preview else "changes.json")
    destination.write_text(json.dumps(result, sort_keys=True), encoding="utf-8")


if __name__ == "__main__":
    main()
