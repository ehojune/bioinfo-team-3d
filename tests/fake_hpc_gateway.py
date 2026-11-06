"""A fake HPC gateway for the session manager's tests (#1299): a program that stands where ``ssh`` would.

It gives an interactive shell on stdin and ignores everything else on its command line, the way a gateway that drops a
remote command does. It understands just enough shell for the session manager's protocol: ``printf`` with ``%s`` and the
last exit code ``$?``, and a few fake commands:

    true            exit 0                      echo TEXT      print TEXT
    fail N          exit N                      big N          print N characters in 100-character lines
    sleep S         wait S seconds              exit           end the connection
    count           print how many connections this gateway was started for (from --counter FILE)

Fake Slurm (with ``--slurm-state FILE``, a JSON file the test can edit to finish a job): ``sbatch`` prints a new job id,
``squeue`` lists the jobs that are still active, ``sacct`` the ended ones, ``scancel ID`` ends one.

Flags: ``--echo`` (a terminal echoes what is typed), ``--banner`` (print a login banner), ``--lock-after S`` (lock the
session when idle for S seconds), ``--drop-after N`` (end the connection after N commands), ``--hostkey-changed`` and
``--tunnel-down`` (print the ssh failure and exit 255), ``--counter FILE`` (append one line per start).
No real network or HPC is touched.
"""
import json
import os
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


def main(argv: list[str]) -> int:
    flags = {"echo": False, "banner": False, "lock": None, "drop": None, "hostkey": False, "tunnel": False,
             "counter": None, "slurm": None}
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
