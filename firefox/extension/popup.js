"use strict";

const STORAGE_CAPTURE = "sanaginx_capture";
const CONFIG_KEY = "sanaginx_config";

const statusEl = document.getElementById("status");
const capturePreviewEl = document.getElementById("capturePreview");
const downloadBtn = document.getElementById("download");
const refreshBtn = document.getElementById("refresh");

let serverUrl = "http://127.0.0.1:47984";

function truncateForUi(s, maxLen) {
  if (!s || typeof s !== "string") return "-";
  const t = s.trim();
  return t.length > maxLen ? t.slice(0, maxLen) + "..." : t;
}

function formatHostPath(url, maxLen) {
  try {
    const u = new URL(url);
    return truncateForUi(u.host + u.pathname + u.search, maxLen);
  } catch (e) {
    return truncateForUi(String(url), maxLen);
  }
}

function setCapturePreview(capture) {
  if (!capturePreviewEl) return;
  if (!capture || !capture.mpd_url || !capture.license_url) {
    capturePreviewEl.textContent = "";
    return;
  }
  const tokenLen = capture.token && typeof capture.token === "string" ? capture.token.length : 0;
  capturePreviewEl.textContent =
    "MPD: " +
    formatHostPath(capture.mpd_url, 80) +
    "\nLicense: " +
    formatHostPath(capture.license_url, 64) +
    "\nToken: " +
    (tokenLen ? tokenLen + " chars" : "(see license_headers)");
}

function setStatus(text, isError = false) {
  statusEl.textContent = text;
  statusEl.className = "status" + (isError ? " error" : text.indexOf("Ready") >= 0 ? " ready" : "");
}

function isCaptureReady(capture) {
  if (!capture || !capture.mpd_url || !capture.license_url) return false;
  if (capture.token && String(capture.token).trim()) return true;
  if (capture.license_headers && Object.keys(capture.license_headers).length) return true;
  return false;
}

function updateUI(capture) {
  if (isCaptureReady(capture)) {
    const name = capture.name || capture.mpd_url.split("/").slice(-2, -1)[0] || "video";
    setStatus("Ready: " + name + " — click Download", false);
    setCapturePreview(capture);
    downloadBtn.disabled = false;
    downloadBtn.dataset.capture = JSON.stringify(capture);
  } else {
    setStatus("Play the video on the page first; then Download will enable.");
    setCapturePreview(null);
    downloadBtn.disabled = true;
    downloadBtn.removeAttribute("data-capture");
  }
}

browser.storage.local.get([CONFIG_KEY]).then((data) => {
  if (data[CONFIG_KEY] && data[CONFIG_KEY].serverUrl) serverUrl = data[CONFIG_KEY].serverUrl;
});

browser.storage.local.get(STORAGE_CAPTURE).then((data) => updateUI(data[STORAGE_CAPTURE] || null));
browser.storage.onChanged.addListener((changes, area) => {
  if (area === "local" && changes[STORAGE_CAPTURE]) updateUI(changes[STORAGE_CAPTURE].newValue || null);
});

document.getElementById("openOptions").addEventListener("click", (e) => {
  e.preventDefault();
  browser.runtime.openOptionsPage();
});

downloadBtn.addEventListener("click", async () => {
  const raw = downloadBtn.dataset.capture;
  if (!raw) return;
  let capture;
  try {
    capture = JSON.parse(raw);
  } catch (e) {
    setStatus("No capture data.", true);
    return;
  }
  downloadBtn.disabled = true;
  setStatus("Downloading...");
  try {
    const r = await fetch(serverUrl.replace(/\/$/, "") + "/add", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        mpd_url: capture.mpd_url,
        license_url: capture.license_url,
        token: capture.token || "",
        license_headers: capture.license_headers || null,
        mpd_headers: capture.mpd_headers || null,
        name: capture.name || null,
      }),
    });
    const json = await r.json().catch(() => ({}));
    if (r.ok && json.success) {
      setStatus("Download started: " + (json.path || "check save folder"), false);
    } else {
      setStatus("Error: " + (json.error || r.statusText || "Download failed"), true);
      downloadBtn.disabled = false;
    }
  } catch (e) {
    setStatus("Error: Is the SanaGinx download server running?", true);
    downloadBtn.disabled = false;
  }
});

refreshBtn.addEventListener("click", () => {
  browser.runtime.sendMessage({ type: "CLEAR_CAPTURE" }).then(() => {
    setStatus("Cleared. Refreshing page...");
    setCapturePreview(null);
    downloadBtn.disabled = true;
    downloadBtn.removeAttribute("data-capture");
    browser.tabs.query({ active: true, currentWindow: true }).then((tabs) => {
      if (tabs[0] && tabs[0].id) browser.tabs.reload(tabs[0].id);
      setStatus("Cleared. Play the video again to capture a fresh license.");
    });
  });
});
