from __future__ import annotations
from pathlib import Path
from openpyxl import Workbook
from openpyxl.styles import Alignment,Border,Font,PatternFill,Side
from job_extractor.models import CollectionResult,Job
from job_extractor.reporting.field_formatters import excel_text,job_completeness,locations_text,numbered,scope_text,truncate_excel

HEADERS=["序号","公司","岗位名称","Job ID","招聘类型","岗位类别","部门","工作地点","学历","专业","招聘人数","发布时间","岗位职责","任职要求","完整 JD","Detail URL","Apply URL","Source URL","数据完整性"]
WIDTHS=[8,20,35,25,15,20,25,25,18,30,12,20,55,55,70,45,45,45,22]
HEADER_FILL=PatternFill("solid",fgColor="1F4E78");SECTION_FILL=PatternFill("solid",fgColor="D9EAF7");THIN=Side(style="thin",color="B7C9D6")

def quality_rows(result:CollectionResult):
    rows=[]
    for job in result.jobs:
        status=job_completeness(job)
        if status=="MISSING_JD":rows.append((job.job_id,job.job_title,"SOURCE_MISSING_JD","No full JD was available from the collected official source.",job.detail_url))
        elif status=="STRUCTURE_PARTIAL":
            missing=[]
            if not job.responsibilities:missing.append("responsibilities")
            if not job.requirements:missing.append("requirements")
            rows.append((job.job_id,job.job_title,"STRUCTURED_FIELDS_UNAVAILABLE",f"Full JD preserved; structured {', '.join(missing)} unavailable.",job.detail_url))
        if len(job.full_jd or "")>32767:rows.append((job.job_id,job.job_title,"EXCEL_CELL_TRUNCATED","Excel cell truncated; jobs.json retains the full value.",job.detail_url))
    for warning in result.warnings:rows.append((None,None,"SOURCE DATA WARNING",warning,None))
    for error in result.errors:rows.append((None,None,"COLLECTION ERROR",error,None))
    return rows

class ExcelReporter:
    def generate(self,result:CollectionResult,path:Path)->Path:
        workbook=Workbook();summary=workbook.active;summary.title="Summary";jobs=workbook.create_sheet("Jobs");quality=workbook.create_sheet("Data Quality")
        sections=[("COLLECTION",[("Company",result.company),("Platform",result.platform),("Source URL",result.source_url),("Recruitment scope",scope_text(result.scope)),("Collection time",result.finished_at or result.started_at),("Collection status",result.status)]),
            ("JOB COUNTS",[("Expected",result.total_expected),("Fetched",result.total_fetched),("Unique",result.total_unique)]),
            ("DATA QUALITY",[("Complete jobs",result.data_completeness.complete_jobs),("Missing JD",result.data_completeness.missing_jd_jobs),("Missing responsibilities",result.data_completeness.missing_responsibilities_jobs),("Missing requirements",result.data_completeness.missing_requirements_jobs),("Completeness ratio",result.data_completeness.completeness_ratio)]),
            ("PERFORMANCE",[("Elapsed",result.metrics.elapsed_seconds),("Pages",result.metrics.list_pages),("List requests",result.metrics.list_requests),("Detail requests",result.metrics.detail_requests),("JD strategy",result.metrics.jd_strategy)]),
            ("WARNINGS",[("Warnings count",len(result.warnings)),("Errors count",len(result.errors))])]
        row=1
        for name,items in sections:
            summary.cell(row,1,name);summary.cell(row,1).font=Font(bold=True);summary.cell(row,1).fill=SECTION_FILL;summary.merge_cells(start_row=row,start_column=1,end_row=row,end_column=2);row+=1
            for metric,value in items:summary.cell(row,1,metric);summary.cell(row,2,excel_text(value));row+=1
            row+=1
        summary.column_dimensions["A"].width=28;summary.column_dimensions["B"].width=70
        jobs.append(HEADERS)
        for cell in jobs[1]:cell.font=Font(bold=True,color="FFFFFF");cell.fill=HEADER_FILL;cell.alignment=Alignment(horizontal="center");cell.border=Border(bottom=THIN)
        for index,job in enumerate(result.jobs,1):
            resp,_=truncate_excel(numbered(job.responsibilities));req,_=truncate_excel(numbered(job.requirements));full,_=truncate_excel(job.full_jd)
            row=[index,job.company,job.job_title,job.job_id,job.recruitment_type,job.job_category,job.department,locations_text(job.locations),job.education,job.major,job.headcount,job.publish_date,resp,req,full,"查看岗位" if job.detail_url else None,"前往投递" if job.apply_url else None,"来源网页" if job.source_url else None,job_completeness(job)]
            jobs.append([excel_text(x) for x in row]);r=jobs.max_row;jobs.row_dimensions[r].height=45
            for col,url in ((16,job.detail_url),(17,job.apply_url),(18,job.source_url)):
                if url:jobs.cell(r,col).hyperlink=url;jobs.cell(r,col).style="Hyperlink"
            for col in range(1,len(HEADERS)+1):jobs.cell(r,col).alignment=Alignment(vertical="top",wrap_text=col in (13,14,15))
        jobs.freeze_panes="A2";jobs.auto_filter.ref=jobs.dimensions
        for i,width in enumerate(WIDTHS,1):jobs.column_dimensions[jobs.cell(1,i).column_letter].width=width
        quality.append(["Job ID","Job Title","Issue Type","Description","Detail URL"])
        for cell in quality[1]:cell.font=Font(bold=True,color="FFFFFF");cell.fill=HEADER_FILL
        issues=quality_rows(result)
        if issues:
            for issue in issues:quality.append([excel_text(x) for x in issue])
        else:quality.append(["No data quality issues."])
        quality.freeze_panes="A2";quality.auto_filter.ref=quality.dimensions
        for letter,width in zip(("A","B","C","D","E"),(25,35,35,70,45)):quality.column_dimensions[letter].width=width
        path.parent.mkdir(parents=True,exist_ok=True);workbook.save(path);return path
