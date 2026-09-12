from pathlib import Path
DATA_DIR = Path.home() / ".job_extractor"
OUTPUT_DIR = DATA_DIR / "output"
DISCOVERY_OUTPUT_DIR = OUTPUT_DIR / "discovery"

def ensure_directories() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    DISCOVERY_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
