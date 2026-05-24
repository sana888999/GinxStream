"use strict";

const statusEl = document.getElementById("status");
const CC = globalThis.SanaGinxConfig;

const PALLYCON_PRESET = {
  licenseUrlIncludes: ["pallycon", "licensemanager"],
  licenseHeader: "pallycon-customdata-v2",
  licenseWebRequestHosts: ["*://*.pallycon.com/*", "*://pallycon.com/*"],
  manifestWebRequestHosts: ["*://*/*"],
  mpdUrlIncludes: [".mpd"],
  segmentUrlIncludes: ["/assets/"],
  segmentFileHints: ["video_", "audio_", "seg-", ".mpd"],
  segmentHostExcludes: ["api-prod-new"],
  deriveMpd: { enabled: true, pathMarker: "/assets/", masterFile: "master.mpd" },
};

function showStatus(msg, ok) {
  statusEl.textContent = msg;
  statusEl.className = "hint" + (ok ? " ok" : "");
}

function cfgToForm(cfg) {
  document.getElementById("serverUrl").value = cfg.serverUrl || "";
  document.getElementById("origin").value = (cfg.pageHeaders && cfg.pageHeaders.Origin) || "";
  document.getElementById("referer").value = (cfg.pageHeaders && cfg.pageHeaders.Referer) || "";
  document.getElementById("json").value = JSON.stringify(cfg.capture || {}, null, 2);
}

function formToCfg() {
  let capture = {};
  try {
    capture = JSON.parse(document.getElementById("json").value);
  } catch (e) {
    throw new Error("Invalid JSON in capture rules: " + e.message);
  }
  return {
    serverUrl: document.getElementById("serverUrl").value.trim(),
    pageHeaders: {
      Origin: document.getElementById("origin").value.trim(),
      Referer: document.getElementById("referer").value.trim(),
      "User-Agent":
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:148.0) Gecko/20100101 Firefox/148.0",
    },
    capture,
  };
}

CC.loadConfig().then(cfgToForm);

document.getElementById("save").addEventListener("click", async () => {
  try {
    const patch = formToCfg();
    const merged = CC.mergeConfig(CC.defaultConfig(), patch);
    await CC.saveConfig(merged);
    showStatus("Saved. Reload open tabs for content-script hooks to pick up changes.", true);
  } catch (e) {
    showStatus(String(e.message || e), false);
  }
});

document.getElementById("reset").addEventListener("click", async () => {
  const d = CC.defaultConfig();
  await CC.saveConfig(d);
  cfgToForm(d);
  showStatus("Reset to defaults.", true);
});

document.getElementById("presetPallycon").addEventListener("click", () => {
  document.getElementById("json").value = JSON.stringify(PALLYCON_PRESET, null, 2);
  showStatus("Pallycon + DASH preset loaded — click Save.", true);
});
