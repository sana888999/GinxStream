# 01.03.25
# Run after capturing network requests while the DRM video is playing.
# Paste the JSON array from browser_network_requests (or save to network_capture.json)
# and this script will suggest mpd_url, license_url and update DASH.py.

import json
import re
import sys
from pathlib import Path

def find_dash_urls(requests):
    """From a list of {url, method, resourceType}, find MPD and license URLs."""
    mpd_url = None
    license_url = None
    for r in requests:
        url = (r.get("url") or "").strip()
        method = (r.get("method") or "GET").upper()
        rtype = (r.get("resourceType") or "").lower()
        if not url:
            continue
        # MPD manifest
        if ".mpd" in url.split("?")[0] or rtype == "media":
            if ".mpd" in url:
                mpd_url = url
                break
        # License: often POST, or URL contains license / widevine / getlicense / key
        if method == "POST" or "license" in url.lower() or "widevine" in url.lower() or "getlicense" in url.lower() or "key" in url.lower():
            if any(x in url.lower() for x in ("license", "widevine", "getlicense", "key", "drm", "auth", "token")):
                if not license_url or "license" in url.lower():
                    license_url = url
    return mpd_url, license_url


def main():
    if len(sys.argv) > 1:
        path = Path(sys.argv[1])
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
        else:
            data = json.loads(sys.argv[1])
    else:
        data = json.loads(sys.stdin.read())

    if isinstance(data, dict) and "type" in data:
        # Single request or wrapped; try to get list
        data = data.get("requests") or [data]
    if not isinstance(data, list):
        data = [data]

    mpd_url, license_url = find_dash_urls(data)
    print("Suggested values for DASH.py:")
    print("  mpd_url =", repr(mpd_url) if mpd_url else "None  # not found")
    print("  license_url =", repr(license_url) if license_url else "None  # not found")
    print()
    print("Headers (mpd_headers, license_headers): copy from browser DevTools")
    print("  Network tab -> click the MPD request -> Headers -> Request Headers")
    print("  Same for the license POST request.")
    return mpd_url, license_url


if __name__ == "__main__":
    main()
