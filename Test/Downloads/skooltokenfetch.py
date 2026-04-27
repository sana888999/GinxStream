# skooltokenfetch - mitmproxy addon: capture MPD URL + license URL + pallycon-customdata-v2
# when you play a video (browser via FoxyProxy -> mitmproxy). Writes to skool_captured.txt
# in this folder; crypterSkool auto-imports from that file every 3s.
#
# Setup: pip install mitmproxy. Run from this folder:
#   mitmdump -s skooltokenfetch.py --listen-host 127.0.0.1 --listen-port 8082
#   (If 8080 is in use, use 8082 and set FoxyProxy to 127.0.0.1:8082.)
# Set FoxyProxy in Chrome to the same host:port. Open your site, log in, play videos;
# each play adds one line to skool_captured.txt and crypterSkool picks it up.
#
# Captures when we see a .mpd GET or a DASH segment (we derive MPD from segment URL).
# License usually arrives before segments; we pair the license with the next MPD/segment seen.

import os
import re
import time

# Same folder as this script (put script in Test/Downloads next to crypterSkool)
CAPTURE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "skool_captured.txt")
PAIR_WINDOW_SEC = 120  # pair license with MPD seen within this many seconds

last_license = None  # (license_url, token, time) — paired when we see MPD/segment next


def _derive_mpd_from_segment(url: str, host: str) -> str:
    """From .../assets/<id>/video_1/seg-1.m4s or audio_1_1/seg-1.m4s return .../assets/<id>/master.mpd."""
    try:
        from urllib.parse import urlparse
        host_lower = (host or "").lower().split(":")[0]
        if "api-prod-new" in host_lower:
            return ""
        if "tagmango" not in host_lower and "tagmango" not in (url or "").lower():
            return ""
        parsed = urlparse(url or "")
        path = (parsed.path or "").strip("/")
        if "/assets/" not in path and "assets/" not in path:
            return ""
        if "video_" not in path and "audio_" not in path and "seg-" not in path:
            return ""
        parts = path.replace("\\", "/").strip("/").split("/")
        if "assets" not in parts:
            return ""
        i = parts.index("assets")
        if i + 1 >= len(parts):
            return ""
        asset_id = parts[i + 1]
        if not asset_id or len(asset_id) < 4:
            return ""
        scheme = parsed.scheme or "https"
        netloc = parsed.netloc or host_lower
        return f"{scheme}://{netloc}/assets/{asset_id}/master.mpd"
    except Exception:
        return ""


def _name_from_mpd_url(url: str) -> str:
    try:
        from urllib.parse import urlparse
        path = urlparse(url).path or ""
        name = path.rstrip("/").split("/")[-1] or ""
        name = name.replace(".mpd", "").strip()
        return re.sub(r'[<>:"/\\|?*]', "_", name)[:80] if name else ""
    except Exception:
        return ""


def _write_capture(mpd_url: str, license_url: str, token: str, name: str):
    line = f"{mpd_url}\t{license_url}\t{token}\t{name}\n"
    try:
        with open(CAPTURE_FILE, "a", encoding="utf-8") as f:
            f.write(line)
            f.flush()
        display = name or mpd_url.split("/")[-1].replace(".mpd", "")[:40]
        print("\n" + "=" * 60)
        print("[skooltokenfetch] SUCCESS - Captured. Move to next video.")
        print(f"  -> {display}")
        print("=" * 60 + "\n")
    except Exception as e:
        print(f"[skooltokenfetch] Failed to write: {e}")


def request(flow):
    global last_license
    try:
        url = getattr(flow.request, "pretty_url", None) or getattr(flow.request, "url", None) or ""
        if not url and hasattr(flow.request, "scheme") and hasattr(flow.request, "host") and hasattr(flow.request, "path"):
            url = f"{flow.request.scheme or 'https'}://{flow.request.host or ''}{flow.request.path or ''}"
        url = (url or "").strip()
        method = (flow.request.method or "").upper()
        host = (flow.request.host or "").lower()

        # MPD: direct .mpd GET or derive from DASH segment URL (tagmango.com/assets/<id>/video_1/seg-*.m4s)
        if method == "GET":
            mpd_url = url if ".mpd" in url else _derive_mpd_from_segment(url, host)
            if mpd_url:
                name = _name_from_mpd_url(mpd_url) or (url.split("/")[-2] if "/" in url.rstrip("/") else "")
                now = time.time()
                if last_license:
                    lic_url, lic_token, lic_t = last_license
                    if now - lic_t <= PAIR_WINDOW_SEC:
                        _write_capture(mpd_url, lic_url, lic_token, name)
                        last_license = None

        # Pallycon license POST: store for pairing with next MPD/segment (license usually comes before segments)
        if "licensemanager.do" in url.lower() and "pallycon" in host and method == "POST":
            token = (flow.request.headers.get("pallycon-customdata-v2") or "").strip()
            if not token:
                return
            license_url = url
            now = time.time()
            last_license = (license_url, token, now)
    except Exception as e:
        print(f"[skooltokenfetch] Error: {e}")
