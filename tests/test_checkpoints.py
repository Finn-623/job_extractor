import json

import httpx
import pytest

from job_extractor.checkpoints import CheckpointWriter
from job_extractor.manual_curl import resume_manual_checkpoint, run_manual_curl
from job_extractor.models import Job


LIST="curl 'https://checkpoint.test/jobs'"
DETAIL="curl 'https://checkpoint.test/detail?id=demo'"


def _job(identifier: str, jd: str | None=None) -> Job:
    return Job(job_id=identifier,job_title=f"岗位{identifier}",source_url="https://checkpoint.test",full_jd=jd)


def test_checkpoint_is_atomic_when_replace_fails(tmp_path,monkeypatch):
    writer=CheckpointWriter(tmp_path,"https://checkpoint.test")
    path=writer.save([_job("1","已保存 JD")],stage="JD_IN_PROGRESS")
    before=path.read_text(encoding="utf-8")
    monkeypatch.setattr("job_extractor.checkpoints.os.replace",lambda *_a:(_ for _ in ()).throw(OSError("disk failure")))
    with pytest.raises(OSError):writer.save([_job("1","新 JD")],stage="JD_COMPLETE")
    assert path.read_text(encoding="utf-8")==before


def test_partial_detail_checkpoint_keeps_all_list_jobs_and_successful_jds(tmp_path):
    rows=[{"id":str(i),"jobName":f"岗位{i}"} for i in range(10)]
    def handler(request):
        if request.url.path=="/jobs":return httpx.Response(200,json={"data":rows,"page":{"totalCount":10}})
        identifier=request.url.params["id"]
        if int(identifier)<4:
            return httpx.Response(200,json={"data":{"positionInfoList":[{"jobDuty":"负责系统设计与交付，推动跨团队协作。","jobRequirements":"本科及以上，具备工程实践能力。"}]}})
        return httpx.Response(500)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result=run_manual_curl(LIST,DETAIL,max_jobs=None,client=client,checkpoint_root=tmp_path)
    path=next(tmp_path.glob("_checkpoints/*/jd_checkpoint.json"))
    payload=json.loads(path.read_text(encoding="utf-8"))
    assert len(payload["jobs"])==10
    assert sum(bool(job["full_jd"]) for job in payload["jobs"])==4
    assert result.failed_details==6 and payload["stage"]=="JD_PARTIAL"


def test_resume_retries_only_incomplete_jobs_and_creates_a_new_checkpoint(tmp_path):
    prior="岗位职责：负责系统设计与交付，推动跨团队协作与质量改进。\n任职要求：本科及以上，具备工程实践能力与沟通能力。"
    jobs=[_job(str(i),prior if i<4 else None) for i in range(10)]
    original=CheckpointWriter(tmp_path,"https://checkpoint.test")
    source=original.save(jobs,stage="JD_PARTIAL",task_states={str(i):"SKIPPED" for i in range(4,10)})
    requested=[]
    def handler(request):
        requested.append(request.url.params["id"])
        return httpx.Response(200,json={"data":{"positionInfoList":[{"jobDuty":"负责系统设计与交付，推动跨团队协作。","jobRequirements":"本科及以上，具备工程实践能力。"}]}})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result=resume_manual_checkpoint(source,DETAIL,client=client,checkpoint_root=tmp_path)
    assert requested==[str(i) for i in range(4,10)]
    assert len(result.jobs)==10 and all(job.full_jd for job in result.jobs)
    checkpoints=list(tmp_path.glob("_checkpoints/*/jd_checkpoint.json"))
    assert len(checkpoints)==2 and source in checkpoints
