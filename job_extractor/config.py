from pathlib import Path
ROOT_DIR = Path(__file__).resolve().parent.parent
OUTPUT_ROOT = ROOT_DIR / "output"
DATA_DIR = Path.home() / ".job_extractor"
OUTPUT_DIR = DATA_DIR / "output"
DISCOVERY_OUTPUT_DIR = OUTPUT_DIR / "discovery"

def ensure_directories() -> None:
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    DISCOVERY_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
