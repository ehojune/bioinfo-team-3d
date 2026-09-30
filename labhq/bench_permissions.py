"""Claude baseline tool policy: file tools and simple commands in one arm.

This is a tool-call guard, not an OS sandbox. Arbitrary scripts/interpreters
are deliberately not approved on native Windows, where Claude has no sandbox.
"""

import json
from pathlib import Path
import re
import shlex
import sys


def local_path(root: Path, raw: str) -> Path:
    # Claude Bash uses Git Bash paths on Windows.
    if sys.platform == "win32" and re.match(r"^/[a-zA-Z]/", raw):
        raw = raw[1] + ":" + raw[2:]
    return (root / raw).resolve()


def inside(root: Path, raw: str) -> bool:
    if not raw or "~" in raw or re.search(r"[\x00-\x1f*?]", raw):
        return False
    try:
        path = local_path(root, raw)
        relative = path.relative_to(root.resolve())
        return not any(part.casefold() in {".claude", ".git"} for part in relative.parts)
    except (ValueError, OSError, RuntimeError):
        return False


def permits(root: Path, event: dict) -> bool:
    tool = event.get("tool_name")
    data = event.get("tool_input") or {}
    if tool in {"Write", "Edit", "NotebookEdit"}:
        return inside(root, data.get("file_path") or data.get("notebook_path") or "")
    if tool != "Bash":
        return False
    # Fail closed on shell syntax, substitutions, background jobs and wrappers.
    # Paths are resolved (including symlinks/junctions), not prefix-matched.
    command = data.get("command", "")
    if re.search(r"[$`;&|()\r\n]", command):
        return False
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars="<>")
        lexer.whitespace_split = True
        lexer.commenters = ""
        tokens = list(lexer)
    except ValueError:
        return False
    # shlex normally treats '#' as a comment; reject rather than truncate it.
    if "#" in command or not tokens:
        return False
    if ">" in tokens or ">>" in tokens:
        if len(tokens) < 3 or tokens[-2] not in {">", ">>"} or not inside(root, tokens[-1]):
            return False
        tokens = tokens[:-2]
    if any("<" in token or ">" in token for token in tokens) or not tokens:
        return False
    name, *args = tokens
    if name == "pwd":
        return not args
    if name == "echo":
        return True
    if name == "wc":
        return bool(args) and args[0] == "-l" and all(
            not arg.startswith("-") and inside(root, arg) for arg in args[1:])
    if name == "mkdir" and args[:1] == ["-p"]:
        args = args[1:]
    if name in {"mkdir", "touch"}:
        return bool(args) and all(not arg.startswith("-") and inside(root, arg) for arg in args)
    return False


def main() -> int:
    try:
        allowed = permits(Path(sys.argv[1]).resolve(), json.load(sys.stdin))
    except (ValueError, TypeError, AttributeError, IndexError, OSError):
        allowed = False
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse", "permissionDecision": "allow" if allowed else "deny",
        "permissionDecisionReason": "Bench allows file tools and simple commands only inside this arm."
    }}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
