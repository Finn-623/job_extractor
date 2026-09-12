from __future__ import annotations
from collections import Counter
from typing import Any
from job_extractor.discovery.network_analyzer import find_array_info,find_field,response_shape

TITLE={"title","jobtitle","job_title","jobname","positionname","position_name","projectpositionname","name"}
IDS={"id","jobid","job_id","jobcode","postid","post_id","postingid","posting_id","positionid","position_id","positioncode","requisitionid","requisition_id"}
LOCATION={"location","locations","city","cityname","city_name","country","workplacecode"}
CATEGORY={"department","team","category","jobcategory","job_category","function","recruitcategoryname"}
LINK={"url","absolute_url","job_url","joburl","detail_url","detailurl","apply_url","applyurl","path","slug"}
JD={"description","overview","responsibility","responsibilities","requirement","requirements","qualification","qualifications","jobdescription","job_description"}

def _fields(item:Any)->set[str]:return {str(k).lower() for k in item} if isinstance(item,dict) else set()
def _job_prefixed(keys:set[str])->bool:
    return any(("job" in k or "position" in k or "requisition" in k or "posting" in k or k.endswith("postcode") or k=="postcode") for k in keys)
def _array_metrics(items:list[Any])->tuple[float,float,set[str]]:
    records=[x for x in items[:20] if isinstance(x,dict)]
    if records and all(isinstance(x.get("node"),dict) for x in records):records=[x["node"] for x in records]
    if not records:return 0.0,0.0,set()
    key_sets=[_fields(x) for x in records];modal=set(Counter(tuple(sorted(x)) for x in key_sets).most_common(1)[0][0])
    homogeneity=sum(len(keys&modal)/max(1,len(keys|modal)) for keys in key_sets)/len(key_sets)
    density=sum(bool(keys&TITLE and keys&IDS and (keys&(LOCATION|CATEGORY|LINK|JD) or len(keys)>=4 or _job_prefixed(keys))) for keys in key_sets)/len(key_sets)
    return round(homogeneity,4),round(density,4),set().union(*key_sets)

def _negative_reasons(url:str,payload:Any,homogeneity:float,density:float,fields:set[str])->list[str]:
    low=url.lower();top={str(k).lower() for k in payload} if isinstance(payload,dict) else set();reasons=[]
    if any(term in low for term in ("news", "announcement", "article")) or fields & {"body", "tag", "createtime", "createdtime"} and "tag" in fields:reasons.append("CONTENT_PAYLOAD")
    if any(x in low for x in ("global-header","global_header","/navigation","/nav/","/menu","flyout","/header","/footer")) or top&{"navigation","nav","menu","header","footer","flyouts"}:reasons.append("NAVIGATION_PAYLOAD")
    if any(x in low for x in ("config","settings","setting")) or top&{"config","configuration","settings"}:reasons.append("CONFIG_PAYLOAD")
    if any(x in low for x in ("facet","facetvalues","/options/")):reasons.append("FACET_PAYLOAD")
    if "widget" in low:reasons.append("WIDGET_PAYLOAD")
    if any(x in low for x in ("locale","translation","language","analytics","tracking","collect","telemetry")) and density<0.8:reasons.append("CONFIG_PAYLOAD")
    job_prefixed=any(("job" in k or "position" in k or "requisition" in k or "posting" in k or k=="postcode" or k.endswith("postcode")) for k in fields)
    job_meta=fields&{"department","department_id","deptid","team","location","locations","city","cityname","city_info","worklocation","requirement","requirements","responsibility","responsibilities","jobdescription","description","recruittype","employment","job_function","job_category"}
    content_semantic=fields&{"content","contentshort","summary","subtitle","urlshort","categoryfullname","categorycode","categoryname","contenttype","story","banner","cooperation"}
    if content_semantic and not job_prefixed and not job_meta:reasons.append("CONTENT_LIST")
    if any(x in low for x in ("/cooperation","cooperationinfo","/banner","/story","/news","/article")):reasons.append("NON_JOB_SEMANTIC_SOURCE")
    dict_semantic=fields&{"pcpath","mobilepath","seasontype","displayorder","orderval","hotval","parentid"}
    region_semantic=fields&{"regioncode","regionname","provincename","provinceid","citycode","cityid","hotcity"}
    if not job_prefixed and not (fields&{"requirement","jobdescription","positionname","jobname","title"}):
        if dict_semantic or region_semantic or any(x in low for x in ("/region/","selectallvalidregions","/province/","/citylist","/arealist")):reasons.append("REGION_LIST")
    if homogeneity<0.55 and len(fields)>=4:reasons.append("MIXED_CONTENT_PAYLOAD")
    if density<0.5:reasons.append("LOW_JOB_ENTITY_DENSITY")
    return list(dict.fromkeys(reasons))

def score_list(url:str,payload:Any)->tuple[int,list[str],dict[str,Any]]:
    low=url.lower();score=0;evidence=[]
    arrays=find_array_info(payload,max_depth=7)
    def rank_array(entry):
        path,length,items=entry;homogeneity,density,fields=_array_metrics(items)
        return density, bool(fields&TITLE and fields&IDS), homogeneity, min(length,100)
    path,length,items=max(arrays,key=rank_array,default=(None,0,[]))
    homogeneity,density,fields=_array_metrics(items);shape=response_shape(payload)
    records=[x.get("node") if isinstance(x,dict) and isinstance(x.get("node"),dict) else x for x in items]
    shape.update({"candidate_list_path":path,"array_length":length,"sample_field_names":sorted({str(k) for x in records if isinstance(x,dict) for k in x})[:100],"sampled_items":len(items)})
    shape["inferred_job_id_field"] = next((key for key in ("id","jobId","positionId","requisitionId") if key.lower() in fields),None)
    shape["inferred_job_title_field"] = next((key for key in ("title","jobTitle","positionName","projectPositionName") if key.lower() in fields),None)
    reasons=_negative_reasons(url,payload,homogeneity,density,fields)
    if "job" in low:score+=2;evidence.append("URL contains job/jobs")
    if "position" in low:score+=2;evidence.append("URL contains position")
    if "search" in low or "list" in low:score+=1;evidence.append("URL contains search/list")
    if fields&TITLE:score+=3;evidence.append("records contain a title field")
    if fields&IDS:score+=3;evidence.append("records contain a stable id field")
    if fields&LOCATION:score+=2;evidence.append("records contain location/city")
    if fields&CATEGORY:score+=1;evidence.append("records contain department/category")
    if fields&LINK:score+=2;evidence.append("records contain detail/apply links")
    if fields&JD:score+=3;evidence.append("records contain JD fields")
    if fields&(LOCATION|CATEGORY) and fields&TITLE and fields&IDS:
        score+=2;evidence.append("records combine stable ID, title, and job metadata")
    if homogeneity>=0.75:score+=2;evidence.append(f"homogeneous record structure: {homogeneity:.2f}")
    if density>=0.8:score+=4;evidence.append(f"high job entity density: {density:.2f}")
    observed=length
    if observed>=5:score+=3;evidence.append(f"response contains {observed} records")
    total=find_field(payload,{"total","count","totalcount","total_count"})
    if total:score+=2;evidence.append(f"response has total field: {total}")
    if not items and isinstance(payload,dict):score-=3;evidence.append("no list-shaped response")
    if reasons:score=min(score,9);evidence.extend(f"rejected: {x}" for x in reasons)
    shape.update({"homogeneity_score":homogeneity,"job_entity_density":density,"rejection_reasons":reasons})
    return score,evidence,shape

def score_detail(url:str,payload:Any)->tuple[int,list[str]]:
    low=url.lower();evidence=[];score=0
    if any(x in low for x in ("/locales/","/translations/","/config/")):score-=8;evidence.append("localization/config response")
    if any(x in low for x in ("/detail","/job/","/jobs/","/position/","/post/")):score+=4;evidence.append("detail-like URL")
    fields=set()
    def walk(v,depth=0):
        if depth>4:return
        if isinstance(v,dict):
            fields.update(str(k).lower() for k in v)
            for x in list(v.values())[:100]:walk(x,depth+1)
    walk(payload)
    if fields&JD:score+=7;evidence.append("response contains description/requirement fields")
    if fields&TITLE:score+=3;evidence.append("response contains title")
    if fields&IDS:score+=2;evidence.append("response contains job id")
    return score,evidence
def confidence(score:int)->str:return "HIGH" if score>=15 else "MEDIUM" if score>=10 else "LOW"
