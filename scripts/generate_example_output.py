"""Generate the anonymized example output under examples/example_output/.

Builds a fully fictional CollectionResult (Example Robotics Ltd., example.com
URLs only) and runs it through the project's real reporting/export pipeline so
the five example files have exactly the structure of a real run:

    ReportManager().generate_reports(result, output_dir, collection_mode=...)
      -> jobs.json / jobs.xlsx / report.md  (ReportManager)
      -> jobs.csv / collection.json         (write_run_artifacts)

Re-runnable at any time: PYTHONPATH=. .venv/bin/python scripts/generate_example_output.py
No private paths, no real companies, no credentials.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from job_extractor.models import CollectionMetrics, CollectionResult, DataCompleteness, Job
from job_extractor.reporting.manager import ReportManager

SOURCE_URL = "https://example.com/careers"
COMPANY = "Example Robotics Ltd."
RUN_STAMP = datetime(2026, 9, 21, 12, 0, 0)
COLLECTED_AT = datetime(2026, 9, 21, 12, 3, 30)


def job(number: int, title: str, *, category: str, department: str, locations: list[str],
        recruitment_type: str, education: str, major: str, headcount: int,
        responsibilities: list[str], requirements: list[str], published: str) -> Job:
    jd = "岗位职责：\n" + "\n".join(f"{i}. {r}" for i, r in enumerate(responsibilities, 1))
    jd += "\n\n任职要求：\n" + "\n".join(f"{i}. {r}" for i, r in enumerate(requirements, 1))
    job_id = f"EX{1000 + number}"
    detail_url = f"https://jobs.example.com/position/{job_id}"
    return Job(
        company=COMPANY, job_id=job_id, job_title=title, job_category=category,
        department=department, locations=locations, recruitment_type=recruitment_type,
        education=education, major=major, headcount=headcount,
        responsibilities=responsibilities, requirements=requirements, full_jd=jd,
        apply_url=detail_url, detail_url=detail_url, source_url=SOURCE_URL,
        publish_date=published, collected_at=COLLECTED_AT,
    )


JOBS = [
    job(1, "Procurement Graduate", category="采购", department="采购部",
        locations=["上海"], recruitment_type="校园招聘", education="本科", major="采购/供应链相关",
        headcount=5,
        responsibilities=["参与供应商寻源与评估，维护供应商档案", "协助 RFQ 询价与报价比价分析", "支持采购成本分析与降本项目", "协助供应商日常管理与绩效考核"],
        requirements=["本科及以上学历，采购/供应链/商务相关专业", "了解 RFQ 流程与基本成本分析方法", "熟练使用 Excel", "沟通能力强，具备供应商管理潜质"],
        published="2026-09-10"),
    job(2, "Supply Chain Analyst", category="供应链", department="供应链管理部",
        locations=["上海", "苏州"], recruitment_type="社会招聘", education="本科", major="供应链/统计/工业工程",
        headcount=2,
        responsibilities=["负责库存数据分析与安全库存策略优化", "参与产销协同计划（S&OP）编制", "搭建供应链数据看板，输出月度分析报告", "识别计划与履约环节的改进机会"],
        requirements=["本科及以上学历，供应链/统计/工业工程相关专业", "2 年以上计划或数据分析经验", "熟练使用 SQL 与 Python 做数据分析", "逻辑清晰，能独立输出分析结论"],
        published="2026-09-05"),
    job(3, "Supplier Quality Engineer", category="质量", department="质量部",
        locations=["苏州"], recruitment_type="社会招聘", education="本科", major="机械/材料/质量工程",
        headcount=3,
        responsibilities=["负责供应商审核与质量体系评估", "跟进来料与制程质量问题的闭环处理", "推动供应商制程改善与质量能力提升", "制定检验标准并培训供应商落地"],
        requirements=["本科及以上学历，机械/材料/质量相关专业", "3 年以上制造业供应商质量管理经验", "熟悉 8D、SPC、FMEA 等质量工具", "有 ISO9001 内审员资格者优先"],
        published="2026-09-12"),
    job(4, "Mechanical Engineer", category="研发", department="机械设计部",
        locations=["深圳"], recruitment_type="社会招聘", education="本科", major="机械设计/机电一体化",
        headcount=4,
        responsibilities=["负责新产品结构设计与 CAD 建模出图", "参与产品从概念到量产的全流程开发", "进行公差分析、选型与设计验证", "配合工艺与供应商完成试产问题整改"],
        requirements=["本科及以上学历，机械设计相关专业", "熟练使用 SolidWorks 或 Creo 等 CAD 工具", "3 年以上机械/结构设计经验", "熟悉常用材料与加工工艺"],
        published="2026-09-08"),
    job(5, "Software Engineer", category="研发", department="软件平台部",
        locations=["深圳", "远程"], recruitment_type="社会招聘", education="本科", major="计算机/软件工程",
        headcount=6,
        responsibilities=["负责内部平台后端系统的设计与开发", "设计并维护稳定可靠的 RESTful APIs", "优化系统性能与数据库查询", "参与代码评审与技术方案设计"],
        requirements=["本科及以上学历，计算机相关专业", "熟练掌握 Python，了解 Web 框架", "熟悉关系型数据库与 API 设计规范", "具备良好的工程习惯与团队协作意识"],
        published="2026-09-15"),
]


def build_result() -> CollectionResult:
    metrics = CollectionMetrics(
        list_requests=2, detail_requests=5, list_pages=2,
        details_attempted=5, details_succeeded=5, details_failed=0, details_required=5,
        elapsed_seconds=42.6, total_elapsed_seconds=51.3, collection_elapsed_seconds=42.6,
        page_size=20, jd_strategy="DETAIL_REQUIRED",
        pages_requested=2, pages_succeeded=2, raw_rows=6, unique_jobs=5, duplicate_jobs=1,
        detail_request_seconds=18.4, average_detail_request_seconds=3.68,
        termination_reason="TOTAL_REACHED", collection_mode="generic",
    )
    completeness = DataCompleteness(
        total_jobs=5, complete_jobs=5, completeness_ratio=1.0,
        jd_complete=5, jd_total=5, detail_required=5, detail_attempted=5, detail_succeeded=5,
    )
    return CollectionResult(
        source_url=SOURCE_URL, platform="generic", company=COMPANY,
        metrics=metrics, total_expected=6, total_fetched=6, total_unique=5,
        status="COMPLETE", jobs=JOBS,
        duplicate_audit={"raw_total": 6, "raw_fetched": 6, "unique_jobs": 5,
                         "duplicate_records": 1, "source_cross_page_duplicates": True},
        data_completeness=completeness,
        started_at=RUN_STAMP, finished_at=COLLECTED_AT,
    )


def main(output_dir: Path | None = None) -> None:
    examples_dir = output_dir or Path(__file__).resolve().parents[1] / "examples" / "example_output"
    artifacts = ReportManager().generate_reports(build_result(), examples_dir, collection_mode="generic")
    print("Example output generated:")
    for name in ("jobs.json", "jobs.csv", "jobs.xlsx", "report.md", "collection.json"):
        path = Path(artifacts.output_directory) / name
        print(f"  {'OK' if path.exists() else 'MISSING'}  {path}")


if __name__ == "__main__":
    main()
