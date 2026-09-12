from __future__ import annotations
import html,re
from typing import Any
from job_extractor.models import Job

EXCEL_CELL_LIMIT=32767
def locations_text(values:list[str])->str:return "；".join(x for x in values if x)
def numbered(values:list[str])->str:return "\n".join(f"{i}. {value}" for i,value in enumerate(values,1))
def excel_text(value:Any)->str:
    text="" if value is None else str(value)
    if text.startswith(("=","+","-","@")):text="'"+text
    return text[:EXCEL_CELL_LIMIT]
def truncate_excel(value:Any)->tuple[str,bool]:
    text="" if value is None else str(value)
    if len(text)<=EXCEL_CELL_LIMIT:return excel_text(text),False
    marker="\n[EXCEL_CELL_TRUNCATED — full content is available in jobs.json]"
    return excel_text(text[:EXCEL_CELL_LIMIT-len(marker)]+marker),True
def job_completeness(job:Job)->str:
    if not job.full_jd:return "MISSING_JD"
    if job.responsibilities and job.requirements:return "COMPLETE"
    return "STRUCTURE_PARTIAL"
def markdown_inline(value:Any)->str:
    text="" if value is None else str(value)
    text=html.escape(text,quote=False).replace("\\","\\\\")
    return re.sub(r"([|#*_`])",r"\\\1",text).replace("\n"," ")
def scope_text(scope:dict[str,Any])->str:
    return "; ".join(f"{k}={v}" for k,v in scope.items()) or "Unknown"
