from __future__ import annotations
import hashlib,json,re
from typing import Any
from urllib.parse import parse_qsl,urlsplit,urlunsplit,urlencode
from job_extractor.field_semantics import infer_field

REQUISITION_FIELDS=("requisitionid","requisition_id","reqid","req_id","jobpostingid","job_posting_id")
JOB_FIELDS=("jobid","job_id","postingid","posting_id","postid","post_id")
STABLE_FIELDS=("id","positionid","position_id","internal_job_id")

def _find(raw:Any,names:tuple[str,...],depth:int=0):
    if depth>3 or not isinstance(raw,dict):return None,None
    lookup={str(k).lower():k for k in raw}
    for name in names:
        key=lookup.get(name)
        if key is not None and raw[key] not in (None,""):return str(raw[key]),str(key)
    semantic_role="id" if names==STABLE_FIELDS else None
    inferred=infer_field(raw.keys(),semantic_role) if semantic_role else None
    if inferred is not None and raw[inferred] not in (None,""):return str(raw[inferred]),str(inferred)
    for value in raw.values():
        found=_find(value,names,depth+1)
        if found[0] is not None:return found
    return None,None

def canonical_url(url:str|None)->str|None:
    if not url:return None
    parsed=urlsplit(url);safe_query=[(k,v) for k,v in parse_qsl(parsed.query,keep_blank_values=True) if not re.search(r"(?i)(utm_|tracking|source|campaign|ref$)",k)]
    return urlunsplit((parsed.scheme.lower(),parsed.netloc.lower(),parsed.path.rstrip("/"),urlencode(safe_query),""))

def _url_identifier(url:str|None):
    if not url:return None,None
    parsed=urlsplit(url)
    for key,value in parse_qsl(parsed.query):
        if key.lower() in REQUISITION_FIELDS+JOB_FIELDS+STABLE_FIELDS and value:return value,f"query:{key}"
    segments=[x for x in parsed.path.split("/") if x]
    for segment in segments:
        if re.fullmatch(r"[0-9a-fA-F]{8}-[0-9a-fA-F-]{27,}",segment):return segment.lower(),"detail_url_uuid"
        if re.fullmatch(r"(?=.*\d)[A-Za-z0-9-]{5,}",segment) and (sum(c.isdigit() for c in segment)>=5):return segment,"detail_url_path_id"
    return None,None

def job_identity(raw:dict[str,Any]|None=None,detail_url:str|None=None,title:str|None=None,location:Any=None,department:str|None=None,company:str|None=None)->dict[str,str]:
    raw=raw or {}
    for fields,source in ((REQUISITION_FIELDS,"REQUISITION_ID"),(JOB_FIELDS,"JOB_POSTING_ID"),(STABLE_FIELDS,"STABLE_API_ID")):
        value,field=_find(raw,fields)
        if value is not None:return {"identity_source":source,"identity_value":value,"identity_field":field or ""}
    value,source=_url_identifier(detail_url)
    if value:return {"identity_source":"STRUCTURED_DETAIL_ID" if source.startswith("query:") else "CANONICAL_URL_ID","identity_value":value,"identity_field":source}
    canonical=canonical_url(detail_url)
    stable={"url":canonical,"title":title or "","location":location or "","department":department or "","company":company or ""}
    digest=hashlib.sha256(json.dumps(stable,sort_keys=True,default=str,ensure_ascii=False).encode()).hexdigest()[:24]
    return {"identity_source":"COMPOSITE_FINGERPRINT","identity_value":digest,"identity_field":"url+title+location+department+company"}
