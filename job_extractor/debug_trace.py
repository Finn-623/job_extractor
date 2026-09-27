"""Single gate for internal discovery diagnostics (SCOPE_TRACE / TIMING_TRACE ...).

Default user runs must never print internal diagnostics: every internal trace
line goes through :func:`emit`, which is a no-op unless tracing is explicitly
enabled for one run with ``JOB_EXTRACTOR_TRACE=1`` (``STEP95G_TIMING=1``
keeps working as before and also enables the traces).

When tracing is enabled and a ProgressReporter is active, lines are handed to
the reporter so a TTY board is paused, the diagnostic is printed as a permanent
line below it, and the board resumes — trace text can never be overwritten
mid-line by a frame refresh.
"""
from __future__ import annotations

import json
import os
import threading
from typing import Any, Callable

_ENV_VARS = ("JOB_EXTRACTOR_TRACE", "STEP95G_TIMING")
_LOCK = threading.Lock()
_SINK: Callable[[str], None] | None = None


def enabled() -> bool:
    return any(os.getenv(name) == "1" for name in _ENV_VARS)


def set_sink(sink: Callable[[str], None] | None) -> None:
    """Register the terminal-aware sink (None -> plain stdout print)."""
    global _SINK
    with _LOCK:
        _SINK = sink


def emit(prefix: str, payload: Any) -> None:
    """Emit one diagnostic line; suppressed entirely in default runs."""
    if not enabled():
        return
    line = f"{prefix} {json.dumps(payload, ensure_ascii=False, default=str)}"
    with _LOCK:
        sink = _SINK
        if sink is not None:
            sink(line)
            return
    print(line, flush=True)
