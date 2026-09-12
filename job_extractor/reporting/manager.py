from pathlib import Path
from job_extractor.exporters import export_collection_result
from job_extractor.models import CollectionResult
from job_extractor.reporting.excel_reporter import ExcelReporter
from job_extractor.reporting.markdown_reporter import MarkdownReporter
from job_extractor.reporting.models import ReportArtifacts

class ReportManager:
    def generate_reports(self,result:CollectionResult,output_dir:Path,markdown_detail:str="auto")->ReportArtifacts:
        output_dir.mkdir(parents=True,exist_ok=True)
        json_path=output_dir/"jobs.json";excel_path=output_dir/"jobs.xlsx";markdown_path=output_dir/"report.md"
        export_collection_result(result,json_path);ExcelReporter().generate(result,excel_path);MarkdownReporter().generate(result,markdown_path,markdown_detail)
        return ReportArtifacts(output_directory=output_dir,json_path=json_path,excel_path=excel_path,markdown_path=markdown_path)
