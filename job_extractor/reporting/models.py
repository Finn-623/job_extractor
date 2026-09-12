from pathlib import Path
from pydantic import BaseModel

class ReportArtifacts(BaseModel):
    output_directory:Path
    json_path:Path
    excel_path:Path
    markdown_path:Path
