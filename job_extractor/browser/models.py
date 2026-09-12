from dataclasses import dataclass
from typing import Any

@dataclass(frozen=True)
class CapturedResponse:
    url: str
    method: str
    post_data: str | None
    status: int
    json: dict[str, Any]
