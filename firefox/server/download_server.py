#!/usr/bin/env python3
"""
SanaGinx local DRM download server.

Receives POST /add with JSON from the Firefox extension:
  mpd_url, license_url, token (optional if license_headers set),
  license_headers, mpd_headers, name (optional)

Run from project root: python firefox/server/download_server.py
"""
import json
import os
import re
import subprocess
import sys
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Dict, Optional

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
FIREFOX_DIR = os.path.dirname(SCRIPT_DIR)
ROOT_DIR = os.path.abspath(os.path.join(FIREFOX_DIR, ".."))
sys.path.insert(0, ROOT_DIR)
os.chdir(ROOT_DIR)

try:
    from StreamingCommunity.utils import config_manager

    DEFAULT_SAVE_DIR = os.path.join(ROOT_DIR, config_manager.config.get("OUTPUT", "root_path") or "videos")
except Exception:
    DEFAULT_SAVE_DIR = os.path.join(ROOT_DIR, "videos")

PORT = 47984
_download_lock = threading.Lock()

_DEFAULT_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:148.0) Gecko/20100101 Firefox/148.0"


def _save_dir():
    return (
        os.environ.get("SANAGINX_SAVE_DIR")
        or os.environ.get("CRYPTER_SAVE_DIR")
        or os.environ.get("SKOOL_SAVE_DIR")
        or DEFAULT_SAVE_DIR
    )


def _normalize_headers(raw: Optional[Dict]) -> dict:
    if not raw or not isinstance(raw, dict):
        return {}
    out = {}
    for k, v in raw.items():
        if k and v is not None:
            out[str(k)] = str(v)
    return out


def build_license_headers(token: str, license_headers: Optional[Dict]) -> dict:
    if license_headers:
        h = _normalize_headers(license_headers)
        if "User-Agent" not in h and "user-agent" not in {x.lower() for x in h}:
            h["User-Agent"] = _DEFAULT_UA
        return h
    headers = {
        "Content-Type": "application/octet-stream",
        "User-Agent": _DEFAULT_UA,
    }
    if token:
        headers["pallycon-customdata-v2"] = (token or "").strip()
    return headers


def build_mpd_headers(mpd_headers: Optional[Dict]) -> dict:
    h = _normalize_headers(mpd_headers)
    if not h:
        h = {"Accept": "*/*", "User-Agent": _DEFAULT_UA}
    elif "User-Agent" not in h and "user-agent" not in {x.lower() for x in h}:
        h["User-Agent"] = _DEFAULT_UA
    if "Accept" not in h:
        h["Accept"] = "*/*"
    return h


def next_default_number(save_dir, ext="mp4"):
    if not os.path.isdir(save_dir):
        return 1
    existing = set()
    for f in os.listdir(save_dir):
        m = re.match(r"^(\d+)\." + re.escape(ext.lstrip(".")) + r"$", f, re.IGNORECASE)
        if m:
            existing.add(int(m.group(1)))
    n = 1
    while n in existing:
        n += 1
    return n


def get_ffmpeg_path():
    try:
        from StreamingCommunity.setup import get_ffmpeg_path as _g

        return _g()
    except Exception:
        return "ffmpeg"


def mkv_to_mp4(mkv_path, mp4_path):
    ffmpeg = get_ffmpeg_path()
    cmd = [ffmpeg, "-i", mkv_path, "-c", "copy", "-y", mp4_path]
    try:
        r = subprocess.run(
            cmd,
            capture_output=True,
            timeout=600,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        if r.returncode == 0 and os.path.isfile(mp4_path):
            try:
                os.remove(mkv_path)
            except Exception:
                pass
            return True
    except Exception:
        pass
    return False


def run_download(mpd_url, license_url, token, license_headers, mpd_headers, name, save_dir):
    try:
        from StreamingCommunity.utils import config_manager
        from StreamingCommunity.core.downloader import DASH_Downloader

        ext = config_manager.config.get("PROCESS", "extension")
    except Exception as e:
        return False, str(e)

    if not name:
        name = str(next_default_number(save_dir, "mp4"))
    name = re.sub(r'[<>:"/\\|?*]', "_", name)[:80].strip() or "video"
    out_path = os.path.join(save_dir, name) + "." + ext

    lic_h = build_license_headers(token, license_headers)
    mpd_h = build_mpd_headers(mpd_headers)
    if not lic_h and not token:
        return False, "Missing license_headers or token"

    try:
        dash = DASH_Downloader(
            mpd_url=mpd_url,
            mpd_headers=mpd_h,
            license_url=license_url,
            license_headers=lic_h,
            output_path=out_path,
            drm_preference="widevine",
            ensure_audio=True,
        )
        result_path, need_stop = dash.start()
    except Exception as e:
        return False, str(e)

    if not result_path:
        err = getattr(dash, "error", None) or "Download failed or stopped."
        el = err.lower()
        _looks_like_network_or_cdm = any(
            x in el
            for x in (
                "timeout",
                "timed out",
                "cdrm-project",
                "remote cdm",
                "initializing remote",
                "max retries",
                "connection",
                "unreachable",
                "getaddrinfo",
                "httpsconnectionpool",
                "connecttimeout",
                "failed to fetch decryption",
                "supabase",
                "extraction methods failed",
                "no keys",
            )
        )
        if (
            err
            and not _looks_like_network_or_cdm
            and ("decryption" in el or "key" in el or "license" in el)
        ):
            err = err + " License may be expired. Clear capture, play the video again, then Download."
        return False, err

    if result_path.lower().endswith(".mkv"):
        mp4_path = result_path[:-4] + ".mp4"
        if mkv_to_mp4(result_path, mp4_path):
            result_path = mp4_path
    return True, result_path


class Handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        origin = self.headers.get("Origin", "").strip()
        self.send_response(204)
        self.send_cors(origin)
        self.send_header("Access-Control-Max-Age", "86400")
        self.end_headers()

    def send_cors(self, origin=None):
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
        else:
            self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_POST(self):
        if self.path != "/add":
            self.send_error(404)
            return
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)
        try:
            data = json.loads(body.decode("utf-8"))
        except Exception as e:
            self.send_json(400, {"success": False, "error": "Invalid JSON: " + str(e)})
            return

        mpd_url = (data.get("mpd_url") or "").strip()
        license_url = (data.get("license_url") or "").strip()
        token = (data.get("token") or "").strip()
        license_headers = data.get("license_headers")
        mpd_headers = data.get("mpd_headers")
        name = (data.get("name") or "").strip() or None

        if not mpd_url or ".mpd" not in mpd_url.lower():
            self.send_json(400, {"success": False, "error": "Missing or invalid mpd_url"})
            return
        if not license_url:
            self.send_json(400, {"success": False, "error": "Missing license_url"})
            return
        if not token and not (license_headers and isinstance(license_headers, dict)):
            self.send_json(400, {"success": False, "error": "Missing token or license_headers"})
            return

        save_dir = _save_dir()
        os.makedirs(save_dir, exist_ok=True)

        if not _download_lock.acquire(blocking=False):
            self.send_json(
                409,
                {
                    "success": False,
                    "error": "A download is already running. Wait for it to finish.",
                },
            )
            return

        def run():
            try:
                ok, result = run_download(
                    mpd_url, license_url, token, license_headers, mpd_headers, name, save_dir
                )
                if ok:
                    print("[SanaGinx] Done:", result)
                else:
                    print("[SanaGinx] Failed:", result)
            finally:
                _download_lock.release()

        threading.Thread(target=run, daemon=True).start()
        self.send_json(200, {"success": True, "message": "Download started. Save folder: " + save_dir})

    def do_GET(self):
        if self.path in ("/status", "/"):
            self.send_json(200, {"status": "ok", "save_dir": _save_dir()})
            return
        self.send_error(404)

    def send_json(self, code, obj):
        origin = self.headers.get("Origin", "").strip()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_cors(origin)
        self.end_headers()
        try:
            self.wfile.write(json.dumps(obj).encode("utf-8"))
        except (ConnectionAbortedError, BrokenPipeError, OSError):
            pass

    def log_message(self, format, *args):
        print("[SanaGinx]", format % args)


def main():
    save_dir = _save_dir()
    print("SanaGinx DRM download server")
    print("Save folder:", save_dir)
    try:
        from StreamingCommunity.setup import get_wvd_path
        from StreamingCommunity.setup.binary_paths import binary_paths
        from StreamingCommunity.setup.device_install import workspace_root

        wvd = get_wvd_path()
        bdir = binary_paths.get_binary_directory()
        wr = workspace_root()
        proj_bin = os.path.join(wr, "binary")
        if not wvd:
            print(
                "CDM: no device.wvd — add to",
                proj_bin,
                "or",
                bdir,
                "(see README.md — CDM setup)",
            )
        else:
            print("CDM: local Widevine:", wvd)
    except Exception:
        pass
    print("Listening on http://127.0.0.1:%s" % PORT)
    print("POST /add  JSON: mpd_url, license_url, license_headers | token, mpd_headers?, name?")
    server = HTTPServer(("127.0.0.1", PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.shutdown()


if __name__ == "__main__":
    main()
