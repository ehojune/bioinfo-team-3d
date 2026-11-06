"""A fake HPC gateway for the session manager's tests (#1299): a program that stands where ``ssh`` would.

It gives an interactive shell on stdin and ignores everything else on its command line, the way a gateway that drops a
remote command does. It understands just enough shell for the session manager's protocol: ``printf`` with ``%s`` and the
last exit code ``$?``, and a few fake commands:

    true            exit 0                      echo TEXT      print TEXT
    fail N          exit N                      big N          print N characters in 100-character lines
    sleep S         wait S seconds              exit           end the connection
    count           print how many connections this gateway was started for (from --counter FILE)

Files (with ``SLURM_FAKE_SANDBOX`` naming a folder that stands in for the cluster's file system): the commands
``hpc_transfer`` sends (mkdir/cd/pwd, printf >>, base64, sha256sum, mv, rm, test -L, wc -c, tail|head|base64, find) run there.

Fake Slurm (with ``--slurm-state FILE``, a JSON file the test can edit to finish a job): ``sbatch`` prints a new job id,
``squeue`` lists the jobs that are still active, ``sacct`` the ended ones, ``scancel ID`` ends one.

Environment: ``SLURM_FAKE_FAIL_TEXT`` (the connect fails printing it) and ``SLURM_FAKE_SBATCH_ERROR`` (sbatch fails printing it);
they are not flags so that the text is not part of the connect command a profile would hide.

Flags: ``--echo`` (a terminal echoes what is typed), ``--banner`` (print a login banner), ``--lock-after S`` (lock the
session when idle for S seconds), ``--drop-after N`` (end the connection after N commands), ``--hostkey-changed`` and
``--tunnel-down`` (print the ssh failure and exit 255), ``--counter FILE`` (append one line per start).
No real network or HPC is touched.
"""
import json
import os
import re
import shlex
import sys
import threading
import time

ACTIVE = {"PENDING", "RUNNING"}


def slurm(words: list[str], path: str) -> tuple[str, int]:
    """One fake Slurm command against the state file. Returns (output, exit code)."""
    try:
        with open(path, encoding="utf-8") as f:
            state = json.load(f)
    except OSError:
        state = {"next": 1000, "jobs": {}}
    head, jobs, out, status = words[0], state["jobs"], "", 0

    def option(flag: str) -> str:
        return words[words.index(flag) + 1] if flag in words else ""

    if head == "sbatch":
        job_id = str(state["next"])
        state["next"] += 1
        name = next((w.split("=", 1)[1] for w in words if w.startswith("--job-name=")), "job")
        jobs[job_id] = {"name": name, "state": "RUNNING", "exit": "0:0"}
        out = job_id + "\n"
    elif head == "squeue":
        wanted = option("-j").split(",") if "-j" in words else list(jobs)
        out = "".join(f"{i}|{jobs[i]['state']}|None|{jobs[i]['name']}\n" for i in wanted
                      if i in jobs and jobs[i]["state"] in ACTIVE)
    elif head == "sacct":
        wanted = option("-j").split(",")
        out = "".join(f"{i}|{jobs[i]['state']}|{jobs[i]['exit']}|{jobs[i]['name']}\n" for i in wanted
                      if i in jobs and jobs[i]["state"] not in ACTIVE)
    elif head == "scancel":
        if words[-1] in jobs:
            jobs[words[-1]]["state"] = "CANCELLED"
        else:
            out, status = "scancel: error: Invalid job id specified\n", 1
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f)
    return out, status


WRAPPER = re.compile(r"""E=\$\(mktemp\); (.*) 2>"\$E"; S=\$\?; printf '(.*)'; cat "\$E"; rm -f "\$E"; \(exit \$S\)$""")


def command(words: list[str], flags: dict) -> tuple[str, str, int]:
    """A fake scheduler command with stdout and stderr apart: (stdout, stderr, exit status)."""
    head = words[0] if words else ""
    if head == "sbatch" and flags["sbatch_error"]:
        return "", flags["sbatch_error"] + "\n", 1
    if head == "sbatch" and os.environ.get("SLURM_FAKE_SBATCH_OUT"):  # a successful sbatch that prints something odd
        return os.environ["SLURM_FAKE_SBATCH_OUT"] + "\n", "", 0
    if head in ("sbatch", "squeue", "sacct", "scancel") and flags["slurm"]:
        text, status = slurm(words, flags["slurm"])
        warn = os.environ.get("SLURM_FAKE_SBATCH_WARN") if head == "sbatch" and status == 0 else None
        return text, (warn + "\n") if warn else "", status
    if head == "echo":
        return " ".join(words[1:]) + "\n", "", 0
    return "", f"sh: {head}: command not found\n", 127


SANDBOX = os.environ.get("SLURM_FAKE_SANDBOX")  # the folder standing in for the cluster's file system


def _real(path: str) -> str:
    return os.path.join(os.path.realpath(SANDBOX), path.lstrip("/"))


def _shown(real: str) -> str:
    """A path inside the sandbox as the cluster would print it; a path outside it is printed as is (a link that leaves)."""
    base = os.path.realpath(SANDBOX)
    if real == base:
        return "/"
    if real.startswith(base + os.sep):
        return "/" + real[len(base) + 1:].replace(os.sep, "/")
    return real.replace(os.sep, "/")


def files(tokens: list[str]) -> tuple[str, int] | None:
    """The file commands of hpc_transfer, run against the sandbox. None when the line is not one of them."""
    if not SANDBOX:
        return None
    if "&&" in tokens and tokens[0] == "mkdir":  # mkdir [-p] -- D && cd -- D && pwd -P
        target = tokens[tokens.index("--") + 1]
        try:
            os.makedirs(_real(target), exist_ok="-p" in tokens)
        except OSError as error:
            return f"mkdir: {error}\n", 1
        return _shown(os.path.realpath(_real(target))) + "\n", 0
    if tokens[0] == "test" and tokens[1] == "-d" and "||" in tokens:  # test -d D && cd -- D && pwd -P || echo missing
        path = _real(tokens[2])
        return (_shown(os.path.realpath(path)) + "\n", 0) if os.path.isdir(path) else ("missing\n", 0)
    head = tokens[0]
    if head == ":" and tokens[1:2] == [">"]:
        open(_real(tokens[2]), "wb").close()
        return "", 0
    if head == "printf" and ">>" in tokens and tokens[1] == "%s":
        with open(_real(tokens[-1]), "ab") as f:
            f.write(tokens[2].encode("ascii"))
        return "", 0
    if head == "base64" and tokens[1] == "-d":
        import base64

        with open(_real(tokens[3]), "rb") as src, open(_real(tokens[5]), "wb") as dst:
            dst.write(base64.b64decode(src.read()))
        return "", 0
    if head == "sha256sum":
        import hashlib

        path = _real(tokens[-1])
        if not os.path.isfile(path):
            return f"sha256sum: {tokens[-1]}: No such file or directory\n", 1
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest() + "  " + tokens[-1] + "\n", 0
    if head == "chmod":
        return "", 0
    if head == "mv":
        os.replace(_real(tokens[3]), _real(tokens[4]))
        return "", 0
    if head == "rm":
        for name in tokens[3:]:
            try:
                os.unlink(_real(name))
            except OSError:
                pass
        return "", 0
    if head == "test" and tokens[1] == "-L":
        return ("link\n" if os.path.islink(_real(tokens[2])) else "plain\n"), 0
    if head == "wc" and tokens[1] == "-c":
        path = _real(tokens[-1])
        return (f"{os.path.getsize(path)}\n", 0) if os.path.isfile(path) else (f"wc: {tokens[-1]}: No such file\n", 1)
    if head == "tail" and "|" in tokens:  # tail -c +N F | head -c M | base64 | tr -d '\n'
        import base64

        start, path, count = int(tokens[2][1:]), _real(tokens[3]), int(tokens[7])
        with open(path, "rb") as f:
            f.seek(start - 1)
            return base64.b64encode(f.read(count)).decode("ascii"), 0
    if head == "find":
        base, depth = _real(tokens[1]), int(tokens[3])
        out = []
        for root, dirs, names in os.walk(base):
            if root[len(base):].count(os.sep) >= depth:
                dirs[:] = []
            for name in names:
                full = os.path.join(root, name)
                if os.path.isfile(full) and not os.path.islink(full):
                    out.append(f"{os.path.getsize(full)} {os.path.relpath(full, base).replace(os.sep, '/')}\n")
        return "".join(out), 0
    return None


def main(argv: list[str]) -> int:
    flags = {"echo": False, "banner": False, "lock": None, "drop": None, "hostkey": False, "tunnel": False,
             "counter": None, "slurm": None,
             "fail_text": os.environ.get("SLURM_FAKE_FAIL_TEXT"), "sbatch_error": os.environ.get("SLURM_FAKE_SBATCH_ERROR")}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--echo":
            flags["echo"] = True
        elif a == "--banner":
            flags["banner"] = True
        elif a == "--hostkey-changed":
            flags["hostkey"] = True
        elif a == "--tunnel-down":
            flags["tunnel"] = True
        elif a in ("--lock-after", "--drop-after", "--counter", "--slurm-state"):
            i += 1
            key = {"--lock-after": "lock", "--drop-after": "drop", "--counter": "counter", "--slurm-state": "slurm"}[a]
            flags[key] = float(argv[i]) if key == "lock" else int(argv[i]) if key == "drop" else argv[i]
        i += 1  # anything else is a remote command the gateway ignores
    if flags["counter"]:
        with open(flags["counter"], "a", encoding="utf-8") as f:
            f.write("start\n")
    out = sys.stdout
    if flags["fail_text"]:
        out.write(flags["fail_text"] + "\n")
        out.flush()
        return 255
    if flags["hostkey"]:
        out.write("@@@ WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED! @@@\nHost key verification failed.\n")
        out.flush()
        return 255
    if flags["tunnel"]:
        out.write("ssh: connect to host 127.0.0.1 port 2222: Connection refused\n")
        out.flush()
        return 255
    if flags["banner"]:
        out.write("Welcome to the fake cluster\nLast login: never\nfake$ ")
        out.flush()
    last = [time.monotonic()]
    stop = threading.Event()
    if flags["lock"]:
        def lock_watch() -> None:
            while not stop.wait(0.05):
                if time.monotonic() - last[0] > flags["lock"]:
                    out.write("Session locked: idle timeout\n")
                    out.flush()
                    import os
                    os._exit(0)

        threading.Thread(target=lock_watch, daemon=True).start()
    status, count = 0, 0
    for line in sys.stdin:
        last[0] = time.monotonic()
        line = line.rstrip("\r\n")
        if flags["echo"]:
            out.write(line + "\r\n")
        if not line.strip():
            continue
        handled = files(shlex.split(line)) if SANDBOX else None
        if handled is not None:  # a file command of hpc_transfer against the sandbox folder
            out.write(handled[0])
            out.flush()
            status = handled[1]
            continue
        wrapped = WRAPPER.match(line)
        if wrapped:  # the scheduler's one-liner that keeps stderr apart: run the command, stdout, marker, stderr, status
            count += 1
            if flags["drop"] is not None and count > flags["drop"]:
                return 0
            text, err, status = command(shlex.split(wrapped.group(1)), flags)
            out.write(text + wrapped.group(2).replace("\\n", "\n") + err)
            out.flush()
            continue
        words = shlex.split(line)
        head = words[0] if words else ""
        if head != "printf":  # --drop-after counts real commands, not the manager's marker lines
            count += 1
            if flags["drop"] is not None and count > flags["drop"]:
                return 0
        if head == "printf":
            fmt = words[1].replace("\\n", "\n")
            args = [str(status) if w == "$?" else w for w in words[2:]]
            out.write(fmt % tuple(args))
            out.flush()
            continue
        status = 0
        if head == "true":
            pass
        elif head == "stty":
            if "-echo" in words:
                flags["echo"] = False
        elif head == "printenv":
            out.write(os.environ.get(words[1], "") + "\n")
        elif head == "echo":
            out.write(" ".join(words[1:]) + "\n")
        elif head == "fail":
            status = int(words[1])
        elif head == "big":
            total = int(words[1])
            while total > 0:
                n = min(99, total)
                out.write("x" * n + "\n")
                total -= n + 1
        elif head == "sleep":
            out.flush()
            time.sleep(float(words[1]))
        elif head == "exit":
            out.flush()
            return 0
        elif head == "sbatch" and flags["sbatch_error"]:
            out.write(flags["sbatch_error"] + "\n")
            status = 1
        elif head in ("sbatch", "squeue", "sacct", "scancel") and flags["slurm"]:
            text, status = slurm(words, flags["slurm"])
            out.write(text)
        elif head == "nonl":  # output that does not end in a newline
            out.write("tail-without-newline")
        else:
            out.write(f"sh: {head}: command not found\n")
            status = 127
        out.flush()
    stop.set()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
