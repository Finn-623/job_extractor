// N5.6 helper human-resume tests — fake rocket-* DOM + virtual clock.
// Usage: node n86_helper_resume_test.js
"use strict";
const fs = require("fs");
const path = require("path");

const helperSource = fs.readFileSync(
  path.join(__dirname, "..", "..", "scripts", "pdd_browser_console_helper.js"),
  "utf8");

const FAIL_PAGE = 10;
const PAGE_SIZE = 50;
const SCENARIO = process.argv[2] || "resume-success";
if (SCENARIO === "single-page") { /* handled below via TOTAL_PAGES */ }
// which pages fail their list request until the user completes verification
const TOTAL_PAGES = SCENARIO === "single-page" ? 1 : 16;
const VERIFIED_PAGES = (SCENARIO === "resume-success" || SCENARIO === "retry-failure")
  ? { [FAIL_PAGE]: true }   // only page 10 needs verification (real PDD case)
  : (SCENARIO === "warmup-verification" ? { 2: true, 10: true } : {});

// ---- virtual clock: every sleep advances the clock and runs inline ----
let vnow = 0;
const virtualDate = { now: () => vnow };

// ---- fake DOM ----
const state = {
  active: 1,
  list: 1,
  verified: false,
  clicks: [],
};

function jobLink(page, index) {
  const code = "T" + String(page).padStart(2, "0") + String(index).padStart(3, "0");
  return {
    textContent: "岗位 " + code,
    href: "https://careers.example.com/jobs/detail?code=" + code + "&lang=zh",
    offsetParent: {},
    getClientRects: () => [{}],
  };
}

function jobsFor(page) {
  const out = [];
  for (let i = 0; i < (page === LAST_PAGE ? 42 : PAGE_SIZE); i++) {
    out.push(jobLink(page, i));
  }
  return out;
}
const LAST_PAGE = TOTAL_PAGES;

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

function goToPage(page) {
  // the site's own pagination click: it always advances the active page,
  // but the list request itself fails (54001) until the user completes
  // the site's own verification — then the request succeeds.
  state.clicks.push(page);
  state.active = page;
  if (VERIFIED_PAGES[page] && !state.verified) {
    state.listRequestFailed = true;   // success=false, errorCode=54001
    return;                            // list keeps showing the previous page
  }
  state.listRequestFailed = false;
  state.list = page;
}

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

// ---- sandboxed evaluation with the virtual clock ----
const sandboxFactory = new Function(
  "window", "document", "console", "setTimeout", "Date",
  helperSource);
global.setTimeout = (cb, ms) => { vnow += Math.max(ms, 1); cb(); return 0; };
sandboxFactory(windowStub, documentStub, consoleStub, global.setTimeout,
  { now: () => vnow });

const text = () => "\n" + logs.join("\n") + "\n";
let failures = 0;
function expect(condition, label) {
  if (condition) { console.log("PASS " + label); }
  else { console.log("FAIL " + label); failures += 1; }
}
const pump = async () => new Promise((resolve) => setImmediate(resolve));

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
  // ---- scenario 1: stability timeout pauses automation (no next click) ----
  let guard = 0;
  while (!logs.some((l) => l.includes("Automation is paused")) && guard++ < 300) {
    await pump();
  }
  if (SCENARIO !== "warmup-verification") {
    expect(logs.some((l) => l === "PAGE_STABILITY_TIMEOUT page " + (FAIL_PAGE - 1)),
      "stability timeout reported at page " + (FAIL_PAGE - 1));
  }
  expect(logs.some((l) => l.includes("Automation is paused")), "paused message");
  expect(logs.some((l) => l.includes("jobHelperResume()")), "resume instructions shown");
  // ---- no resume: stays paused ----
  const readyPages = logs.filter((l) => l.startsWith("page ") && l.endsWith(" ready")).length;
  if (SCENARIO !== "warmup-verification") {
    expect(readyPages === FAIL_PAGE - 1, "no pages advanced past the failing page while paused");
  }
  await pump(); await pump();
  if (SCENARIO !== "warmup-verification") {
    expect(logs.filter((l) => l.startsWith("page ") && l.endsWith(" ready")).length ===
      FAIL_PAGE - 1, "still paused without a resume");
  }

  if (SCENARIO === "resume-success" || SCENARIO === "warmup-verification") {
    // wait for the pagination pause (page 10 in resume-success; page 2
    // during WARMUP in warmup-verification)
    if (SCENARIO === "warmup-verification") {
      // the FIRST pause must happen during warmup, before any "page N ready"
      expect(logs.some((l) => l === "WARMUP_PAGE_UNSTABLE page 2"),
        "warmup verification entered the human resume flow");
      expect(logs.some((l) => l.includes("Automation is paused")), "warmup paused");
      expect(logs.filter((l) => l.startsWith("page ") && l.endsWith(" ready")).length === 0,
        "no formal pagination before warmup completes");
    }
    // ---- user completes the site's verification, then explicitly resumes ----
    state.verified = true;
    expect(windowStub.jobHelperResume() === true, "jobHelperResume accepts the resume");
  // helper continues under the virtual clock until the run ends
  guard = 0;
  while (!logs.some((l) => l === "done") && guard++ < 5000) { await pump(); }

  // ---- scenario 3/4: resume retried the expected page; automation continued ----
  expect(logs.some((l) => l === "resume requested"), "explicit resume logged");
  expect(logs.some((l) => l.startsWith("stepping back to page ")), "previous→current retry step");
  expect(logs.some((l) => l === "verification/page recovered"), "page recovered after resume");
  expect(logs.some((l) => l === "resuming automation"), "automation resumed");
  if (SCENARIO === "warmup-verification") {
    expect(logs.some((l) => l === "verification/page recovered"),
      "verification/page recovered logged for warmup resume");
    expect(logs.some((l) => l === "resuming automation"),
      "resuming automation logged for warmup resume");
  }
  expect(logs.some((l) => l === "last page reached"), "pagination continued to the last page");
  expect(logs.some((l) => l === "total pages visited: 16"), "all 16 pages visited");
  expect(logs.some((l) => l === "done"), "helper completed");
  expect(state.list === 16, "final rendered page is the last page");
  // warmup assertions (spec: 2->1 round-trip recreates the Page 1 request,
  // pagesVisited untouched)
  expect(logs.some((l) => l === "refreshing page 1 for HAR capture (page 1 -> 2 -> 1)"),
    "warmup round-trip logged");
  expect(logs.some((l) => l === "initial page refreshed for HAR capture"),
    "initial page refreshed");
  expect(logs.some((l) => l === "total pages visited: 16"), "pagesVisited not affected by warmup");
  const firstPageReadyIndex = logs.findIndex((l) => l === "page 1 ready");
  const warmupClicks = state.clicks.slice(0, firstPageReadyIndex < 0 ? 99 : 99);
  expect(warmupClicks.includes(2) && warmupClicks.includes(1) &&
    warmupClicks.indexOf(2) < warmupClicks.indexOf(1) &&
    warmupClicks.indexOf(1) < firstPageReadyIndex,
    "page 1 request recreated before formal pagination");

    console.log(failures === 0 ? "SCENARIO_ALL_PASS" : "SCENARIO_FAILED " + failures);
    process.exit(failures === 0 ? 0 : 1);
  }
  if (SCENARIO === "retry-failure") {
    // ---- user resumes WITHOUT completing verification: retry fails again ----
    expect(windowStub.jobHelperResume() === true, "jobHelperResume accepted");
    // recovery timeout fires (virtual clock); the helper must NOT auto-stop:
    // it waits for another explicit resume
    while (!logs.some((l) => l === "PAGE_RECOVERY_TIMEOUT page " + FAIL_PAGE) &&
           guard++ < 2000) { await pump(); }
    expect(logs.some((l) => l === "PAGE_RECOVERY_TIMEOUT page " + FAIL_PAGE),
      "retry failure reported");
    expect(logs.filter((l) => l.includes("jobHelperResume() 重试")).length >= 1,
      "re-resume or stop instructions shown");
    const afterFailure = logs.filter((l) => l.startsWith("page ") && l.endsWith(" ready")).length;
    await pump(); await pump();
    expect(logs.filter((l) => l.startsWith("page ") && l.endsWith(" ready")).length ===
      afterFailure, "still waiting for an explicit resume after failure");
    // ---- user stops cleanly via jobHelperStop ----
    expect(windowStub.jobHelperStop() === true, "jobHelperStop stops the run");
    while (!logs.some((l) => l.startsWith("automation stopped")) && guard++ < 5000) { await pump(); }
    expect(logs.some((l) => l === "automation stopped at page " + FAIL_PAGE),
      "clean stop without verification");
    expect(logs.some((l) => l === "done"), "helper completed after stop");
    console.log(failures === 0 ? "SCENARIO_ALL_PASS" : "SCENARIO_FAILED " + failures);
    process.exit(failures === 0 ? 0 : 1);
  }
}
main();
