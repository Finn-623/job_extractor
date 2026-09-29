/* PDD N5.5 Adaptive Pagination + Human-in-the-loop Resume — Safari Console.
 *
 * Operates ONLY the official page DOM: verifies the official page-size
 * changer already shows the largest official option (50), otherwise opens
 * the site's own size dropdown and clicks that option, then clicks the
 * official next-page control until the last page. Every list request is
 * produced by the page itself — no fetch/XHR, no replay, no body,
 * token, anti_content or cookie access, no captcha handling.
 * Stops on the disabled next control or after 100 pages.
 *
 * N5.4: adaptive settling — after the active page number changes, the
 * visible job list (links matching '/jobs/detail?code=' that are actually
 * rendered, offsetParent != null) must be non-empty, clearly different
 * from the previous page, and stable across two consecutive samples.
 *
 * N5.5: when the visible list never stabilizes, the page is treated as
 * "not confirmed loaded" (e.g. PDD's own risk-control popup). Automation
 * pauses immediately (no further next-page clicks) and waits up to 120s
 * for the USER to complete PDD's own verification manually. It resumes
 * only once the current page's job data has re-rendered stably. The
 * script never touches, submits, bypasses or reads anything from the
 * verification flow itself.
 *
 * Real PDD DOM uses the rocket-* component library (not ant-*). ant-*
 * selectors are kept only as a compatibility fallback.
 */
(async () => {
  "use strict";
  const MAX_PAGES = 100;
  const INITIAL_SETTLE_MS = 2000;
  const MIN_SETTLE_MS = 1000;
  const MAX_SETTLE_MS = 6000;
  const STABILITY_POLL_MS = 400;
  const BATCH_PAUSE_MS = 3000;
  const BATCH_SIZE = 5;
  const CLICK_WAIT_MS = 2000;
  const PAGE_CHANGE_TIMEOUT_MS = 10000;
  const HUMAN_WAIT_TIMEOUT_MS = 120000;
  const HUMAN_POLL_MS = 500;
  const HUMAN_LOG_INTERVAL_MS = 10000;

  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const parseIntText = (el) => {
    if (!el) return NaN;
    return parseInt((el.textContent || "").trim(), 10);
  };
  const activePage = () => {
    // rocket-* is the real PDD component library; antd kept as fallback
    const el = document.querySelector(".rocket-pagination-item-active") ||
      document.querySelector(".ant-pagination-item-active");
    const value = parseIntText(el);
    return Number.isNaN(value) ? null : value;
  };
  const nextDisabled = () => {
    const next = document.querySelector(".rocket-pagination-next") ||
      document.querySelector(".ant-pagination-next");
    if (!next) return true;
    if (next.classList.contains("rocket-pagination-disabled")) return true;
    if (next.classList.contains("ant-pagination-disabled")) return true;
    if (next.getAttribute("aria-disabled") === "true") return true;
    return false;
  };
  // Only links that are actually rendered on the current page count as
  // "visible" — hidden/cached nodes must not pollute the stability signal.
  const visibleIds = () => {
    const ids = new Set();
    document.querySelectorAll("a[href*='/jobs/detail?code=']").forEach((a) => {
      if (a.offsetParent !== null || a.getClientRects().length > 0) {
        ids.add(a.href);
      }
    });
    return ids;
  };
  const sameSet = (a, b) =>
    a.size === b.size && [...a].every((v) => b.has(v));

  console.log("PDD pagination helper started");

  // ---- Step 1: page size via the official size changer (DOM only) ----
  try {
    const sizeChanger = document.querySelector(
      ".rocket-pagination-options-size-changer");
    const selected = document.querySelector(
      ".rocket-pagination-options-size-changer .rocket-select-selection-item");
    const current = parseIntText(selected);
    if (!Number.isNaN(current)) {
      console.log("page size already max: " + current);
    } else {
      const changer = sizeChanger ||
        document.querySelector(".ant-pagination-options .ant-select");
      if (!changer) throw new Error("no size changer");
      changer.click();
      await sleep(600);
      // rocket renders options in a dropdown root; collect visible ones
      const options = [...document.querySelectorAll(
        ".rocket-select-item-option:not(.rocket-select-item-option-disabled)")]
        .filter((el) => el.offsetParent !== null);
      const sizes = options.map(parseIntText).filter((v) => !Number.isNaN(v));
      if (!sizes.length) throw new Error("no size options");
      const max = Math.max(...sizes);
      const target = options.find((el) => parseIntText(el) === max);
      if (!target) throw new Error("max option not found");
      target.click();
      // wait for the size change to settle (>= 2s) before paginating
      await sleep(CLICK_WAIT_MS);
      console.log("page size changed to: " + max);
    }
  } catch (error) {
    console.log("PAGE_SIZE_UI_NOT_AVAILABLE");
  }

  // ---- Human-in-the-loop recovery ----
  // Waits for the USER to complete PDD's own verification. Resumes only
  // when the expected page is still active and its job list has
  // re-rendered stably (non-empty, different from the previous page,
  // identical across two consecutive samples).
  const waitForHumanRecovery = async (expectedPage, prevIds) => {
    const waitStart = Date.now();
    let lastLog = Date.now();
    let lastSample = null;
    while (Date.now() - waitStart < HUMAN_WAIT_TIMEOUT_MS) {
      await sleep(HUMAN_POLL_MS);
      if (Date.now() - lastLog >= HUMAN_LOG_INTERVAL_MS) {
        console.log("waiting for page recovery... " +
          Math.round((Date.now() - waitStart) / 1000) + "s");
        lastLog = Date.now();
      }
      if (activePage() !== expectedPage) {
        lastSample = null;
        continue;
      }
      const ids = visibleIds();
      if (ids.size === 0 || sameSet(ids, prevIds)) {
        lastSample = null;
        continue;
      }
      if (lastSample && sameSet(ids, lastSample)) return true;
      lastSample = ids;
    }
    return false;
  };

  // ---- Step 2: adaptive pagination via the official next control ----
  let pagesVisited = 0;
  let stuckAt = null;
  const runStart = Date.now();
  while (pagesVisited < MAX_PAGES) {
    const pageNo = activePage();
    if (pageNo === null) {
      console.log("PAGE_NUMBER_NOT_DETECTED");
      break;
    }
    if (pageNo === stuckAt) {
      console.log("page did not advance, stopping at page " + pageNo);
      break;
    }
    pagesVisited += 1;
    console.log("page " + pageNo + " ready");
    // short initial settle so Page 1's list request reaches the Network layer
    if (pagesVisited === 1) {
      await sleep(INITIAL_SETTLE_MS);
      console.log("initial settle complete");
    }
    if (nextDisabled()) {
      console.log("last page reached");
      break;
    }
    stuckAt = pageNo;
    const prevIds = visibleIds();
    const next = document.querySelector(".rocket-pagination-next") ||
      document.querySelector(".ant-pagination-next");
    if (!next) {
      console.log("NEXT_CONTROL_NOT_FOUND");
      break;
    }
    const trigger = next.querySelector("a") || next;
    trigger.click();

    // wait for the active page number to change
    const startedAt = Date.now();
    let changed = false;
    while (Date.now() - startedAt < PAGE_CHANGE_TIMEOUT_MS) {
      await sleep(300);
      const now = activePage();
      if (now !== null && now !== pageNo) {
        stuckAt = null;
        changed = true;
        break;
      }
    }
    if (!changed) {
      await sleep(CLICK_WAIT_MS);
      continue;
    }

    // adaptive settle: after activePage changes, wait MIN_SETTLE_MS, then
    // poll the visible list; continue as soon as it is non-empty, clearly
    // different from the previous page, and stable across two samples
    let settled = false;
    const settleStart = Date.now();
    await sleep(MIN_SETTLE_MS);
    while (Date.now() - settleStart < MAX_SETTLE_MS) {
      await sleep(STABILITY_POLL_MS);
      const ids = visibleIds();
      if (ids.size > 0 && !sameSet(ids, prevIds)) {
        const again = visibleIds();
        if (sameSet(ids, again)) {
          settled = true;
          break;
        }
      }
    }
    if (settled) {
      const settleSeconds = ((Date.now() - settleStart) / 1000).toFixed(1);
      console.log("settled in " + settleSeconds + "s");
    } else {
      // one stability timeout means the current page's job data is NOT
      // confirmed: never auto-click next. Pause for human verification.
      console.log("PAGE_STABILITY_TIMEOUT page " + pageNo);
      console.log("PDD verification may be required.");
      console.log("Please complete the verification in the page. " +
        "Automation is paused.");
      const recovered = await waitForHumanRecovery(pageNo, prevIds);
      if (recovered) {
        console.log("verification/page recovered");
        console.log("resuming automation");
        stuckAt = null;
      } else {
        console.log("HUMAN_VERIFICATION_TIMEOUT");
        console.log("automation stopped at page " + pageNo);
        break;
      }
    }

    // light deterministic throttle: pause briefly after every BATCH_SIZE
    // pages to avoid the 0.3-0.4s/page cadence that triggered 54001
    if (pagesVisited % BATCH_SIZE === 0) {
      console.log("batch pause after page " + pagesVisited);
      await sleep(BATCH_PAUSE_MS);
    }
  }
  console.log("total pages visited: " + pagesVisited);
  console.log("total elapsed: " + ((Date.now() - runStart) / 1000).toFixed(1) + "s");
  console.log("done");
})();
