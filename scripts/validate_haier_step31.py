"""Run five independent ordinary CLI commands and summarize their fresh artifacts."""
from __future__ import annotations
import argparse,json,re,subprocess,sys,time
from pathlib import Path

from job_extractor.config import DISCOVERY_OUTPUT_DIR,OUTPUT_DIR

FAILURES=("NAVIGATION_TIMEOUT","HYDRATION_TIMEOUT","NETWORKIDLE_TIMEOUT","SOURCE_NOT_OBSERVED","PLAN_INVALID","REPLAY_FAILED","LIST_COLLECTION_FAILED","DETAIL_FAILED","EXPORT_FAILED")

def newest(pattern: str, after: float):
    values=[p for p in Path(pattern).parent.glob(Path(pattern).name) if p.stat().st_mtime>=after-1]
    return max(values,key=lambda p:p.stat().st_mtime,default=None)

def classify(discovery,result,excel,stdout,timed_out):
    if discovery and discovery.get("failure_classification"):return discovery["failure_classification"]
    if timed_out:return "NAVIGATION_TIMEOUT" if discovery is None else "REPLAY_FAILED"
    if "Plan valid: False" in stdout:return "PLAN_INVALID"
    if result is None:return "LIST_COLLECTION_FAILED"
    if result.get("total_fetched")!=result.get("total_expected") or result.get("total_unique")!=result.get("total_expected"):return "LIST_COLLECTION_FAILED"
    if result.get("metrics",{}).get("details_failed"):return "DETAIL_FAILED"
    if not excel:return "EXPORT_FAILED"
    return None

def main():
    parser=argparse.ArgumentParser();parser.add_argument("url");parser.add_argument("--output",type=Path,required=True);parser.add_argument("--runs",type=int,default=5);parser.add_argument("--timeout",type=int,default=180);args=parser.parse_args()
    args.output.parent.mkdir(parents=True,exist_ok=True);rows=[]
    for ordinal in range(1,args.runs+1):
        started=time.time();timed_out=False;stdout="";returncode=None
        try:
            completed=subprocess.run([sys.executable,"main.py",args.url],cwd=Path(__file__).resolve().parents[1],capture_output=True,text=True,errors="replace",timeout=args.timeout)
            stdout=completed.stdout+completed.stderr;returncode=completed.returncode
        except subprocess.TimeoutExpired as exc:
            timed_out=True;stdout=(exc.stdout or "")+(exc.stderr or "")
        total_elapsed=time.time()-started
        discovery_path=newest(str(DISCOVERY_OUTPUT_DIR/"*_discovery.json"),started)
        discovery=json.loads(discovery_path.read_text(encoding="utf-8")) if discovery_path else None
        # Bind a result only to the exact artifact printed by this command. A
        # recently written artifact from a previous long-running command must
        # never turn a discovery-only run into a false COMPLETE.
        result_match=re.search(r"(?m)^\s*JSON:\s*(.+?jobs\.json)\s*$",stdout)
        result_path=Path(result_match.group(1).strip()) if result_match else None
        if result_path and (not result_path.is_file() or result_path.stat().st_mtime<started-1):result_path=None
        result=json.loads(result_path.read_text(encoding="utf-8")) if result_path else None
        excel=bool(result_path and result_path.with_name("jobs.xlsx").exists())
        failure=classify(discovery,result,excel,stdout,timed_out)
        row={"run":ordinal,"command":f'{sys.executable} main.py "{args.url}"',"fresh":True,"reused_artifact":False,"returncode":returncode,"timed_out":timed_out,
            "discovery_source_observed":(discovery.get("probable_list_api") or {}).get("url") if discovery else None,"discovery_elapsed":discovery.get("phase_timings",{}).get("fresh_discovery_total") if discovery else None,
            "phase_timings":discovery.get("phase_timings",{}) if discovery else {},"plan_valid":"Plan valid: True" in stdout,
            "expected":result.get("total_expected") if result else None,"fetched":result.get("total_fetched") if result else None,"unique":result.get("total_unique") if result else None,
            "detail_success":result.get("metrics",{}).get("details_succeeded") if result else None,"detail_failed":result.get("metrics",{}).get("details_failed") if result else None,
            "full_jd":result.get("data_completeness",{}).get("complete_jobs") if result else None,"excel":excel,"total_elapsed":total_elapsed,"final_status":result.get("status") if result else "FAILED","failure_classification":failure,
            "discovery_artifact":str(discovery_path) if discovery_path else None,"result_artifact":str(result_path) if result_path else None,"stdout_tail":stdout[-10000:]}
        rows.append(row);args.output.write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding="utf-8");print(json.dumps(row,ensure_ascii=False),flush=True)

if __name__=="__main__":main()
