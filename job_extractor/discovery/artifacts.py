from __future__ import annotations
from datetime import datetime,timezone
from pathlib import Path
from urllib.parse import parse_qsl,urlencode,urlsplit,urlunsplit
from job_extractor import __version__
from job_extractor.discovery.models import DiscoveryResult

def normalized_source_url(url:str)->str:
    p=urlsplit(url);query=urlencode(sorted(parse_qsl(p.query,keep_blank_values=True)))
    path=p.path.rstrip("/") or "/"
    return urlunsplit((p.scheme.lower(),p.netloc.lower(),path,query,""))
def load_reusable_discovery(url:str,directory:Path,max_age_seconds:int=86400,now:datetime|None=None)->tuple[DiscoveryResult,Path]|None:
    now=now or datetime.now(timezone.utc);target=normalized_source_url(url)
    candidates=[]
    for path in directory.glob("*_discovery.json"):
        try:
            result=DiscoveryResult.model_validate_json(path.read_text(encoding="utf-8"))
            finished=result.finished_at or result.started_at
            if finished.tzinfo is None:finished=finished.replace(tzinfo=datetime.now().astimezone().tzinfo)
            age=(now-finished.astimezone(timezone.utc)).total_seconds()
            if normalized_source_url(result.source_url)==target and result.tool_version==__version__ and 0<=age<=max_age_seconds:candidates.append((finished,path,result))
        except Exception:continue
    if not candidates:return None
    _,path,result=max(candidates,key=lambda x:x[0]);return result,path
