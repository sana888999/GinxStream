"use strict";

const SERVER_URL = "http://127.0.0.1:47984";

const statusEl = document.getElementById("status");
const downloadBtn = document.getElementById("download");
const refreshBtn = document.getElementById("refresh");

function setStatus(text, isError = false) {
  statusEl.textContent = text;
  statusEl.className = "status" + (isError ? " error" : text.indexOf("Ready") >= 0 ? " ready" : "");
}

function updateUI(capture) {
  if (capture && capture.mpd_url && capture.license_url && capture.token) {
    const name = capture.name || capture.mpd_url.split("/").slice(-2, -1)[0] || "video";
    setStatus("Ready: " + name + " — click Download", false);
    downloadBtn.disabled = false;
    downloadBtn.dataset.capture = JSON.stringify(capture);
  } else {
    setStatus("Play the video on the page first; then Download will enable.");
    downloadBtn.disabled = true;
    downloadBtn.removeAttribute("data-capture");
  }
}

browser.storage.local.get("skool_capture").then((data) => updateUI(data.skool_capture || null));
browser.storage.onChanged.addListener((changes, area) => {
  if (area === "local" && changes.skool_capture) updateUI(changes.skool_capture.newValue || null);
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
  setStatus("Downloading…");
  try {
    const r = await fetch(SERVER_URL + "/add", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        mpd_url: capture.mpd_url,
        license_url: capture.license_url,
        token: capture.token,
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
    setStatus("Error: Is the download server running? Start it from the firefox folder.", true);
    downloadBtn.disabled = false;
  }
});

refreshBtn.addEventListener("click", () => {
  browser.runtime.sendMessage({ type: "CLEAR_CAPTURE" }).then(() => {
    setStatus("Cleared. Refreshing page…");
    downloadBtn.disabled = true;
    downloadBtn.removeAttribute("data-capture");
    browser.tabs.query({ active: true, currentWindow: true }).then((tabs) => {
      if (tabs[0] && tabs[0].id) browser.tabs.reload(tabs[0].id);
      setStatus("Cleared and page refreshed. Play the video to capture a new token.");
    });
  });
});
