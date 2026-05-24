"use strict";

function injectPageScript(configJson) {
  const code = function (cfg) {
    function sendLicense(urlStr, token, licenseHeaders) {
      if (!urlStr) return;
      window.postMessage(
        {
          type: "SANAGINX_LICENSE",
          licenseUrl: urlStr,
          token: String(token || "").trim(),
          licenseHeaders: licenseHeaders || {},
        },
        "*"
      );
    }
    function resolveFetchUrl(input) {
      if (typeof input === "string") return input;
      if (input && typeof input.url === "string") return input.url;
      try {
        if (input && input.href) return String(input.href);
      } catch (e) {}
      return "";
    }
    function getFetchHeaders(input, init) {
      if (init && init.headers) return init.headers;
      if (input && input.headers) return input.headers;
      return null;
    }
    function readToken(headers, headerName) {
      if (!headers || !headerName) return "";
      var want = String(headerName).toLowerCase();
      if (headers instanceof Headers) return (headers.get(headerName) || "").trim();
      if (typeof headers === "object") {
        var k = Object.keys(headers);
        for (var i = 0; i < k.length; i++) {
          if (String(k[i]).toLowerCase() === want) return String(headers[k[i]] || "").trim();
        }
      }
      return "";
    }
    function headersToObject(headers) {
      var out = {};
      if (!headers) return out;
      if (headers instanceof Headers) {
        headers.forEach(function (v, k) {
          out[k] = v;
        });
        return out;
      }
      if (typeof headers === "object") {
        for (var i = 0; i < Object.keys(headers).length; i++) {
          var key = Object.keys(headers)[i];
          out[key] = headers[key];
        }
      }
      return out;
    }
    function urlMatchesAll(url, parts) {
      if (!parts || !parts.length) return true;
      var u = String(url).toLowerCase();
      for (var i = 0; i < parts.length; i++) {
        if (u.indexOf(String(parts[i]).toLowerCase()) === -1) return false;
      }
      return true;
    }
    function hostExcluded(url, excludes) {
      if (!excludes || !excludes.length) return false;
      var u = String(url).toLowerCase();
      for (var i = 0; i < excludes.length; i++) {
        if (u.indexOf(String(excludes[i]).toLowerCase()) !== -1) return true;
      }
      return false;
    }
    function deriveMpd(urlStr, derive) {
      if (!derive || !derive.enabled) return null;
      try {
        var u = new URL(urlStr);
        var parts = u.pathname.split("/");
        var marker = (derive.pathMarker || "/assets/").replace(/\/$/, "");
        var key = marker.split("/").filter(Boolean).pop() || "assets";
        var i = parts.indexOf(key);
        if (i < 0 || i + 1 >= parts.length) return null;
        var assetId = parts[i + 1];
        var master = derive.masterFile || "master.mpd";
        return u.origin + marker + "/" + assetId + "/" + master;
      } catch (e) {
        return null;
      }
    }
    function sendMpd(urlStr) {
      var cap = cfg.capture || {};
      if (hostExcluded(urlStr, cap.segmentHostExcludes)) return;
      var mpdUrl = null;
      if (urlStr.toLowerCase().indexOf(".mpd") !== -1 && urlMatchesAll(urlStr, cap.mpdUrlIncludes || [".mpd"])) {
        mpdUrl = urlStr;
      } else {
        var hints = cap.segmentFileHints || [];
        var seg = cap.segmentUrlIncludes || [];
        if (seg.length && !urlMatchesAll(urlStr, seg)) return;
        if (hints.length) {
          var ok = false;
          for (var h = 0; h < hints.length; h++) {
            if (urlStr.indexOf(hints[h]) !== -1) {
              ok = true;
              break;
            }
          }
          if (!ok) return;
        }
        mpdUrl = deriveMpd(urlStr, cap.deriveMpd);
      }
      if (mpdUrl) {
        var name = (mpdUrl.split("/").slice(-2)[0] || "video").replace(".mpd", "") || "video";
        window.postMessage({ type: "SANAGINX_MPD", mpdUrl: mpdUrl, name: name }, "*");
      }
    }
    var cap = cfg.capture || {};
    var licenseParts = cap.licenseUrlIncludes || ["license"];
    var licenseHeader = cap.licenseHeader || "";
    var origFetch = window.fetch;
    if (origFetch) {
      window.fetch = function (input, init) {
        var urlStr = resolveFetchUrl(input);
        if (urlMatchesAll(urlStr, licenseParts)) {
          var hdrs = getFetchHeaders(input, init);
          var token = readToken(hdrs, licenseHeader);
          var hdrObj = headersToObject(hdrs);
          sendLicense(urlStr, token, hdrObj);
        }
        sendMpd(urlStr);
        return origFetch.apply(this, arguments);
      };
    }
    var XHR = XMLHttpRequest.prototype;
    var origOpen = XHR.open,
      origSend = XHR.send,
      origSetHeader = XHR.setRequestHeader;
    XHR.open = function (method, url) {
      this._sgUrl = url;
      this._sgHeaders = {};
      return origOpen.apply(this, arguments);
    };
    XHR.setRequestHeader = function (name, value) {
      this._sgHeaders = this._sgHeaders || {};
      this._sgHeaders[name] = value;
      if (licenseHeader && String(name).toLowerCase() === licenseHeader.toLowerCase()) {
        this._sgToken = value;
      }
      return origSetHeader.apply(this, arguments);
    };
    XHR.send = function () {
      var urlStr = this._sgUrl || "";
      if (urlMatchesAll(urlStr, licenseParts)) {
        sendLicense(urlStr, this._sgToken || readToken(this._sgHeaders, licenseHeader), this._sgHeaders);
      }
      sendMpd(urlStr);
      return origSend.apply(this, arguments);
    };
  };
  const script = document.createElement("script");
  script.textContent = "(" + code.toString() + ")(" + configJson + ");";
  (document.head || document.documentElement).appendChild(script);
  script.remove();
}

browser.runtime.sendMessage({ type: "GET_CONFIG" }).then((cfg) => {
  if (!cfg) return;
  injectPageScript(JSON.stringify(cfg));
});

window.addEventListener("message", function (event) {
  if (event.source !== window || !event.data || !event.data.type) return;
  if (event.data.type === "SANAGINX_LICENSE" || event.data.type === "CRYPTER_LICENSE") {
    browser.runtime.sendMessage({
      type: "CAPTURE_LICENSE_FROM_PAGE",
      licenseUrl: event.data.licenseUrl,
      token: event.data.token,
      licenseHeaders: event.data.licenseHeaders,
    });
  } else if (event.data.type === "SANAGINX_MPD" || event.data.type === "CRYPTER_MPD") {
    browser.runtime.sendMessage({
      type: "CAPTURE_MPD_FROM_PAGE",
      mpdUrl: event.data.mpdUrl,
      name: event.data.name,
    });
  }
});

if (window === window.top) {
  function sendPageTitle() {
    try {
      var title = document.title || "";
      if (title.trim()) browser.runtime.sendMessage({ type: "CAPTURE_PAGE_TITLE", title: title.trim() });
    } catch (e) {}
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", sendPageTitle);
  } else {
    sendPageTitle();
  }
}
