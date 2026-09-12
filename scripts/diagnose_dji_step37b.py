"""Run terminal-only forensic observability for the DJI trusted terminal."""
from __future__ import annotations

import argparse
from pathlib import Path

from job_extractor.adapters.generic import GenericAdapter


TERMINAL = "https://apply.careers.dji.com/social-recruitment/dji/170070?hash=%23%2Fjobs"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = GenericAdapter().discover_terminal(TERMINAL)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    trace = result.terminal_trace
    print({
        "terminal": TERMINAL,
        "status": result.status,
        "trace": bool(trace),
        "actions": len(trace.terminal_actions_attempted) if trace else 0,
        "network": len(trace.network) if trace else 0,
        "dom_phases": len(trace.dom_evidence) if trace else 0,
        "candidate_lifecycle": len(trace.candidate_lifecycle) if trace else 0,
        "output": str(args.output),
    })


if __name__ == "__main__":
    main()