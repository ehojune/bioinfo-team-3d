"""A stand-in for the `time` (or `asyncio`) name that ONE module sees.

`monkeypatch.setattr(module.time, "time", fake)` changes the `time` function of the whole process, because `module.time`
is the standard `time` module: every other thread and every event loop that is alive while the test runs reads the fake
clock too (#1076). Replace the module's own name instead:

    monkeypatch.setattr(module, "time", RuntimeProxy(module.time, time=lambda: 100.0))

Whatever the proxy does not override is read from the real object.
"""


class RuntimeProxy:
    """Override a consumer's runtime view without mutating host stdlib objects."""

    def __init__(self, original, **overrides):
        self.__dict__.update(overrides)
        self._original = original

    def __getattr__(self, name):
        return getattr(self._original, name)
