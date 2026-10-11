"""Crash-safe, resumable-in-future checkpoints for the Manual cURL JD stage."""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4


class CheckpointWriter:
    def __init__(self, root: Path, source_url: str, resumed_from: str | None=None) -> None:
        run_id=datetime.now().strftime("%Y-%m-%d_%H%M%S")+"_"+uuid4().hex[:8]
        self.directory=root / "_checkpoints" / run_id
        self.path=self.directory / "jd_checkpoint.json"
        self.source_url=source_url;self.resumed_from=resumed_from
        self.updated_at: str | None=None

    def save(self, jobs: list[Any], *, stage: str, task_states: dict[str, str] | None=None) -> Path:
        """Atomically replace only this run's checkpoint after complete serialization."""
        self.directory.mkdir(parents=True, exist_ok=True)
        self.updated_at=datetime.now().isoformat(timespec="seconds")
        payload={"format":"job-extractor-jd-checkpoint-v1","updated_at":self.updated_at,
                 "source_url":self.source_url,"stage":stage,
                 "jobs":[job.model_dump(mode="json") for job in jobs],
                 "task_states":task_states or {},"resumed_from":self.resumed_from}
        fd, temporary=tempfile.mkstemp(prefix=".jd_checkpoint_",suffix=".tmp",dir=self.directory)
        try:
            with os.fdopen(fd,"w",encoding="utf-8") as handle:
                json.dump(payload,handle,ensure_ascii=False,indent=2)
                handle.flush();os.fsync(handle.fileno())
            os.replace(temporary,self.path)
        except BaseException:
            try:os.unlink(temporary)
            except FileNotFoundError:pass
            raise
        return self.path
