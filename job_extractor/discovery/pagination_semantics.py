from __future__ import annotations
from typing import Any

INFERENCE_SOURCE="request_response_schema"
PAGE_NAMES={"page","pageno","pagenum","pageindex","pagenumber","currentpage","current","currentpageindex"}
OFFSET_NAMES={"offset","start","startindex","from","fromindex","skip"}
SIZE_NAMES={"pagesize","size","limit","pagelimit","rows","perpage"}
TOTAL_TIER1={"total","totalcount","total_count","totalsize","totalelements","total_elements","recordstotal","records_total","datacount"}
TOTAL_TIER2={"count"}
_MAX_DEPTH=4
_MAX_KEYS=100

def _compact(key:str)->str:
    return str(key).lower().replace("_","").replace("-","").strip()

def _as_int(value:Any)->int|None:
    if isinstance(value,bool):return None
    if isinstance(value,int):return value
    if isinstance(value,float) and float(value).is_integer():return int(value)
    if isinstance(value,str) and value.strip().isdigit():return int(value.strip())
    return None

def iter_fields(value:Any,max_depth:int=_MAX_DEPTH,depth:int=0,path:str=""):
    """Pre-order bounded traversal of dict fields; yields (path, key, value). Lists are not descended."""
    if depth>max_depth or not isinstance(value,dict):return
    for index,(key,item) in enumerate(list(value.items())[:_MAX_KEYS]):
        child_path=f"{path}.{key}" if path else str(key)
        yield child_path,key,item,index,depth
        yield from iter_fields(item,max_depth,depth+1,child_path)

def find_total(response:Any,list_length:int|None=None)->tuple[str|None,int|None]:
    """Locate a total field by semantic name, preferring explicit total names over ambiguous 'count'."""
    for names in (TOTAL_TIER1,TOTAL_TIER2):
        for item_path,key,item,_index,_depth in iter_fields(response):
            if _compact(key) in names and _as_int(item) is not None:
                number=int(item)
                if isinstance(list_length,int) and list_length>0 and number<list_length:
                    continue
                return item_path,number
    return None,None

def infer_pagination_from_schema(request_body:dict[str,Any]|None,query:dict[str,str|None]|None,response:Any,list_length:int|None=None)->dict[str,Any]:
    """Infer PAGE/OFFSET pagination from request+response schema alone (first-page capable, generic)."""
    evidence:list[str]=[]
    # Total credibility needs the observed job-list length.  When the caller
    # did not supply one, derive it from the response with the same generic
    # array metric response_shape uses, so both total selectors stay
    # consistent for stale/invalid low totals beside a credible Count.
    if list_length is None:
        from job_extractor.discovery.network_analyzer import find_array_info
        arrays=find_array_info(response)
        if arrays:list_length=max(arrays,key=lambda x:x[1])[1]
    advancing:dict[str,list[tuple[int,int,str,str,Any]]]={"page":[],"offset":[]}
    sizes:list[tuple[int,int,str,str,Any]]=[]
    for origin,source in (("request.body",request_body if isinstance(request_body,dict) else {}),("request.query",query if isinstance(query,dict) else {})):
        if not source:continue
        for path,key,item,index,depth in iter_fields(source):
            name=_compact(key);number=_as_int(item)
            if number is None or number<0:continue
            evidence.append(f"{origin}.{path}={number}")
            if name in PAGE_NAMES:advancing["page"].append((depth,index,path,key,number))
            elif name in OFFSET_NAMES:advancing["offset"].append((depth,index,path,key,number))
            if name in SIZE_NAMES and number>0:sizes.append((depth,index,path,key,number))
    page_hits=sorted(advancing["page"],key=lambda x:(x[0],x[1]));offset_hits=sorted(advancing["offset"],key=lambda x:(x[0],x[1]))
    advancing_all=sorted(page_hits+offset_hits,key=lambda x:(x[0],x[1]))
    sizes.sort(key=lambda x:(x[0],x[1]))
    if advancing_all and sizes:
        depth,index,path,key,number=advancing_all[0]
        kind="PAGE" if any(page_hit[2]==path for page_hit in page_hits) else "OFFSET"
        total_path,total_value=find_total(response,list_length)
        if total_path is not None:evidence.append(f"response.{total_path}={total_value}")
        else:evidence.append("no response total field found")
        return {"kind":kind,"page_param":key,"size_param":sizes[0][3],
                "first_page":number if (kind=="PAGE" and number in (0,1)) or (kind=="OFFSET" and number==0) else None,
                "page_size":sizes[0][4],"total_path":total_path,"total_value":total_value,
                "inference_source":INFERENCE_SOURCE,
                "confidence":0.94 if total_value is not None else 0.8,"evidence":evidence}
    if advancing_all:evidence.append("no page-size field found in request; pagination model not executable")
    else:evidence.append("no advancing pagination field (page-number or offset) found in request")
    return {"kind":None,"page_param":None,"size_param":None,"first_page":None,"page_size":None,
            "total_path":None,"total_value":None,"confidence":0.0,"evidence":evidence}
