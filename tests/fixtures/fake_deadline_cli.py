"""Fake Claude CLI for the request-deadline process-tree test (#1232): no model call.

It announces itself, starts a child and a grandchild (on Windows also a grandchild's detached child, and a try to
leave the Job with CREATE_BREAKAWAY_FROM_JOB) and then never finishes. FAKE_DIR gets one pid file per process."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

if sys.argv[1:2] in (["--version"], ["-v"]):
    print("2.1.310 (Claude Code)")
    raise SystemExit(0)

fake_dir = Path(os.environ["FAKE_DIR"])
fake_dir.mkdir(parents=True, exist_ok=True)
SLEEP = "import time; time.sleep(3600)"

GRANDCHILD = f"""import json, os, subprocess, sys, time
from pathlib import Path
d = Path(os.environ['FAKE_DIR'])
(d / f'grandchild-{{os.getpid()}}.pid').write_text(str(os.getpid()))
result = {{}}
if os.name == 'nt':
    for name, flag in {{'breakaway': 0x01000000, 'detached': 0x00000008 | 0x00000200}}.items():
        try:
            p = subprocess.Popen([sys.executable, '-c', {SLEEP!r}], creationflags=flag, stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            (d / f'great-{{name}}-{{p.pid}}.pid').write_text(str(p.pid))
            result[name] = p.pid
        except OSError as error:
            result[name] = f'refused: {{error}}'
(d / 'escape-result.json').write_text(json.dumps(result))
time.sleep(3600)
"""
CHILD = f"""import os, subprocess, sys, time
from pathlib import Path
d = Path(os.environ['FAKE_DIR'])
(d / f'child-{{os.getpid()}}.pid').write_text(str(os.getpid()))
subprocess.Popen([sys.executable, '-c', {GRANDCHILD!r}])
time.sleep(3600)
"""
child = subprocess.Popen([sys.executable, "-c", CHILD], stdin=subprocess.DEVNULL)
(fake_dir / f"cli-{os.getpid()}.pid").write_text(str(os.getpid()))
print(json.dumps({"type": "system", "subtype": "init", "session_id": "fake-session", "model": "fake",
                  "mcp_servers": [], "tools": []}), flush=True)
time.sleep(3600)
