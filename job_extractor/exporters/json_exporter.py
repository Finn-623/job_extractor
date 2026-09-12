from pathlib import Path
from job_extractor.models import CollectionResult
def export_collection_result(result: CollectionResult, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
