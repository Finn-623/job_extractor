from __future__ import annotations

import hashlib
from urllib.parse import urlsplit

from job_extractor.discovery.models import ATSFingerprint, ATSProfile, DiscoveryResult


def profile_from_discovery(result: DiscoveryResult) -> ATSProfile|None:
    candidate=result.probable_list_api
    if candidate is None:
        return None
    fields=[str(x) for x in candidate.response_shape.get("sample_field_names", [])]
    ids=[x for x in fields if x.lower() in {"id","job_id","jobid","jobpostid","positionid","requisitionid","requisition_id"}]
    titles=[x for x in fields if x.lower() in {"title","name","jobtitle","positionname","projectpositionname","job_title","position_name"}]
    if not ids or not titles or candidate.job_entity_density < 0.5:
        return None
    host=urlsplit(candidate.url).hostname or ""
    fingerprint=ATSFingerprint(host=host,api_route_patterns=[urlsplit(candidate.url).path],http_methods=[candidate.method],list_response_shape={k:v for k,v in candidate.response_shape.items() if k != "sample_field_names"},job_id_fields=ids,title_fields=titles,pagination_model=result.detected_pagination.pagination_type,scope_model=result.detected_scope,detail_url_pattern=candidate.detail_url_field,public_identifiers={k:v for k,v in result.detected_scope.items() if "token" not in k.lower() and "key" not in k.lower()})
    digest=hashlib.sha256(f"{host}|{candidate.method}|{candidate.response_shape.get('candidate_list_path')}|{','.join(ids)}|{','.join(titles)}".encode()).hexdigest()[:16]
    confidence="HIGH" if candidate.confidence=="HIGH" and candidate.observed_unique_ids else "MEDIUM"
    return ATSProfile(profile_id=f"generic-{digest}",confidence=confidence,fingerprint=fingerprint,list_endpoint=candidate.url,list_method=candidate.method,list_path=candidate.response_shape.get("candidate_list_path"),pagination=result.detected_pagination.model_dump(),detail={"url_field":candidate.detail_url_field},scope=result.detected_scope,evidence=candidate.evidence)