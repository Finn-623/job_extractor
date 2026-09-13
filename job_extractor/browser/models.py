from dataclasses import dataclass
from typing import Any

@dataclass(frozen=True)
class CapturedResponse:
    url: str
    method: str
    post_data: str | None
    status: int
    json: dict[str, Any]
    request_url: str | None = None  # full wire URL including query (memory only)
    request_headers: dict[str, str] | None = None  # observed wire headers (memory only)
