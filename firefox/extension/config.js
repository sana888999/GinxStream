"use strict";

/** @typedef {import('./default_config.json')} DefaultConfig */

const CONFIG_KEY = "sanaginx_config";
const LEGACY_CONFIG_KEYS = ["crypter_config"];
const LEGACY_CAPTURE = "skool_capture";
const STORAGE = {
  capture: "sanaginx_capture",
  lastLicense: "sanaginx_last_license",
  lastMpd: "sanaginx_last_mpd",
};
const LEGACY_STORAGE = {
  capture: ["crypter_capture", LEGACY_CAPTURE],
  lastLicense: ["crypter_last_license", "skool_last_license"],
  lastMpd: ["crypter_last_mpd", "skool_last_mpd"],
};

function defaultConfig() {
  return {
    serverUrl: "http://127.0.0.1:47984",
    pageHeaders: {
      Origin: "",
      Referer: "",
      "User-Agent":
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:148.0) Gecko/20100101 Firefox/148.0",
    },
    capture: {
      licenseUrlIncludes: ["license"],
      licenseHeader: "pallycon-customdata-v2",
      licenseWebRequestHosts: ["*://*/*"],
      manifestWebRequestHosts: ["*://*/*"],
      mpdUrlIncludes: [".mpd"],
      segmentUrlIncludes: [],
      segmentFileHints: ["video_", "audio_", "seg-", ".mpd"],
      segmentHostExcludes: [],
      deriveMpd: { enabled: false, pathMarker: "/assets/", masterFile: "master.mpd" },
    },
  };
}

function mergeConfig(base, patch) {
  const out = JSON.parse(JSON.stringify(base));
  if (!patch || typeof patch !== "object") return out;
  if (patch.serverUrl) out.serverUrl = String(patch.serverUrl).trim();
  if (patch.pageHeaders && typeof patch.pageHeaders === "object") {
    out.pageHeaders = Object.assign({}, out.pageHeaders, patch.pageHeaders);
  }
  if (patch.capture && typeof patch.capture === "object") {
    out.capture = Object.assign({}, out.capture, patch.capture);
    if (patch.capture.deriveMpd) {
      out.capture.deriveMpd = Object.assign({}, out.capture.deriveMpd, patch.capture.deriveMpd);
    }
  }
  return out;
}

async function loadConfig() {
  const keys = [CONFIG_KEY, ...LEGACY_CONFIG_KEYS];
  const data = await browser.storage.local.get(keys);
  let cfg = data[CONFIG_KEY];
  if (!cfg || typeof cfg !== "object") {
    for (const legacy of LEGACY_CONFIG_KEYS) {
      if (data[legacy] && typeof data[legacy] === "object") {
        cfg = data[legacy];
        break;
      }
    }
  }
  if (!cfg || typeof cfg !== "object") {
    cfg = defaultConfig();
    await browser.storage.local.set({ [CONFIG_KEY]: cfg });
  }
  return mergeConfig(defaultConfig(), cfg);
}

async function saveConfig(cfg) {
  await browser.storage.local.set({ [CONFIG_KEY]: mergeConfig(defaultConfig(), cfg) });
}

async function migrateLegacyStorage() {
  const legacyKeys = [
    ...LEGACY_CONFIG_KEYS,
    ...LEGACY_STORAGE.capture,
    ...LEGACY_STORAGE.lastLicense,
    ...LEGACY_STORAGE.lastMpd,
    CONFIG_KEY,
    STORAGE.capture,
    STORAGE.lastLicense,
    STORAGE.lastMpd,
  ];
  const data = await browser.storage.local.get(legacyKeys);

  if (!data[CONFIG_KEY]) {
    for (const legacy of LEGACY_CONFIG_KEYS) {
      if (data[legacy]) {
        await browser.storage.local.set({ [CONFIG_KEY]: data[legacy] });
        break;
      }
    }
  }

  for (const [target, sources] of Object.entries(LEGACY_STORAGE)) {
    const dest = STORAGE[target];
    if (data[dest]) continue;
    for (const src of sources) {
      if (data[src]) {
        await browser.storage.local.set({ [dest]: data[src] });
        break;
      }
    }
  }
}

function urlMatchesAll(url, parts) {
  if (!url || !parts || !parts.length) return true;
  const u = String(url).toLowerCase();
  return parts.every((p) => u.includes(String(p).toLowerCase()));
}

function hostExcluded(url, excludes) {
  if (!excludes || !excludes.length) return false;
  const u = String(url).toLowerCase();
  return excludes.some((x) => u.includes(String(x).toLowerCase()));
}

function deriveMpdUrl(url, derive) {
  if (!derive || !derive.enabled) return null;
  try {
    const u = new URL(url);
    const path = u.pathname.replace(/\/$/, "");
    const marker = derive.pathMarker || "/assets/";
    if (!path.includes(marker.replace(/\/$/, ""))) return null;
    const parts = path.split("/");
    const key = marker.replace(/\//g, "").replace(/^\s+|\s+$/g, "") || "assets";
    const i = parts.indexOf(key);
    if (i === -1 || i + 1 >= parts.length) return null;
    const assetId = parts[i + 1];
    if (!assetId || assetId.length < 2) return null;
    const master = derive.masterFile || "master.mpd";
    const base = marker.endsWith("/") ? marker.slice(0, -1) : marker;
    return `${u.protocol}//${u.host}${base}/${assetId}/${master}`;
  } catch (e) {
    return null;
  }
}

function resolveManifestUrl(url, capture) {
  if (!url) return null;
  if (hostExcluded(url, capture.segmentHostExcludes)) return null;
  const mpdParts = capture.mpdUrlIncludes || [".mpd"];
  if (urlMatchesAll(url, mpdParts) && url.toLowerCase().includes(".mpd")) return url;
  const segParts = capture.segmentUrlIncludes || [];
  const hints = capture.segmentFileHints || [];
  if (segParts.length && !urlMatchesAll(url, segParts)) return null;
  if (hints.length && !hints.some((h) => url.includes(h))) return null;
  const derived = deriveMpdUrl(url, capture.deriveMpd);
  if (derived) return derived;
  if (url.toLowerCase().includes(".mpd")) return url;
  return null;
}

function isLicenseUrl(url, capture) {
  return urlMatchesAll(url, capture.licenseUrlIncludes || ["license"]);
}

function readTokenFromHeaders(headers, headerName) {
  if (!headers || !headerName) return "";
  const want = String(headerName).toLowerCase();
  if (headers instanceof Headers) return (headers.get(headerName) || "").trim();
  if (Array.isArray(headers)) {
    for (const h of headers) {
      if (h && String(h.name).toLowerCase() === want) return String(h.value || "").trim();
    }
    return "";
  }
  if (typeof headers === "object") {
    for (const k of Object.keys(headers)) {
      if (String(k).toLowerCase() === want) return String(headers[k] || "").trim();
    }
  }
  return "";
}

function buildPageHeaders(config, tabUrl) {
  const h = Object.assign({}, config.pageHeaders || {});
  if ((!h.Origin || !h.Referer) && tabUrl) {
    try {
      const u = new URL(tabUrl);
      if (!h.Origin) h.Origin = u.origin;
      if (!h.Referer) h.Referer = tabUrl;
    } catch (e) {}
  }
  return h;
}

if (typeof globalThis !== "undefined") {
  globalThis.SanaGinxConfig = {
    CONFIG_KEY,
    STORAGE,
    defaultConfig,
    mergeConfig,
    loadConfig,
    saveConfig,
    migrateLegacyStorage,
    urlMatchesAll,
    hostExcluded,
    deriveMpdUrl,
    resolveManifestUrl,
    isLicenseUrl,
    readTokenFromHeaders,
    buildPageHeaders,
  };
  globalThis.CrypterConfig = globalThis.SanaGinxConfig;
}
