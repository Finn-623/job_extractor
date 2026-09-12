from __future__ import annotations
from typing import Any
from urllib.parse import parse_qsl,urlsplit,urlunsplit

SECRET_WORDS=("token","authorization","cookie","csrf","signature","secret","session","key")
def ephemeral(key:str)->bool:
    value=key.lower().replace("-","_")
    compact=value.replace("_","")
    return any(marker in compact for marker in ("trackid","trackingid","traceid","requestid"))
def sensitive(key: str) -> bool:
    value=key.lower().replace("-","_")
    return value in SECRET_WORDS or any(value==x or value.startswith(x+"_") or value.endswith("_"+x) for x in SECRET_WORDS)
def safe_url(url: str) -> tuple[str,dict[str,str]]:
    p=urlsplit(url); query={k:("[REDACTED]" if sensitive(k) else v) for k,v in parse_qsl(p.query,keep_blank_values=True) if not ephemeral(k)}
    return urlunsplit((p.scheme,p.netloc,p.path,"","")),query
def type_name(value: Any) -> str:
    if value is None:return "null"
    if isinstance(value,bool):return "bool"
    if isinstance(value,int):return "int"
    if isinstance(value,float):return "float"
    if isinstance(value,str):return "string"
    if isinstance(value,list):return "array"
    if isinstance(value,dict):return "object"
    return type(value).__name__
def request_shape(value: Any) -> dict[str,Any]:
    if not isinstance(value,dict): return {}
    return {k:("[REDACTED]" if sensitive(k) else type_name(v)) for k,v in value.items()}
def sanitized_values(value: Any) -> dict[str,Any]:
    if not isinstance(value,dict): return {}
    def clean(item:Any)->Any:
        if isinstance(item,dict):
            return {k:("[REDACTED]" if sensitive(str(k)) else clean(v)) for k,v in item.items()}
        if isinstance(item,list):return [clean(x) for x in item]
        if isinstance(item,(str,int,float,bool,type(None))):return item
        return None
    return clean(value)
def safe_business_data(value:Any)->Any:
    if isinstance(value,dict):return {k:safe_business_data(v) for k,v in value.items() if not sensitive(str(k))}
    if isinstance(value,list):return [safe_business_data(v) for v in value]
    return value

def find_arrays(value: Any,max_depth: int=4,path: str="",depth: int=0) -> list[tuple[str,list[Any]]]:
    if depth>max_depth:return []
    found=[]
    if isinstance(value,list): found.append((path or "$",value[:20]))
    elif isinstance(value,dict):
        for k,v in list(value.items())[:100]: found.extend(find_arrays(v,max_depth,f"{path}.{k}" if path else k,depth+1))
    return found
def find_array_info(value:Any,max_depth:int=4,path:str="",depth:int=0)->list[tuple[str,int,list[Any]]]:
    if depth>max_depth:return []
    if isinstance(value,list):return [(path or "$",len(value),value[:20])]
    found=[]
    if isinstance(value,dict):
        for k,v in list(value.items())[:100]:found.extend(find_array_info(v,max_depth,f"{path}.{k}" if path else k,depth+1))
    return found
def find_field(value: Any,names: set[str],max_depth: int=4,depth: int=0,prefix: str="") -> str|None:
    if depth>max_depth or not isinstance(value,dict):return None
    for k,v in list(value.items())[:100]:
        path=f"{prefix}.{k}" if prefix else k
        if k.lower() in names:return path
        found=find_field(v,names,max_depth,depth+1,path)
        if found:return found
    return None
def response_shape(value: Any) -> dict[str,Any]:
    if not isinstance(value,(dict,list)):return {}
    path,length,items=list_observation(value); fields=sorted({str(k) for x in items if isinstance(x,dict) for k in list(x)[:100]})[:100]
    return {"top_level_keys":sorted(value.keys())[:100] if isinstance(value,dict) else [],
            "candidate_list_path":path,"sample_field_names":fields,"array_length":length,"sampled_items":len(items),
            "total_field":find_field(value,{"total","count","totalcount","total_count"})}
def get_path(value:Any,path:str|None)->Any:
    if not path:return None
    for part in (path or "").split("."):
        if not part or part=="$":continue
        if not isinstance(value,dict):return None
        value=value.get(part)
    return value
def list_observation(value:Any)->tuple[str|None,int,list[Any]]:
    arrays=find_array_info(value,max_depth=7)
    if not arrays:return None,0,[]
    path,length,sample=max(arrays,key=lambda x:x[1]);return path,length,sample
