from pathlib import Path
from job_extractor.exporters import export_collection_result
from job_extractor.models import CollectionResult
from job_extractor.output_layout import FAILED_ROOT, resolve_run_directory, write_run_artifacts
from job_extractor.reporting.excel_reporter import ExcelReporter
from job_extractor.reporting.markdown_reporter import MarkdownReporter
from job_extractor.reporting.models import ReportArtifacts

class ReportManager:
    def generate_reports(self,result:CollectionResult,output_dir:Path|None=None,markdown_detail:str="auto",*,collection_mode:str|None=None,run_stamp=None)->ReportArtifacts:
        """Generate the run directory + report artifacts.

        ``output_dir`` semantics:
        - ``None`` (default): STEP91 formal layout —
          ``<OUTPUT_DIR>/<Company>/<stamp>/`` (``_unknown``/``_failed`` variants),
          computed from the result and created automatically.
        - explicit path (legacy callers/tests): used as-is, files written there.
        - FAILED runs in the formal layout: only ``error_report.json`` under
          ``<OUTPUT_ROOT>/_failed/<Company|_unknown>/<stamp>/``.
        """
        failed = result.status == "FAILED"
        if output_dir is None:
            from job_extractor.config import OUTPUT_ROOT
            output_dir=resolve_run_directory(OUTPUT_ROOT,company=result.company,source_url=result.source_url,
                failed=failed,at=run_stamp)
        output_dir.mkdir(parents=True,exist_ok=True)
        json_path=output_dir/"jobs.json";excel_path=output_dir/"jobs.xlsx";markdown_path=output_dir/"report.md"
        if failed and FAILED_ROOT in output_dir.parts:
            # Spec: failed runs record only the error report; no jobs/report artifacts.
            from job_extractor.reporting.error_report import export_error_report
            export_error_report(result,output_dir/"error_report.json",
                collection_mode=collection_mode or result.metrics.collection_mode or result.platform or "provider")
            return ReportArtifacts(output_directory=output_dir,json_path=json_path,excel_path=excel_path,markdown_path=markdown_path)
        export_collection_result(result,json_path);ExcelReporter().generate(result,excel_path);MarkdownReporter().generate(result,markdown_path,markdown_detail)
        write_run_artifacts(output_dir,result,collection_mode or result.metrics.collection_mode or result.platform or "provider")
        return ReportArtifacts(output_directory=output_dir,json_path=json_path,excel_path=excel_path,markdown_path=markdown_path)
