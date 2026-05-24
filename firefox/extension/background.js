"use strict";

importScripts("config.js");

const CC = globalThis.SanaGinxConfig;
const PAIR_WINDOW_MS = 120000;
const { STORAGE } = CC;

let lastPageTitle = null;
let activeConfig = null;
let licenseListener = null;
let manifestListener = null;

async function ensureConfig() {
  if (!activeConfig) activeConfig = await CC.loadConfig();
  return activeConfig;
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

function tryPairCapture(mpdUrl, licenseRecord, name, config) {
  const cap = config.capture;
  const token =
    licenseRecord.token ||
    CC.readTokenFromHeaders(licenseRecord.licenseHeaders, cap.licenseHeader);
  const hasAuth =
    (token && String(token).trim()) ||
    (licenseRecord.licenseHeaders && Object.keys(licenseRecord.licenseHeaders).length > 0);
  if (!mpdUrl || !licenseRecord || !licenseRecord.url || !hasAuth) return false;
  if (Date.now() - licenseRecord.time > PAIR_WINDOW_MS) return false;

  const pageH = CC.buildPageHeaders(config, licenseRecord.tabUrl || "");
  const licenseHeaders = Object.assign(
    {},
    config.capture.defaultLicenseHeaders || { "Content-Type": "application/octet-stream" },
    pageH,
    licenseRecord.licenseHeaders || {}
  );
  if (cap.licenseHeader && token) licenseHeaders[cap.licenseHeader] = token;

  const capture = {
    mpd_url: mpdUrl,
    license_url: licenseRecord.url,
    token,
    license_headers: licenseHeaders,
    mpd_headers: Object.assign({}, pageH, config.capture.defaultMpdHeaders || {}),
    name: name || getNameFromMpdUrl(mpdUrl),
    time: Date.now(),
  };
  browser.storage.local.set({ [STORAGE.capture]: capture });
  browser.storage.local.remove([STORAGE.lastLicense, STORAGE.lastMpd]);
  return true;
}

function pairLicenseWithWaitingMpd(license, config) {
  browser.storage.local.get([STORAGE.lastMpd]).then((data) => {
    const m = data[STORAGE.lastMpd];
    if (!m || !m.url || Date.now() - m.time > PAIR_WINDOW_MS) return;
    tryPairCapture(m.url, license, m.name, config);
  });
}

function registerWebRequestListeners(config) {
  if (licenseListener) {
    browser.webRequest.onBeforeSendHeaders.removeListener(licenseListener);
    licenseListener = null;
  }
  if (manifestListener) {
    browser.webRequest.onBeforeRequest.removeListener(manifestListener);
    manifestListener = null;
  }

  const cap = config.capture;
  const licenseHosts = cap.licenseWebRequestHosts || ["*://*/*"];
  const manifestHosts = cap.manifestWebRequestHosts || ["*://*/*"];
  const licenseHeaderName = cap.licenseHeader || "";

  licenseListener = (details) => {
    const url = details.url || "";
    if (!CC.isLicenseUrl(url, cap)) return;
    const licenseHeaders = {};
    let token = "";
    for (const h of details.requestHeaders || []) {
      licenseHeaders[h.name] = h.value;
      if (licenseHeaderName && h.name.toLowerCase() === licenseHeaderName.toLowerCase()) {
        token = (h.value || "").trim();
      }
    }
    if (!token && licenseHeaderName && !(Object.keys(licenseHeaders).length > 1)) return;
    const license = {
      url,
      token,
      licenseHeaders,
      tabUrl: details.initiator || "",
      time: Date.now(),
    };
    browser.storage.local.set({ [STORAGE.lastLicense]: license });
    pairLicenseWithWaitingMpd(license, config);
  };

  manifestListener = (details) => {
    if (details.method !== "GET") return;
    const url = details.url || "";
    const mpdUrl = CC.resolveManifestUrl(url, cap);
    if (!mpdUrl) return;

    browser.storage.local.get([STORAGE.lastLicense]).then((data) => {
      const last = data[STORAGE.lastLicense];
      const name = getNameFromMpdUrl(mpdUrl);
      if (last && Date.now() - last.time <= PAIR_WINDOW_MS) {
        tryPairCapture(mpdUrl, last, name, config);
      } else {
        browser.storage.local.set({
          [STORAGE.lastMpd]: { url: mpdUrl, name, time: Date.now() },
        });
      }
    });
  };

  browser.webRequest.onBeforeSendHeaders.addListener(licenseListener, { urls: licenseHosts }, [
    "requestHeaders",
  ]);
  browser.webRequest.onBeforeRequest.addListener(manifestListener, { urls: manifestHosts });
}

async function init() {
  await CC.migrateLegacyStorage();
  activeConfig = await CC.loadConfig();
  registerWebRequestListeners(activeConfig);
}

browser.storage.onChanged.addListener((changes, area) => {
  if (area !== "local" || !changes[CC.CONFIG_KEY]) return;
  CC.loadConfig().then((cfg) => {
    activeConfig = cfg;
    registerWebRequestListeners(cfg);
  });
});

init();

browser.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.type === "GET_CONFIG") {
    ensureConfig().then((cfg) => sendResponse(cfg));
    return true;
  }
  if (msg.type === "CAPTURE_LICENSE_FROM_PAGE") {
    ensureConfig().then((config) => {
      const license = {
        url: msg.licenseUrl,
        token: msg.token || "",
        licenseHeaders: msg.licenseHeaders || {},
        tabUrl: sender.tab && sender.tab.url ? sender.tab.url : "",
        time: Date.now(),
      };
      browser.storage.local.set({ [STORAGE.lastLicense]: license });
      pairLicenseWithWaitingMpd(license, config);
      sendResponse(true);
    });
    return true;
  }
  if (msg.type === "CAPTURE_PAGE_TITLE") {
    if (msg.title && typeof msg.title === "string") lastPageTitle = msg.title.trim().substring(0, 80);
    sendResponse(true);
    return false;
  }
  if (msg.type === "CAPTURE_MPD_FROM_PAGE") {
    ensureConfig().then((config) => {
      const mpdUrl = msg.mpdUrl;
      const name = (lastPageTitle || msg.name || getNameFromMpdUrl(mpdUrl)).replace(/[<>:"/\\|?*]/g, "_");
      browser.storage.local.get([STORAGE.lastLicense]).then((data) => {
        const last = data[STORAGE.lastLicense];
        if (last && Date.now() - last.time <= PAIR_WINDOW_MS) {
          tryPairCapture(mpdUrl, last, name, config);
        } else {
          browser.storage.local.set({
            [STORAGE.lastMpd]: { url: mpdUrl, name, time: Date.now() },
          });
        }
        sendResponse(true);
      });
    });
    return true;
  }
  if (msg.type === "GET_CAPTURE") {
    browser.storage.local.get([STORAGE.capture]).then((data) => sendResponse(data[STORAGE.capture] || null));
    return true;
  }
  if (msg.type === "CLEAR_CAPTURE") {
    lastPageTitle = null;
    browser.storage.local
      .remove([STORAGE.capture, STORAGE.lastLicense, STORAGE.lastMpd])
      .then(() => sendResponse(true));
    return true;
  }
  return false;
});
