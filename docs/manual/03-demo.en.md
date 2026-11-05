[← Manual index](../manual.en.md)

## Tour: the mock demo

Runs without API keys or a cluster.

```bash
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\labhq demo --web
```

On POSIX use `python3 -m venv .venv`, `./.venv/bin/python -m pip install -e ".[dev]"` and `./labhq.sh demo --web`. Add `--phone` for the phone.
Even if a real office is running, the demo picks a different free port. Open only the printed demo address; you will see a `Demo (mock)` mark and a notice band.
A web request runs over about 15–30 seconds and shows the time left until the next request. Time spent waiting for phone approval is separate and is not mixed into the real decision record.
To see only the UI without a gateway, run `python -m http.server --directory labhq/web 8000`, then open `http://127.0.0.1:8000/?demo=1`.
