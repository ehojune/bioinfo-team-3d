"""Content-pinned Windows exclusions. Unknown or changed tests are included."""
import ast
from fnmatch import fnmatchcase
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROFILE = "config/ci/windows-boundary-v1.json"
PROFILES = ("linux-full-v1", "windows-boundary-v1", "windows-full-v1")
BOUNDARY_FILES = ("tests/test_windows*.py", "tests/test_process*.py", "tests/test_proc_tree.py",
    "tests/test_supervisor*.py", "tests/test_runner*.py", "tests/test_cli*.py", "tests/test_mcp*.py",
    "tests/test_state*.py", "tests/test_durable*.py", "tests/test_doctor*.py", "tests/test_wsl*.py",
    "tests/test_reproduction_receipt.py", "tests/test_private_paths.py", "tests/test_shell*.py",
    "tests/test_hook*.py", "tests/test_external_wait*.py", "tests/test_request_bundle.py")
# Inclusion is a union with the reviewed exclusions, never a glob-only subset.
BOUNDARY = re.compile(
    r"windows|win32|sys\.platform|os\.name|ctypes|msvcrt|ntpath|junction|mklink|powershell|cmd\.exe|"
    r"subprocess|Popen|proc_tree|runner|\.cli|MCP|mcp|sqlite|fsync|durable|StateStore|"
    r"reproduction_receipt|doctor|wsl|hook|private_paths|shell|quoting", re.I)


def source_digest(text: str) -> str:
    return hashlib.sha256(text.replace("\r\n", "\n").encode("utf-8")).hexdigest()


def implementation_digest(root=ROOT) -> str:
    """A pure-test review expires on ANY runtime/dependency-source change.

    No call-graph guess: an unchanged test may exercise newly OS-sensitive code.
    Unreviewed runtime additions also force the full Windows cohort.
    """
    # Runtime schemas/catalogs and scanner helpers also affect reviewed tests.
    # Keep the broad expiry instead of guessing a production call graph.
    files = sorted((p for folder in ("labhq", "scripts", "config")
                    for p in (root / folder).rglob("*") if p.is_file()
                    and p.relative_to(root).as_posix() != PROFILE
                    and p.suffix in {".py", ".yaml", ".yml", ".json", ".txt"}),
                   key=lambda p: p.relative_to(root).as_posix())
    for name in ("tests/conftest.py", "tests/test_research_protocol.py", "tests/test_research_cp1.py"):
        if (root / name).is_file():
            files.append(root / name)  # reviewed schema-cohort helper fixtures
    if (root / "pyproject.toml").is_file():
        files.append(root / "pyproject.toml")
    rows = []
    for path in files:
        if path.is_symlink():
            raise ValueError("unreviewed runtime symlink")
        rows.append((path.relative_to(root).as_posix(), source_digest(path.read_text(encoding="utf-8"))))
    return source_digest(json.dumps(rows, separators=(",", ":")))


def policy(root=ROOT) -> dict:
    def unique(pairs):
        result = dict(pairs)
        if len(result) != len(pairs):
            raise ValueError("duplicate selection policy key")
        return result
    data = json.loads((root / PROFILE).read_text(encoding="utf-8"), object_pairs_hook=unique)
    if (set(data) != {"version", "profile", "default", "reviewed_pure", "implementation_sha256"}
            or data["default"] != "include" or type(data["version"]) is not int
            or data["version"] != 1 or data["profile"] != "windows-boundary-v1"):
        raise ValueError("unknown Windows selection policy")
    if data["implementation_sha256"] != implementation_digest(root):
        raise ValueError("pure implementation review is stale; include full Windows")
    for name, row in data["reviewed_pure"].items():
        if (not isinstance(row, dict) or set(row) != {"sha256", "reason"}
                or not all(isinstance(v, str) for v in row.values())
                or not re.fullmatch(r"tests/test_[\w]+\.py", name)
                or not re.fullmatch(r"[0-9a-f]{64}", row["sha256"]) or not row["reason"].strip()):
            raise ValueError("invalid reviewed exclusion")
    return data


def include_windows(path: str, text: str, reviewed: dict) -> tuple[bool, str]:
    row = reviewed.get(path)
    if any(fnmatchcase(path, pattern) for pattern in BOUNDARY_FILES) or BOUNDARY.search(text):
        return True, "OS/runtime/safety boundary in source"
    if not row or source_digest(text) != row["sha256"]:
        return True, "new, changed or unreviewed source"
    return False, row["reason"]


def host_resource(path: Path, root=ROOT, seen=None) -> bool:
    """Serialize host capability calls, not mock class names or quoted prose.

    Imports resolve aliases; local test helpers are followed conservatively.
    Per-worker files/DBs and in-process ASGI clients do not share a host port.
    This is scheduling protection, not permission to execute any fixture.
    """
    path = path.resolve()
    seen = set() if seen is None else seen
    if path in seen:
        return False
    seen.add(path)
    try:
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text)
    except (OSError, UnicodeError, SyntaxError):
        return True
    aliases, imports = {}, []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for name in node.names:
                aliases[name.asname or name.name.split(".")[0]] = name.name if name.asname else name.name.split(".")[0]
                imports.append(name.name)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            imports.append(module)
            for name in node.names:
                qualified = ".".join(p for p in (module, name.name) if p)
                aliases[name.asname or name.name] = qualified
                imports.append(qualified)
                # Port reservation helpers often escape to fixtures/callbacks.
                if name.name == "free_port":
                    return True
    def qualified(node):
        if isinstance(node, ast.Name):
            return aliases.get(node.id, "free_port" if node.id == "free_port" else "")
        if isinstance(node, ast.Attribute):
            return qualified(node.value) + "." + node.attr
        return ""
    capabilities = {
        "socket.socket", "socket.create_server", "socket.create_connection",
        "asyncio.start_server", "asyncio.start_unix_server",
        "asyncio.create_subprocess_exec", "asyncio.create_subprocess_shell",
        "uvicorn.run", "uvicorn.Server", "websockets.serve", "websockets.connect",
        "websockets.asyncio.server.serve", "websockets.asyncio.client.connect",
        "subprocess.Popen", "subprocess.run", "subprocess.call", "subprocess.check_call", "subprocess.check_output",
        "os.kill", "os.killpg", "os.system", "os.fork", "psutil.process_iter", "psutil.Process",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = qualified(node.func)
            if (name in capabilities or name.endswith(".free_port") or name == "free_port"
                    or isinstance(node.func, ast.Attribute) and node.func.attr in {
                        "bind", "listen", "serve_forever", "create_server", "create_subprocess_exec", "create_subprocess_shell"}):
                return True
    for name in imports:
        parts = name.split(".")
        if parts[0] == "tests":
            parts = parts[1:]
        # Try the imported module and its parent (from helper import symbol).
        for count in (len(parts), len(parts) - 1):
            if count > 0:
                helper = root / "tests" / Path(*parts[:count]).with_suffix(".py")
                if helper.is_file() and host_resource(helper, root, seen):
                    return True
    return False
