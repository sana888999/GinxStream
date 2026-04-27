"use strict";

const PAIR_WINDOW_MS = 120000; // 2 min
const STORAGE_KEYS = { capture: "skool_capture", lastLicense: "skool_last_license" };
let lastPageTitle = null; // from main frame (e.g. "Editing Skool Walkthrough - Breakdown")

function deriveMpdFromSegment(url) {
  try {
    const u = new URL(url);
    const path = u.pathname.replace(/\/$/, "");
    if (!path.includes("/assets/")) return null;
    if (!path.includes("video_") && !path.includes("audio_") && !path.includes("seg-") && !url.includes(".mpd"))
      return null;
    const parts = path.split("/");
    const i = parts.indexOf("assets");
    if (i === -1 || i + 1 >= parts.length) return null;
    const assetId = parts[i + 1];
    if (!assetId || assetId.length < 4) return null;
    return `${u.protocol}//${u.host}/assets/${assetId}/master.mpd`;
  } catch (e) {
    return null;
  }
}

function getNameFromMpdUrl(url) {
  try {
    const path = new URL(url).pathname.replace(/\/$/, "");
    const name = path.split("/").pop() || "";
    return (name.replace(".mpd", "") || path.split("/").slice(-2, -1)[0] || "video").substring(0, 80);
  } catch (e) {
    return "video";
  }
}

// License request: capture URL and pallycon-customdata-v2
browser.webRequest.onBeforeSendHeaders.addListener(
  (details) => {
    const url = details.url || "";
    if (!url.toLowerCase().includes("licensemanager.do") || !url.toLowerCase().includes("pallycon")) return;
    let token = "";
    for (const h of details.requestHeaders || []) {
      if (h.name.toLowerCase() === "pallycon-customdata-v2") {
        token = (h.value || "").trim();
        break;
      }
    }
    if (!token) return;
    const license = { url, token, time: Date.now() };
    browser.storage.local.set({ [STORAGE_KEYS.lastLicense]: license });
  },
  { urls: ["*://*pallycon*/*licenseManager*"] },
  ["requestHeaders"]
);

// MPD or segment: derive MPD, pair with last license if present
browser.webRequest.onBeforeRequest.addListener(
  (details) => {
    if (details.method !== "GET") return;
    const url = details.url || "";
    let mpdUrl = url.includes(".mpd") ? url : deriveMpdFromSegment(url);
    if (!mpdUrl) return;
    // Only tagmango CDN (not api-prod-new)
    if (!url.includes("tagmango")) return;
    if (url.includes("api-prod-new")) return;

    browser.storage.local.get([STORAGE_KEYS.lastLicense]).then((data) => {
      const last = data[STORAGE_KEYS.lastLicense];
      if (!last || Date.now() - last.time > PAIR_WINDOW_MS) return;
      const name = getNameFromMpdUrl(mpdUrl);
      const capture = {
        mpd_url: mpdUrl,
        license_url: last.url,
        token: last.token,
        name,
        time: Date.now(),
      };
      browser.storage.local.set({
        [STORAGE_KEYS.capture]: capture,
        [STORAGE_KEYS.lastLicense]: null,
      });
      browser.storage.local.remove(STORAGE_KEYS.lastLicense);
    });
  },
  { urls: ["*://*tagmango*/*"] }
);

// Capture from page-injected script (reliable: page's fetch() has the token)
browser.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.type === "CAPTURE_LICENSE_FROM_PAGE") {
    const license = { url: msg.licenseUrl, token: msg.token, time: Date.now() };
    browser.storage.local.set({ [STORAGE_KEYS.lastLicense]: license });
    sendResponse(true);
    return false;
  }
  if (msg.type === "CAPTURE_PAGE_TITLE") {
    if (msg.title && typeof msg.title === "string") lastPageTitle = msg.title.trim().substring(0, 80);
    sendResponse(true);
    return false;
  }
  if (msg.type === "CAPTURE_MPD_FROM_PAGE") {
    const mpdUrl = msg.mpdUrl;
    const name = (lastPageTitle || msg.name || getNameFromMpdUrl(mpdUrl)).replace(/[<>:"/\\|?*]/g, "_");
    browser.storage.local.get([STORAGE_KEYS.lastLicense]).then((data) => {
      const last = data[STORAGE_KEYS.lastLicense];
      if (last && Date.now() - last.time <= PAIR_WINDOW_MS) {
        const capture = { mpd_url: mpdUrl, license_url: last.url, token: last.token, name, time: Date.now() };
        browser.storage.local.set({ [STORAGE_KEYS.capture]: capture });
        browser.storage.local.remove(STORAGE_KEYS.lastLicense);
      }
    });
    sendResponse(true);
    return false;
  }
  if (msg.type === "GET_CAPTURE") {
    browser.storage.local.get([STORAGE_KEYS.capture]).then((data) => sendResponse(data[STORAGE_KEYS.capture] || null));
    return true;
  }
  if (msg.type === "CLEAR_CAPTURE") {
    lastPageTitle = null;
    browser.storage.local.remove([STORAGE_KEYS.capture, STORAGE_KEYS.lastLicense]).then(() => sendResponse(true));
    return true;
  }
  return false;
});
