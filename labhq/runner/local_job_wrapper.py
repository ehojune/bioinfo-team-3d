"""Runs one local job's shell command and records its exit code (#675). Standard library only.

    python local_job_wrapper.py <exit-file> <shell> [shell args…] <command>

The runner starts this instead of the command itself. When the runner restarts it can still tell how the job ended
from the exit file, which is written after the command returns. A wrapper that is killed (a cancel) writes none.
"""
import os
import subprocess
import sys


def main(argv: list[str]) -> int:
    exit_file, command = argv[1], argv[2:]
    try:
        code = subprocess.call(command)
    except OSError as exc:
        print(f"could not start the command: {exc}", file=sys.stderr)
        code = 127
    temporary = exit_file + ".tmp"
    with open(temporary, "w", encoding="ascii") as handle:
        handle.write(str(code))
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, exit_file)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
