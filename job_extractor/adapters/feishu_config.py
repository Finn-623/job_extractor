from dataclasses import dataclass

@dataclass(frozen=True)
class FeishuScope:
    website_id: str
    website_path: str
    process_type: int
    recruitment_type: str
    company: str | None = None
