"use strict";

// Run in page context to capture token from fetch() and XMLHttpRequest; works in main page and iframes
function injectPageScript() {
  const code = function() {
    function sendLicense(urlStr, token) {
      if (urlStr && token) window.postMessage({ type: "SKOOL_LICENSE", licenseUrl: urlStr, token: token }, "*");
    }
    function getVideoTitle() {
      try {
        var numEl = document.querySelector("li.ant-menu-item-selected .number-index span");
        var titleEl = document.querySelector("li.ant-menu-item-selected .content-title h4");
        if (numEl && titleEl) {
          var num = (numEl.textContent || "").trim();
          var title = (titleEl.textContent || "").trim();
          if (num || title) return (num + " " + title).trim().replace(/[<>:"/\\|?*]/g, "_").substring(0, 80);
        }
        var sel = ".ant-typography.ant-typography-ellipsis-single-line, .ant-typography.ant-typography-ellipsis, h1, [class*=\"ant-typography\"][class*=\"ellipsis\"]";
        var el = document.querySelector(sel);
        if (el && el.textContent) {
          var t = el.textContent.trim();
          if (t.length > 0) return t.replace(/[<>:"/\\|?*]/g, "_").substring(0, 80);
        }
      } catch (e) {}
      return null;
    }
    function sendMpd(urlStr) {
      if (urlStr.indexOf("api-prod-new") !== -1) return;
      if (urlStr.indexOf("video_") === -1 && urlStr.indexOf("audio_") === -1 && urlStr.indexOf("seg-") === -1 && urlStr.indexOf(".mpd") === -1) return;
      var mpdUrl = urlStr.indexOf(".mpd") !== -1 ? urlStr : null;
      if (!mpdUrl) try {
        var u = new URL(urlStr);
        var parts = u.pathname.split("/");
        var i = parts.indexOf("assets");
        if (i >= 0 && i + 1 < parts.length) mpdUrl = u.origin + "/assets/" + parts[i + 1] + "/master.mpd";
      } catch (e) {}
      if (mpdUrl) {
        var name = getVideoTitle() || (mpdUrl.split("/").slice(-2)[0] || "video").replace(".mpd", "") || "video";
        window.postMessage({ type: "SKOOL_MPD", mpdUrl: mpdUrl, name: name }, "*");
      }
    }
    var origFetch = window.fetch;
    if (origFetch) {
      window.fetch = function(url, opts) {
        var urlStr = (typeof url === "string" ? url : (url && url.url) ? url.url : "").toString();
        if (urlStr.indexOf("licenseManager.do") !== -1 && urlStr.indexOf("pallycon") !== -1) {
          var token = "";
          if (opts && opts.headers) {
            if (opts.headers instanceof Headers) token = opts.headers.get("pallycon-customdata-v2") || "";
            else if (typeof opts.headers === "object") token = opts.headers["pallycon-customdata-v2"] || opts.headers["Pallycon-Customdata-V2"] || "";
          }
          sendLicense(urlStr, token);
        }
        if (urlStr.indexOf("tagmango") !== -1 && urlStr.indexOf("/assets/") !== -1) sendMpd(urlStr);
        return origFetch.apply(this, arguments);
      };
    }
    var XHR = XMLHttpRequest.prototype;
    var origOpen = XHR.open, origSend = XHR.send, origSetHeader = XHR.setRequestHeader;
    XHR.open = function(method, url) {
      this._skoolUrl = url;
      return origOpen.apply(this, arguments);
    };
    XHR.setRequestHeader = function(name, value) {
      if (String(name).toLowerCase() === "pallycon-customdata-v2") this._skoolToken = value;
      return origSetHeader.apply(this, arguments);
    };
    XHR.send = function(body) {
      var urlStr = this._skoolUrl || "";
      if (urlStr.indexOf("licenseManager.do") !== -1 && this._skoolToken) sendLicense(urlStr, this._skoolToken);
      if (urlStr.indexOf("tagmango") !== -1 && urlStr.indexOf("/assets/") !== -1) sendMpd(urlStr);
      return origSend.apply(this, arguments);
    };
  };
  const script = document.createElement("script");
  script.textContent = "(" + code.toString() + ")();";
  (document.head || document.documentElement).appendChild(script);
  script.remove();
}

injectPageScript();

// Listen for messages from the page script
window.addEventListener("message", function(event) {
  if (event.source !== window || !event.data || !event.data.type) return;
  if (event.data.type === "SKOOL_LICENSE") {
    browser.runtime.sendMessage({ type: "CAPTURE_LICENSE_FROM_PAGE", licenseUrl: event.data.licenseUrl, token: event.data.token });
  } else if (event.data.type === "SKOOL_MPD") {
    browser.runtime.sendMessage({ type: "CAPTURE_MPD_FROM_PAGE", mpdUrl: event.data.mpdUrl, name: event.data.name });
  }
});

// In top frame only: read video title from page and send to background (player may be in iframe so iframe can't see this)
if (window === window.top) {
  function sendPageTitle() {
    try {
      var numEl = document.querySelector("li.ant-menu-item-selected .number-index span");
      var titleEl = document.querySelector("li.ant-menu-item-selected .content-title h4");
      if (numEl && titleEl) {
        var num = (numEl.textContent || "").trim();
        var title = (titleEl.textContent || "").trim();
        if (num || title) {
          var t = (num + " " + title).trim().replace(/[<>:"/\\|?*]/g, "_").substring(0, 80);
          if (t.length > 0) browser.runtime.sendMessage({ type: "CAPTURE_PAGE_TITLE", title: t });
          return;
        }
      }
      var el = document.querySelector(".ant-typography.ant-typography-ellipsis-single-line") || document.querySelector(".ant-typography.ant-typography-ellipsis") || document.querySelector("h1") || document.querySelector("[class*=\"ant-typography\"][class*=\"ellipsis\"]");
      if (el && el.textContent) {
        var t = el.textContent.trim().replace(/[<>:"/\\|?*]/g, "_").substring(0, 80);
        if (t.length > 0) browser.runtime.sendMessage({ type: "CAPTURE_PAGE_TITLE", title: t });
      }
    } catch (e) {}
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", sendPageTitle);
  else sendPageTitle();
  setInterval(sendPageTitle, 2000);
}

// Badge only in top frame to avoid duplicates in iframes
let badge = null;
function showBadge(ready) {
  if (window !== window.top) return;
  if (!badge) {
    badge = document.createElement("div");
    badge.id = "skool-downloader-badge";
    badge.style.cssText = "position:fixed;bottom:16px;right:16px;z-index:999999;padding:8px 12px;border-radius:8px;font-family:system-ui;font-size:12px;box-shadow:0 2px 8px rgba(0,0,0,.2);background:#fff;";
    document.body.appendChild(badge);
  }
  badge.textContent = ready ? "Video captured — open extension to Download" : "Play video to capture";
  badge.style.background = ready ? "#e0f7e0" : "#f5f5f5";
}

browser.storage.onChanged.addListener(function(changes, area) {
  if (area === "local" && changes.skool_capture) showBadge(!!changes.skool_capture.newValue);
});
browser.storage.local.get("skool_capture").then(function(data) {
  showBadge(!!(data.skool_capture && data.skool_capture.token));
});
