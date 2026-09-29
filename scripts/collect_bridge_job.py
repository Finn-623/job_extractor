"""N8.1 collect bridge job runner — stdin JSON in, single JSON line out.

Usage: python scripts/collect_bridge_job.py auto|curl|har
stdin: {"url": "...", "curl": "...", "har_path": "..."}
stdout: {"ok": ..., "code": ..., ...}
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from job_extractor.collect_bridge import main

if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
