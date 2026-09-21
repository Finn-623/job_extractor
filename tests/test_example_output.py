"""最小测试：验证 examples/example_output/ 为真实导出管线的合法产物。

覆盖两层：
1. 仓库内已提交的示例文件可直接解析（json / csv / xlsx / md / collection.json）。
2. scripts/generate_example_output.py 的 main() 在 tmp_path 里可重复生成同样的 5 个文件。
"""

import csv
import hashlib
import json
from pathlib import Path

from openpyxl import load_workbook

REPO = Path(__file__).resolve().parents[1]
EXAMPLE_DIR = REPO / "examples" / "example_output"

EXPECTED_FILES = {
    "jobs.json",
    "jobs.csv",
    "jobs.xlsx",
    "report.md",
    "collection.json",
}


def test_committed_files_exist():
    assert EXAMPLE_DIR.is_dir()
    assert EXPECTED_FILES.issubset({p.name for p in EXAMPLE_DIR.iterdir()})


def test_jobs_json_parses_with_fictional_company():
    data = json.loads((EXAMPLE_DIR / "jobs.json").read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    assert len(data["jobs"]) == 5
    for job in data["jobs"]:
        assert job["company"] == "Example Robotics Ltd."
        assert job["detail_url"].startswith("https://jobs.example.com/")
        assert job["full_jd"]


def test_jobs_csv_has_header_and_rows():
    with (EXAMPLE_DIR / "jobs.csv").open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))
    assert len(rows) == 6  # header + 5 jobs
    assert rows[0][:4] == ["id", "title", "company", "location"]


def test_jobs_xlsx_has_three_sheets():
    wb = load_workbook(EXAMPLE_DIR / "jobs.xlsx", read_only=True)
    assert wb.sheetnames == ["Summary", "Jobs", "Data Quality"]
    assert wb["Jobs"].max_row == 6


def test_collection_json_mode_generic():
    data = json.loads((EXAMPLE_DIR / "collection.json").read_text(encoding="utf-8"))
    assert data["collection_mode"] == "generic"
    assert data["source_url"] == "https://example.com/careers"


def test_report_md_mentions_company():
    text = (EXAMPLE_DIR / "report.md").read_text(encoding="utf-8")
    assert "Example Robotics Ltd." in text
    assert "COMPLETE" in text


def test_generator_reproduces_files(tmp_path):
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "generate_example_output", REPO / "scripts" / "generate_example_output.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    module.main(output_dir=tmp_path)
    produced = {p.name for p in tmp_path.iterdir()}
    assert EXPECTED_FILES.issubset(produced)
    regenerated = json.loads((tmp_path / "jobs.json").read_text(encoding="utf-8"))
    assert len(regenerated["jobs"]) == 5


def _workbook_content_snapshot(path: Path) -> dict:
    """用 openpyxl 读取全部 sheet 的名称 / 维度 / cell 值。"""
    workbook = load_workbook(path, read_only=True)
    snapshot: dict = {}
    for sheet in workbook.worksheets:
        snapshot[sheet.title] = {
            "dims": (sheet.max_row, sheet.max_column),
            "cells": tuple(tuple(cell.value for cell in row) for row in sheet.iter_rows()),
        }
    workbook.close()
    return snapshot


def test_generator_deterministic(tmp_path):
    """两次生成必须确定性：文本产物 byte-identical；xlsx 只要求 workbook 内容一致。

    .xlsx 本质是 zip 包，打包元数据（内部时间戳/压缩顺序）允许少量字节差异——
    只要 openpyxl 读到的 sheet 名称、维度与每个 cell 的值完全相同，即判定
    xlsx deterministic（Release Gate 规则）。
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "generate_example_output", REPO / "scripts" / "generate_example_output.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    dir_a, dir_b = tmp_path / "run_a", tmp_path / "run_b"
    module.main(output_dir=dir_a)
    module.main(output_dir=dir_b)

    for name in ("jobs.json", "jobs.csv", "report.md", "collection.json"):
        digest_a = hashlib.sha256((dir_a / name).read_bytes()).hexdigest()
        digest_b = hashlib.sha256((dir_b / name).read_bytes()).hexdigest()
        assert digest_a == digest_b, f"{name} 不是 byte deterministic"

    assert _workbook_content_snapshot(dir_a / "jobs.xlsx") == _workbook_content_snapshot(
        dir_b / "jobs.xlsx"
    ), "jobs.xlsx workbook 内容不确定"
