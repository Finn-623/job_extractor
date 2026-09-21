"""Opt-in, secret-safe persistence for STEP94H real-CLI diagnostics."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

DEBUG_PATH = Path("/tmp/job_extractor_step94h_debug.log")
_ALLOWED = {"phase", "title", "post_id", "candidate_url", "exception_class",
            "timeout_layer", "failure_code", "failure_reason", "elapsed",
            "debug_enabled", "final_page_url", "ready_state", "body_text_length",
            "body_text_preview", "root_text_length", "document_title",
            "console_error_count", "failed_request_count", "failed_requests",
            "browser_executable", "headless"}


def enabled() -> bool:
    return os.getenv("STEP94H_DEBUG") == "1"


def initialize() -> None:
    """Overwrite stale output as soon as the real CLI starts."""
    if not enabled():
        return
    with DEBUG_PATH.open("w", encoding="utf-8") as handle:
        handle.write("debug_enabled=true\n")
        handle.flush()
        os.fsync(handle.fileno())


def append(**values: Any) -> None:
    """Append one allow-listed record synchronously; never write request data."""
    if not enabled():
        return
    record = {key: value for key, value in values.items() if key in _ALLOWED and value is not None}
    with DEBUG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
