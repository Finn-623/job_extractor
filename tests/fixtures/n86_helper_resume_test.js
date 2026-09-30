// N5.6/N9.2 helper tests — fake rocket-* DOM + fake detail worker window.
// Usage: node n86_helper_resume_test.js <scenario>
//   resume-success | retry-failure | warmup-verification | single-page
//   detail-worker | worker-blocked | worker-closed | identity-mismatch
"use strict";
const fs = require("fs");
const path = require("path");

let helperSource = fs.readFileSync(
  path.join(__dirname, "..", "..", "scripts", "pdd_browser_console_helper.js"),
  "utf8");
const SCENARIO = process.argv[2] || "resume-success";
if (process.env.HELPER_DEBUG || SCENARIO === "worker-closed") {
  helperSource = helperSource
    .replace('return "DETAIL_DOM_NOT_CREDIBLE";', 'console.log("[DBG DOM_NOT_CREDIBLE]"); return "DETAIL_DOM_NOT_CREDIBLE";')
    .replace('if (Date.now() >= deadline) return "DETAIL_NAV_TIMEOUT";', 'if (Date.now() >= deadline) { console.log("[DBG NAV_TIMEOUT]"); return "DETAIL_NAV_TIMEOUT"; }')
    .replace('if (workerLandedOk(target)) break;', 'console.log("[DBG landed=" + workerLandedOk(target) + " workerHref=" + (detailWorker.location && detailWorker.location.href) + "]"); if (workerLandedOk(target)) break;')
    .replace('console.log("DETAIL_WORKER_CLOSED");', 'console.log("DETAIL_WORKER_CLOSED"); console.log("[DBG WORKER_CLOSED]");')
    .replace('      const credible = text.length >= DETAIL_MIN_TEXT &&', 'console.log("[DBG poll len=" + text.length + "]");\n      const credible = text.length >= DETAIL_MIN_TEXT &&')
    .replace('console.log("JD 详情：" + batchIndex + " / " + batchSize +',
      'console.log("[CLOSE_INJECT] batchIndex=" + batchIndex); if (batchIndex === 3 && detailWorker) { detailWorker.closed = true; } console.log("JD 详情：" + batchIndex + " / " + batchSize +');
}

let FAIL_PAGE = 10, TOTAL_PAGES = 16, PAGE_SIZE = 50;
if (SCENARIO === "single-page") { TOTAL_PAGES = 1; }
if (SCENARIO === "detail-worker") { TOTAL_PAGES = 3; PAGE_SIZE = 3; FAIL_PAGE = 2; }
if (SCENARIO === "dup-anchors" || SCENARIO === "unique-51") {
  TOTAL_PAGES = 1; FAIL_PAGE = 99;
}
if (SCENARIO === "unique-51") { PAGE_SIZE = 50; }
if (SCENARIO === "dup-anchors") { PAGE_SIZE = 48; }
const LAST_PAGE = TOTAL_PAGES;
// which pages fail their list request until the user completes verification
const VERIFIED_PAGES = SCENARIO === "warmup-verification"
  ? { 2: true, 10: true }   // warmup page 2 AND page 10 need verification
  : (SCENARIO !== "single-page" && SCENARIO !== "identity-mismatch")
    ? { [FAIL_PAGE]: true } // only the failing page needs verification
    : {};

// ---- virtual clock: every sleep advances the clock and runs inline ----
let vnow = 0;

// ---- fake DOM ----
const state = {
  active: 1,
  list: 1,
  verified: false,
  clicks: [],
  popupBlocked: false,
  workerOpens: 0,
  workerNavigations: 0,
  workerRedirect: null,     // { forCode, landingCode } simulates wrong landing
  workerBodyOverride: null,
};

function jobLink(page, index, hidden, hot) {
  const code = (hot ? "H" : "T") + String(page).padStart(2, "0") + String(index).padStart(3, "0");
  return {
    textContent: "岗位 " + code,
    href: "https://careers.example.com/jobs/detail?code=" + code + "&lang=zh",
    offsetParent: hidden ? null : {},
    getClientRects: () => (hidden ? [] : [{}]),
    closest: (sel) => (hot && sel === ".hot-job-items" ? { hot: true } : null),
  };
}

function jobsFor(page) {
  const out = [];
  const count = (SCENARIO !== "single-page" && SCENARIO !== "detail-worker" &&
                 page === LAST_PAGE) ? 42 : PAGE_SIZE;
  for (let i = 0; i < count; i++) {
    out.push(jobLink(page, i));
    // duplicate visible anchors per job (real list DOM behaviour): 2-3
    // anchors carry the same detail code
    if (SCENARIO === "dup-anchors") {
      out.push(jobLink(page, i));
      if (i % 2 === 0) out.push(jobLink(page, i));
    }
  }
  // stale/hidden pollution: previous-page anchors retained in the DOM but
  // hidden, plus hidden same-page duplicates — must never be accumulated
  if (SCENARIO === "count-invalid") {
    for (let i = 0; i < count; i++) out.push(jobLink(page, count + i));  // visible extras
  }


  if (SCENARIO === "hidden-stale") {
    for (let i = 0; i < 7; i++) out.push(jobLink(page, count + i, true));
    for (let i = 0; i < count; i++) out.push(jobLink(Math.max(1, page - 1), i, true));
  }
  if (SCENARIO === "unique-51") {
    out.push(jobLink(page, count));       // 51st unique job
  }
  // Hot Jobs side region: visible anchors inside .hot-job-items containers,
  // possibly duplicating main-list codes — must never enter list collection
  if (SCENARIO === "hot-jobs" || SCENARIO === "hot-dup") {
    const hotCount = SCENARIO === "hot-jobs" ? 8 : 4;
    const hotBase = SCENARIO === "hot-dup" ? page : 900;  // hot-dup reuses main codes
    for (let i = 0; i < hotCount; i++) {
      const el = jobLink(hotCount > 0 && SCENARIO === "hot-dup" ? page : 900 + page, i, false);
      el.closest = (sel) => (sel === ".hot-job-items" ? { hot: true } : null);
      out.push(el);
    }
  }
  return out;
}

function goToPage(page) {
  // the site's own pagination click: it always advances the active page,
  // but the list request itself fails (54001) until the user completes
  // the site's own verification — then the request succeeds.
  state.clicks.push(page);
  state.active = page;
  if (VERIFIED_PAGES[page] && !state.verified) {
    state.listRequestFailed = true;
    return;
  }
  state.listRequestFailed = false;
  state.list = page;
}

const paginationItem = (page) => ({
  textContent: String(page),
  classList: {
    contains: (c) =>
      c === "rocket-pagination-item" || c === "rocket-pagination-item-" + page,
  },
  getAttribute: () => null,
  offsetParent: {},
  getClientRects: () => [{}],
  querySelector: (sel) => (sel === "a" ? { click() { goToPage(page); } } : null),
  click() { goToPage(page); },
});

const nextEl = {
  get textContent() { return ""; },
  get classList() {
    const last = state.active >= TOTAL_PAGES;
    return { contains: (c) => c === "rocket-pagination-next" || (last && c === "rocket-pagination-disabled") };
  },
  getAttribute: () => (state.active >= TOTAL_PAGES ? "true" : "false"),
  offsetParent: {},
  querySelector: (sel) => (sel === "a" ? { click() { goToPage(state.active + 1); } } : null),
  click() { goToPage(state.active + 1); },
};

const sizeChangerEl = { textContent: "", offsetParent: {}, getClientRects: () => [{}], querySelector: () => null, click() {} };
const sizeSelectionEl = { textContent: "50 条/页", offsetParent: {} };

const documentStub = {
  querySelector(selector) {
    if (selector === ".rocket-pagination-item-active") {
      return paginationItem(state.active);
    }
    if (selector === ".ant-pagination-item-active") return null;
    if (selector === ".rocket-pagination-next") return nextEl;
    if (selector === ".ant-pagination-next") return null;
    if (selector === ".rocket-pagination-options-size-changer") return sizeChangerEl;
    if (selector === ".rocket-pagination-options-size-changer .rocket-select-selection-item") {
      return sizeSelectionEl;
    }
    const itemMatch = /^\.rocket-pagination-item-(\d+)$/.exec(selector);
    if (itemMatch) return paginationItem(Number(itemMatch[1]));
    return null;
  },
  querySelectorAll(selector) {
    if (selector.startsWith("a[href*='/jobs/detail?code=']")) {
      return jobsFor(state.list);
    }
    if (selector === ".rocket-pagination-item,.ant-pagination-item") {
      const items = [];
      for (let p = 1; p <= LAST_PAGE; p++) items.push(paginationItem(p));
      return items;
    }
    return [];
  },
};

const logs = [];
const consoleStub = { log: (msg) => logs.push(String(msg)) };
const windowStub = {};
const JD_TEXT = "岗位职责\n1. 负责风控策略建模与迭代，输出风控规则与策略文档。\n2. 参与反欺诈反作弊体系建设，跟踪策略效果并持续优化。\n任职要求\n1. 本科及以上学历，计算机、数学或相关专业。\n2. 熟悉常用机器学习算法与风控业务，具备扎实的工程实现能力。";

// ---- fake detail worker window ----
function makeWorkerWindow(startUrl) {
  const startCode = (startUrl.match(/code=([^&]+)/) || [])[1];
  console.log("[OPEN] startCode=" + startCode + " redirect=" + JSON.stringify(state.workerRedirect));
  if (state.workerRedirect && startCode === state.workerRedirect.forCode) {
    startUrl = startUrl.replace(startCode, state.workerRedirect.landingCode);
  }
  const w = {
    _page: startUrl,
    closed: false,
    get location() {
      const self = this;
      return {
        get href() { return self._page; },
        assign(url) {
          const code = (url.match(/code=([^&]+)/) || [])[1];
          if (state.workerRedirect && code === state.workerRedirect.forCode) {
            url = url.replace(code, state.workerRedirect.landingCode);
          }
          self._page = url;
          state.workerNavigations += 1;
        },
      };
    },
    get document() {
      const code = (this._page.match(/code=([^&]+)/) || [])[1] || "";
      const landing = state.workerRedirect && state.workerRedirect.forCode === code
        ? state.workerRedirect.landingCode : code;
      const failed = VERIFIED_PAGES[landing] && !state.verified;
      let bodyText = state.workerBodyOverride || (failed
        ? "首页 商家入驻 很遗憾，您要访问的职位已过期或不存在 版权所有"
        : JD_TEXT + " 岗位 " + landing);
      const evolve = state.workerEvolve && state.workerEvolve[code];
      if (evolve) bodyText = bodyText.replace("岗位职责", evolve).slice(0, 400);
      if (state.shellThenReal && state.shellThenReal[code] &&
          state.workerPolls < state.shellThenReal[code]) {
        bodyText = "岗位职责\n任职要求\n加分项\n无\nTips: 完善简历有助于提高投递成功率。\n简历投递\n邮件提示：请留意站内信通知。\nCopyright © example.com 版权所有";
      }
      state.workerPolls = (state.workerPolls || 0) + 1;
      return { body: { get innerText() { return bodyText; } } };
    },
  };
  return w;
}

windowStub.open = (url, name) => {
  if (state.popupBlocked) return null;
  state.workerOpens += 1;
  state.worker = makeWorkerWindow(url);
  return state.worker;
};

// ---- sandboxed evaluation with the virtual clock ----
const sandboxFactory = new Function(
  "window", "document", "console", "setTimeout", "Date", "location",
  helperSource);
global.setTimeout = (cb, ms) => { vnow += Math.max(ms, 1); cb(); return 0; };
sandboxFactory(windowStub, documentStub, consoleStub, global.setTimeout,
  { now: () => vnow }, { href: "https://careers.example.com/jobs" });

let failures = 0;
function expect(condition, label) {
  if (condition) { console.log("PASS " + label); }
  else { console.log("FAIL " + label); failures += 1; }
}
const pump = async () => new Promise((resolve) => setImmediate(resolve));
const readyPages = () => logs.filter((l) => l.startsWith("page ") && l.endsWith(" ready")).length;
const results = () => (windowStub.__JOB_HELPER_RESULT__ || { detail_results: [] }).detail_results;

async function main() {
  if (SCENARIO === "single-page") {
    let guard = 0;
    while (!logs.some((l) => l === "done") && guard++ < 2000) await pump();
    expect(logs.some((l) => l === "single page: no warmup round-trip needed"),
      "single-page warmup skipped");
    expect(logs.some((l) => l === "last page reached"), "last page reached");
    expect(logs.some((l) => l === "total pages visited: 1"), "one page visited");
    console.log(failures === 0 ? "SCENARIO_ALL_PASS" : "SCENARIO_FAILED " + failures);
    process.exit(failures === 0 ? 0 : 1);
  }

  if (SCENARIO === "dup-anchors" || SCENARIO === "unique-51" ||
      SCENARIO === "count-invalid") {
    let guard = 0;
    while (!logs.some((l) => l === "done") && guard++ < 4000) await pump();
    if (SCENARIO === "count-invalid") {
      expect(logs.some((l) => l.startsWith("PAGE_TARGET_COUNT_INVALID page 1")),
        "count invalid reported");
      expect(!logs.some((l) => l.startsWith("page 1 new_unique:")), "no polluted accumulation");
    } else {
      expect(!logs.some((l) => l.startsWith("PAGE_TARGET_COUNT_INVALID")), "no invalid count");
      const r2 = results();
      if (SCENARIO === "dup-anchors") {
        expect(r2.every((x) => x.status === "SUCCESS"), "all unique jobs captured");
        expect(new Set(r2.map((x) => x.code)).size === r2.length,
          "accumulator adds each code exactly once");
      }
    }
    console.log(failures === 0 ? "SCENARIO_ALL_PASS" : "SCENARIO_FAILED " + failures);
    process.exit(failures === 0 ? 0 : 1);
  }

  if (SCENARIO === "hot-jobs" || SCENARIO === "hot-dup") {
    let guard = 0;
    while (!logs.some((l) => l === "done") && guard++ < 4000) await pump();
    if (SCENARIO === "hot-jobs") {
      // 50 main-list jobs + 8 hot jobs: unique_jobs must be 50, not 58
      expect(logs.some((l) => l === "page 1 unique_jobs: 50"),
        "hot jobs excluded from the main-list count");
      expect(logs.some((l) => l === "page 1 new_unique: +50"), "+50 accumulated");
      expect(logs.some((l) => l === "page 1 accumulated: 50"), "accumulated 50");
      expect(logs.some((l) => l === "page 1 raw_anchors: 50"),
        "raw_anchors counts main-list anchors only");
    } else {
      // hot-dup: hot anchors duplicate main-list codes — no effect at all
      expect(logs.some((l) => l === "page 1 unique_jobs: 50"),
        "hot duplicates do not change the main-list count");
    }
    console.log(failures === 0 ? "SCENARIO_ALL_PASS" : "SCENARIO_FAILED " + failures);
    process.exit(failures === 0 ? 0 : 1);
  }

  // ---- scenario 1: stability timeout pauses automation (no next click) ----
  let guard = 0;
  let readyPagesAtPause = null;
  const hasVerificationPause = SCENARIO !== "identity-mismatch";
  if (!hasVerificationPause) {
    while (!logs.some((l) => l === "done") && guard++ < 2000) await pump();
  }
  while (hasVerificationPause &&
         !logs.some((l) => l.includes("Automation is paused")) && guard++ < 300) {
    await pump();
  }
  if (hasVerificationPause) {
    readyPagesAtPause = logs.filter((l) => l.startsWith("page ") && l.endsWith(" ready")).length;
  }
  if (hasVerificationPause) {
    if (SCENARIO !== "warmup-verification" && SCENARIO !== "detail-worker") {
      expect(logs.some((l) => l === "PAGE_STABILITY_TIMEOUT page " + (FAIL_PAGE - 1)),
        "stability timeout reported at page " + (FAIL_PAGE - 1));
    }
    expect(logs.some((l) => l.includes("Automation is paused")), "paused message");
    expect(logs.some((l) => l.includes("jobHelperResume()")), "resume instructions shown");
  }
  const readyPages = logs.filter((l) => l.startsWith("page ") && l.endsWith(" ready")).length;
  if (SCENARIO !== "warmup-verification" && SCENARIO !== "detail-worker" &&
      hasVerificationPause) {
    expect(readyPages === FAIL_PAGE - 1, "no pages advanced past the failing page while paused");
    await pump(); await pump();
    expect(logs.filter((l) => l.startsWith("page ") && l.endsWith(" ready")).length ===
      FAIL_PAGE - 1, "still paused without a resume");
  }

  // ---- user completes the site's verification, then explicitly resumes ----
  if (hasVerificationPause) {
    state.verified = true;
    expect(windowStub.jobHelperResume() === true, "jobHelperResume accepts the resume");
  }
  guard = 0;
  while (!logs.some((l) => l === "done") && guard++ < 8000) { await pump(); }

  // ---- list phase assertions ----
  if (hasVerificationPause) {
    expect(logs.some((l) => l === "resume requested"), "explicit resume logged");
    if (SCENARIO !== "warmup-verification") {
      expect(logs.some((l) => l.startsWith("stepping back to page ")), "previous→current retry step");
    }
    expect(logs.some((l) => l === "verification/page recovered"), "page recovered after resume");
    expect(logs.some((l) => l === "resuming automation"), "automation resumed");
  }
  expect(logs.some((l) => l === "last page reached"), "pagination continued to the last page");
  expect(logs.some((l) => l === "total pages visited: " + TOTAL_PAGES), "all pages visited");
  expect(logs.some((l) => l === "done"), "helper completed");
  expect(state.list === LAST_PAGE, "final rendered page is the last page");
  expect(logs.some((l) => l === "refreshing page 1 for HAR capture (page 1 -> 2 -> 1)"),
    "warmup round-trip logged");
  expect(logs.some((l) => l === "initial page refreshed for HAR capture"),
    "initial page refreshed");
  // per-page accumulation audit: each page logs its unique-code delta and
  // the running total (evidence for total reconciliation)
  const newUniqueLines = logs.filter((l) => l.startsWith("page ") && l.includes(" new_unique: +"));
  const accumLines = logs.filter((l) => l.startsWith("page ") && l.includes(" accumulated: "));
  expect(newUniqueLines.length === TOTAL_PAGES, "every page logs its new_unique");
  expect(accumLines.length === TOTAL_PAGES, "every page logs its accumulation");
  if (SCENARIO === "resume-success") {
    // fixture: 50 links/page + 42 on the last page, dedupe by code
    expect(logs.some((l) => l === "page 1 unique_jobs: 50"), "page 1 unique jobs count");
    expect(logs.some((l) => l === "page 1 new_unique: +50"), "page 1 accumulated +50");
    expect(logs.some((l) => l === "page " + TOTAL_PAGES + " new_unique: +42"),
      "last page accumulated the tail");
    expect(logs.some((l) => l === "page " + TOTAL_PAGES + " accumulated: " + (15 * 50 + 42)),
      "last page grand total");
  }

  if (SCENARIO === "detail-worker" || SCENARIO === "warmup-verification") {
    expect(logs.some((l) => l === "WARMUP_PAGE_UNSTABLE page 2"),
      "page-2 verification paused the warmup before formal pagination");
    expect(readyPagesAtPause === 0,
      "no formal pagination before warmup completes");
  }
  const firstPageReadyIndex = logs.findIndex((l) => l === "page 1 ready");
  expect(state.clicks.includes(2) && state.clicks.includes(1) &&
    state.clicks.indexOf(2) < state.clicks.indexOf(1) &&
    state.clicks.indexOf(1) < firstPageReadyIndex,
    "page 1 request recreated before formal pagination");

  const detailScenario = SCENARIO === "detail-worker" || SCENARIO === "worker-blocked" ||
    SCENARIO === "worker-closed" || SCENARIO === "identity-mismatch" ||
    SCENARIO === "ids-filter" || SCENARIO === "ids-unknown" ||
    SCENARIO === "bonus" || SCENARIO === "bonus-none" ||
    SCENARIO === "shell-only" || SCENARIO === "late-jd" ||
    SCENARIO === "dup-anchors" || SCENARIO === "unique-51" ||
    SCENARIO === "hidden-stale" || SCENARIO === "count-invalid";
  if (hasVerificationPause && !detailScenario) {
    expect(logs.some((l) => l === "resume requested"), "explicit resume logged");
    if (SCENARIO !== "warmup-verification") {
      expect(logs.some((l) => l.startsWith("stepping back to page ")), "previous→current retry step");
    }
    expect(logs.some((l) => l === "verification/page recovered"), "page recovered after resume");
    expect(logs.some((l) => l === "resuming automation"), "automation resumed");
    console.log(failures === 0 ? "SCENARIO_ALL_PASS" : "SCENARIO_FAILED " + failures);
    process.exit(failures === 0 ? 0 : 1);
  }
  if (!detailScenario) {
    console.log(failures === 0 ? "SCENARIO_ALL_PASS" : "SCENARIO_FAILED " + failures);
    process.exit(failures === 0 ? 0 : 1);
  }

  // ================= detail worker scenarios =================
  // ---- detail queue built from real list anchors ----
  guard = 0;
  if (SCENARIO === "worker-blocked") state.popupBlocked = true;
  let startDetailsPromise = null;
  let idsFilterPage1Code = null;
  if (SCENARIO === "identity-mismatch") {
    const firstCode = (jobsFor(LAST_PAGE)[0].href.match(/code=([^&]+)/) || [])[1];
    state.workerRedirect = { forCode: firstCode, landingCode: "T099999" };
    startDetailsPromise = windowStub.jobHelperStartDetails();
  } else if (SCENARIO === "ids-filter") {
    idsFilterPage1Code = (jobsFor(1)[0].href.match(/code=([^&]+)/) || [])[1];
    startDetailsPromise = windowStub.jobHelperStartDetails([idsFilterPage1Code]);
  } else if (SCENARIO === "ids-unknown") {
    startDetailsPromise = windowStub.jobHelperStartDetails(["NOPE"]);
  } else {
    startDetailsPromise = windowStub.jobHelperStartDetails();
  }
  guard = 0;
  while (!logs.some((l) => l.startsWith("detail capture complete")) && guard++ < 60000) {
    await pump();
    // any detail pause waits for an explicit user resume
    if (logs.some((l) => l.includes("完成后输入 jobHelperResume() 继续"))) {
      logs.push("__RESUME_FIRED__");
      windowStub.jobHelperResume();
    }
  }
  const startDetails = await startDetailsPromise;
  const r = results();
  if (process.env.DETAIL_DEBUG) {
    console.log("DUMP " + logs.join(" || "));
    console.log("TAIL " + logs.filter((l) => l.startsWith("detail") || l.startsWith("JD") || l === "DETAIL_QUEUE_EMPTY").join(" || "));
  }

  if (SCENARIO === "ids-filter") {
    // explicit id list restricts the run to those stable ids. Pagination
    // ended on the LAST page: the requested code lives on page 1 and is
    // long gone from the current DOM — only the accumulated list has it.
    const page1Code = (jobsFor(1)[0].href.match(/code=([^&]+)/) || [])[1];
    const lastPageCode = (jobsFor(LAST_PAGE)[0].href.match(/code=([^&]+)/) || [])[1];
    expect(page1Code !== lastPageCode, "requested code is not on the final page");
    const started = await startDetailsPromise;
    guard = 0;
    while (!logs.some((l) => l.startsWith("detail capture complete")) && guard++ < 40000) {
      await pump();
    }
    const r2 = results();
    expect(started === true, "filtered run started");
    expect(r2.length === 1, "only the requested id processed");
    expect(r2.every((x) => x.status === "SUCCESS" && x.code === page1Code),
      "page-1 code captured from the accumulated list");
    expect(logs.some((l) => l.startsWith("JD 详情：1 / 1 ——")),
      "batch progress uses the filtered batch size");
    console.log(failures === 0 ? "SCENARIO_ALL_PASS" : "SCENARIO_FAILED " + failures);
    process.exit(failures === 0 ? 0 : 1);
  }

  if (SCENARIO === "ids-unknown") {
    const started = await startDetailsPromise;
    expect(started === false, "unknown ids abort cleanly");
    expect(logs.some((l) => l.startsWith("DETAIL_IDS_NOT_IN_QUEUE")), "unknown ids reported");
    console.log(failures === 0 ? "SCENARIO_ALL_PASS" : "SCENARIO_FAILED " + failures);
    process.exit(failures === 0 ? 0 : 1);
  }

  if (SCENARIO === "hidden-stale") {
    expect(logs.some((l) => l === "page 1 unique_jobs: 50"), "unique jobs is 50");
    expect(logs.some((l) => l === "page 1 new_unique: +50"), "+50 not +57");
    expect(logs.some((l) => l === "page 1 accumulated: 50"), "accumulated 50");
    // page 1 renders 50 visible anchors + 7 hidden same-page + hidden
    // previous-page ones: only the visible 50 may accumulate
    expect(logs.some((l) => l === "page 1 unique_jobs: 50"), "unique jobs is 50");
    expect(logs.some((l) => l === "page 1 new_unique: +50"), "+50 not +57");
    expect(logs.some((l) => l === "page 1 accumulated: 50"), "accumulated 50");
    console.log(failures === 0 ? "SCENARIO_ALL_PASS" : "SCENARIO_FAILED " + failures);
    process.exit(failures === 0 ? 0 : 1);
  }

  if (SCENARIO === "dup-anchors") {
    // 48 unique jobs, each with 2-3 visible duplicate anchors (128 raw):
    // raw anchor count may exceed the page size — never an error; the
    // accumulator must add exactly one target per unique code
    expect(logs.some((l) => l.startsWith("PAGE_TARGET_COUNT_INVALID")), "no invalid count");
    const r2 = results();
    expect(r2.every((x) => x.status === "SUCCESS"), "all unique jobs captured");
    const codes = new Set(r2.map((x) => x.code));
    expect(codes.size === r2.length, "accumulator adds each code exactly once");
    console.log(failures === 0 ? "SCENARIO_ALL_PASS" : "SCENARIO_FAILED " + failures);
    process.exit(failures === 0 ? 0 : 1);
  }

  if (SCENARIO === "unique-51") {
    // 51 unique jobs on one page: real page-size violation
    expect(logs.some((l) => l.startsWith("PAGE_TARGET_COUNT_INVALID page 1")),
      "51 unique jobs reported invalid");
    expect(!logs.some((l) => l.startsWith("page 1 new_unique:")), "no polluted accumulation");
    console.log(failures === 0 ? "SCENARIO_ALL_PASS" : "SCENARIO_FAILED " + failures);
    process.exit(failures === 0 ? 0 : 1);
  }

  if (SCENARIO === "count-invalid") {
    // visible anchors exceed the official page size: never accumulate
    expect(logs.some((l) => l.startsWith("PAGE_TARGET_COUNT_INVALID page 1")),
      "count invalid reported");
    expect(!logs.some((l) => l.startsWith("page 1 new_unique:")), "no polluted accumulation");
    console.log(failures === 0 ? "SCENARIO_ALL_PASS" : "SCENARIO_FAILED " + failures);
    process.exit(failures === 0 ? 0 : 1);
  }

  if (SCENARIO === "detail-worker") {
    // C: substantive bonus lines must never appear inside requirements
    expect(!r.some((x) => (x.requirements || []).join(" ").includes("加分项")),
      "requirements never contain the bonus section");
    // detail page text is audit-only: no canonical title override field
    expect(r.every((x) => x.detail_page_title && !("title" in x)),
      "detail title recorded as audit-only detail_page_title");
    if (process.env.DUMP_RESULTS) {
      console.log("RESULTS " + JSON.stringify(r));
      console.log("NAVIGATIONS " + state.workerNavigations + " OPENS " + state.workerOpens);
    }
    expect(startDetails === true, "user action started the detail worker");
    expect(state.workerOpens === 1, "worker window created exactly once");
    expect(r.length === TOTAL_PAGES * PAGE_SIZE,
      "one result per accumulated detail target");
    expect(state.workerNavigations === TOTAL_PAGES * PAGE_SIZE - 1,
      "worker navigated sequentially via assign");
    const allOk = r.every((x) => x.status === "SUCCESS" && x.code &&
      x.detail_url && x.body_text && x.full_jd);
    expect(allOk, "all detail results successful with JD text");
    const codes = new Set(r.map((x) => x.code));
    expect(codes.size === r.length, "no duplicate jobs in the detail queue");
    expect(!r.some((x) => x.failure_code === "DETAIL_IDENTITY_MISMATCH"),
      "no identity mismatches");
    expect(logs.some((l) => l.startsWith("JD 详情：")), "progress output");
    console.log(failures === 0 ? "SCENARIO_ALL_PASS" : "SCENARIO_FAILED " + failures);
    process.exit(failures === 0 ? 0 : 1);
  }

  if (SCENARIO === "worker-blocked") {
    expect(startDetails === false, "start aborted when the popup is blocked");
    expect(logs.some((l) => l === "DETAIL_WORKER_BLOCKED"), "popup blocked reported");
    expect(state.workerOpens === 0, "no worker opened");
    // allow popups and retry: same queue state, no duplication
    state.popupBlocked = false;
    const second = await windowStub.jobHelperStartDetails();
    while (!logs.some((l) => l.startsWith("detail capture complete")) && guard++ < 20000) {
      await pump();
    }
    expect(second === true, "retry after unblocking succeeds");
    expect(state.workerOpens === 1, "still exactly one worker window");
    console.log(failures === 0 ? "SCENARIO_ALL_PASS" : "SCENARIO_FAILED " + failures);
    process.exit(failures === 0 ? 0 : 1);
  }

  if (SCENARIO === "worker-closed") {
    // the user closes the worker mid-run, exactly once (injected into the
    // helper source above); the helper must reopen and finish the queue
    guard = 0;
    while (!logs.some((l) => l.startsWith("detail capture complete")) && guard++ < 60000) {
      await pump();
    }
    // self-healing: the helper detects the closed worker, reopens it and
    // continues the queue without losing state
    expect(state.workerOpens === 2, "worker reopened exactly once after close");
    expect(r.every((x) => x.status === "SUCCESS"), "queue completed after the close");
    console.log(failures === 0 ? "SCENARIO_ALL_PASS" : "SCENARIO_FAILED " + failures);
    process.exit(failures === 0 ? 0 : 1);
  }

  if (SCENARIO === "identity-mismatch") {
    // the site lands the first job's worker on a DIFFERENT posting: the
    // helper must reject the merge and keep processing the queue
    const firstCode = (jobsFor(LAST_PAGE)[0].href.match(/code=([^&]+)/) || [])[1];
    guard = 0;
    while (!logs.some((l) => l.startsWith("detail capture complete")) && guard++ < 20000) {
      await pump();
    }
    const r2 = results();
    expect(!r2.some((x) => x.status === "SUCCESS" && x.code === firstCode),
      "wrong landing never merged");
    expect(r2.some((x) => x.code === firstCode && x.status === "FAILED"),
      "mismatch recorded as failed");
    expect(r2.some((x) => x.status === "SUCCESS" && x.code !== firstCode),
      "batch continued after the mismatch");
    console.log(failures === 0 ? "SCENARIO_ALL_PASS" : "SCENARIO_FAILED " + failures);
    process.exit(failures === 0 ? 0 : 1);
  }

  console.log(failures === 0 ? "SCENARIO_ALL_PASS" : "SCENARIO_FAILED " + failures);
  process.exit(failures === 0 ? 0 : 1);
}
main();
