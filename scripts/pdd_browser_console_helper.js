/* PDD N5.6 Adaptive Pagination + Human Resume — Safari/Chrome Console.
 *
 * Operates ONLY the official page DOM: verifies the official page-size
 * changer already shows the largest official option (50), otherwise opens
 * the site's own size dropdown and clicks that option, then clicks the
 * official next-page control until the last page. Every list request is
 * produced by the page itself — no fetch/XHR, no replay, no body,
 * token, anti_content or cookie access, no captcha handling.
 * Stops on the disabled next control or after 100 pages.
 *
 * Adaptive settling (N5.4): after the active page number changes, the
 * visible job list (links matching '/jobs/detail?code=' that are actually
 * rendered, offsetParent != null) must be non-empty, clearly different
 * from the previous page, and stable across two consecutive samples.
 *
 * Human resume (N5.5/N5.6): when the visible list never stabilizes, the
 * page is treated as "not confirmed loaded" (e.g. a risk-control popup).
 * Automation pauses immediately (no further next-page clicks) and prints
 * resume instructions. The user completes the site's own verification
 * manually — in their own time, with no pause deadline — and then calls
 * window.jobHelperResume() in the Console. The helper then re-triggers
 * the current page through normal DOM pagination only (the page item
 * itself, or one previous→current step when a same-page click would not
 * re-fire the request) and waits up to 120s for the page to stabilize.
 * A retry that fails may be attempted again with jobHelperResume(), or
 * stopped cleanly with window.jobHelperStop(). The script never touches,
 * submits, bypasses or reads anything from the verification flow itself.
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
  // Recovery budget starts only AFTER the user explicitly resumes; the
  // human verification stage itself never times out.
  const RECOVERY_TIMEOUT_MS = 120000;
  const RECOVERY_POLL_MS = 500;
  const RECOVERY_LOG_INTERVAL_MS = 10000;

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
  const stableSample = (sampleFn, comparePrev) => {
    const first = sampleFn();
    if (comparePrev && sameSet(first, comparePrev)) return null;
    const second = sampleFn();
    return sameSet(first, second) ? first : null;
  };

  // ---- explicit user resume control ----
  let resumeResolver = null;
  window.jobHelperResume = () => {
    if (resumeResolver) {
      const resolve = resumeResolver;
      resumeResolver = null;
      console.log("resume requested");
      resolve(true);
      return true;
    }
    console.log("no paused automation to resume");
    return false;
  };
  window.jobHelperStop = () => {
    if (resumeResolver) {
      const resolve = resumeResolver;
      resumeResolver = null;
      resolve(false);
    }
    return true;
  };

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

  // ---- normal-DOM page item click (re-trigger a page after resume) ----
  const clickPageItem = (pageNo) => {
    const item = document.querySelector(
      `.rocket-pagination-item-${pageNo}`) ||
      document.querySelector(`.ant-pagination-item-${pageNo}`) ||
      [...document.querySelectorAll(
        ".rocket-pagination-item,.ant-pagination-item")]
        .find((el) => parseIntText(el) === pageNo);
    if (!item) return false;
    const trigger = item.querySelector("a") || item;
    trigger.click();
    return true;
  };
  const waitForActivePage = async (target, timeoutMs) => {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      await sleep(300);
      if (activePage() === target) return true;
    }
    return false;
  };
  // Re-trigger the expected page with normal UI clicks only. When the page
  // is already active (its own item may not re-fire the request), step one
  // page back and return — exactly one previous→expected pass per resume.
  const retryPageRequest = async (expectedPage) => {
    if (activePage() === expectedPage) {
      const prev = expectedPage - 1;
      if (prev >= 1 && clickPageItem(prev)) {
        console.log("stepping back to page " + prev + " to re-trigger page " +
          expectedPage);
        const steppedBack = await waitForActivePage(prev, PAGE_CHANGE_TIMEOUT_MS);
        if (steppedBack && clickPageItem(expectedPage)) {
          console.log("re-triggering page " + expectedPage);
          return true;
        }
      }
      return false;
    }
    if (clickPageItem(expectedPage)) {
      console.log("re-triggering page " + expectedPage);
      return true;
    }
    return false;
  };

  // ---- human verification pause + explicit resume ----
  // The verification stage never times out: it waits for the USER to call
  // jobHelperResume(). After an explicit resume, the page must stabilize
  // (non-empty, different from the previous page, stable twice) within
  // RECOVERY_TIMEOUT_MS; a failed retry can be attempted again.
  const waitForPageRecovery = async (expectedPage, prevIds) => {
    const waitStart = Date.now();
    let lastLog = Date.now();
    let lastSample = null;
    while (Date.now() - waitStart < RECOVERY_TIMEOUT_MS) {
      await sleep(RECOVERY_POLL_MS);
      if (Date.now() - lastLog >= RECOVERY_LOG_INTERVAL_MS + RECOVERY_POLL_MS) {
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
  const humanVerificationRecovery = async (expectedPage, prevIds) => {
    for (;;) {
      console.log("PDD verification may be required.");
      console.log("Please complete the verification in the page. " +
        "Automation is paused.");
      console.log("完成后在 Console 输入：jobHelperResume()");
      const resumed = await new Promise((resolve) => {
        resumeResolver = resolve;
      });
      if (!resumed) {
        console.log("automation stopped at page " + expectedPage);
        return false;
      }
      console.log("verifying page " + expectedPage + " after resume");
      if (!(await retryPageRequest(expectedPage))) {
        console.log("PAGE_RETRY_NOT_AVAILABLE page " + expectedPage);
        console.log("请再次完成验证后输入 jobHelperResume() 重试，" +
          "或调用 jobHelperStop() 结束。");
        continue;
      }
      const settled = await waitForPageRecovery(expectedPage, prevIds);
      if (settled) return true;
      console.log("PAGE_RECOVERY_TIMEOUT page " + expectedPage);
      console.log("请再次完成验证后输入 jobHelperResume() 重试，" +
        "或调用 jobHelperStop() 结束。");
    }
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
      // confirmed: never auto-click next. Pause for the user's manual
      // verification, then wait for an explicit jobHelperResume().
      console.log("PAGE_STABILITY_TIMEOUT page " + pageNo);
      stuckAt = null;
      // the data we still need belongs to the page the transition tried to
      // load — that is the active page number at this point
      const failedPage = activePage() ?? pageNo + 1;
      const recovered = await humanVerificationRecovery(failedPage, prevIds);
      if (recovered) {
        console.log("verification/page recovered");
        console.log("resuming automation");
        stuckAt = null;
      } else {
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
