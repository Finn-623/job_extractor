#!/usr/bin/env python3
"""N7B-F3.5 manual Terminal acceptance; uses no external network.

Run this file from a real macOS Terminal, not through pytest or an IDE output
panel.  It exercises the actual Manual-cURL fallback/menu path and the actual
ProgressReporter.  Only the HTTP transport and browser renderer are replaced
with deterministic local fixtures.
"""
from __future__ import annotations

import tempfile
import sys
from pathlib import Path
from unittest.mock import patch

import httpx

# Direct ``python scripts/...`` execution places ``scripts/`` rather than the
# repository root on sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import job_extractor.cli as cli

LIST_URL = "https://tty-demo.invalid/wecruit/positionInfo/listPosition/TTY?iSaJAx=isAjax"
LIST_CURL = f"curl '{LIST_URL}' -H 'Accept: application/json'"
WORK = "负责系统设计、交付、稳定性治理和跨团队协作，持续推动工程质量改进。" * 8
REQ = "本科及以上学历，具备工程实践、沟通协作及复杂问题解决能力。" * 8


def main() -> int:
    rows = [{"postId": f"demo-{i}", "jobName": f"TTY 演示岗位 {i}",
             "postType": "campus", "recruitType": "1"} for i in range(1, 9)]
    output = Path(tempfile.mkdtemp(prefix="job-extractor-n7b-tty-", dir="/private/tmp"))
    real_client = httpx.Client

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/listPosition/TTY"):
            return httpx.Response(200, json={"data": {"list": rows, "total": len(rows)}, "success": True})
        if "listPositionDetail" in request.url.path:
            post_id = dict(part.split("=", 1) for part in request.content.decode().split("&"))["postId"]
            number = int(post_id.rsplit("-", 1)[1])
            if number <= 5:
                return httpx.Response(200, json={"data": {"postId": post_id, "postName": post_id,
                    "workContent": WORK, "serviceCondition": REQ}})
            return httpx.Response(404, json={})
        return httpx.Response(404, json={})

    class ClientFactory:
        def __call__(self, **_kwargs):
            return real_client(transport=httpx.MockTransport(handler), timeout=10, follow_redirects=True)

    # This replaces browser I/O only; the collector still owns its fallback
    # state machine and emits the final 3/3 failed progress update.
    def failed_render(_factory, targets, **_kwargs):
        return 0, len(targets)

    print("N7B-F3.5 TTY 演示（仅本地模拟，不访问招聘网站）")
    print("请在下方依次选择：Y（启用 cURL 兜底）→ 2（保存现有结果）。")
    print("观察：进度是否原位刷新；菜单期间是否停止刷新；回退是否收束到 3/3。\n")
    with (patch("job_extractor.manual_curl.httpx.Client", ClientFactory()),
          patch("job_extractor.config.OUTPUT_ROOT", output),
          patch.object(cli, "_read_curl_block", lambda _label: LIST_CURL),
          patch.object(cli, "_detail_page_factory", lambda _reporter: lambda: object()),
          patch("job_extractor.collectors.detail_resolver.render_detail_jobs", failed_render)):
        reporter = cli.ProgressReporter()  # real stdout, stdin and TTY detection
        reporter.begin("https://tty-demo.invalid/campaign")
        ok = cli._interactive_curl_fallback("https://tty-demo.invalid/campaign", reporter,
                                            auto_code="DEMO_DISCOVERY", jd_on_timeout="skip")
    print(f"\n演示退出：{'成功' if ok else '未完成'}")
    print(f"演示输出目录：{output}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
