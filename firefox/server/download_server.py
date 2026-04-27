#!/usr/bin/env python3
"""
Local server for the Skool Video Downloader Firefox extension.
Receives POST /add with { mpd_url, license_url, token, name? } and runs the DASH download
using the same logic as crypterSkool. Run from project root (StreamingCommunity-main).
"""
import json
import os
import re
import subprocess
import sys
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

# Project root (StreamingCommunity-main) = parent of firefox/
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
MPD_HEADERS = {
    "Accept": "*/*",
    "Origin": "https://learn.editingskool.com",
    "Referer": "https://learn.editingskool.com/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:148.0) Gecko/20100101 Firefox/148.0",
}


def build_license_headers(token):
    return {
        "Content-Type": "application/octet-stream",
        "Origin": "https://learn.editingskool.com",
        "Referer": "https://learn.editingskool.com/",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:148.0) Gecko/20100101 Firefox/148.0",
        "pallycon-customdata-v2": (token or "").strip(),
    }


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


def run_download(mpd_url, license_url, token, name, save_dir):
    """Run one DASH download. Returns (success, result_path or error_message)."""
    try:
        from StreamingCommunity.utils import config_manager
        from StreamingCommunity.core.downloader import DASH_Downloader
        ext = config_manager.config.get("PROCESS", "extension")
    except Exception as e:
        return False, str(e)

    if not name:
        name = str(next_default_number(save_dir, "mp4"))
    name = re.sub(r'[<>:"/\\|?*]', "_", name)[:80].strip() or "video"
    base_path = os.path.join(save_dir, name)
    out_path = base_path + "." + ext

    try:
        license_headers = build_license_headers(token)
        dash = DASH_Downloader(
            mpd_url=mpd_url,
            mpd_headers=MPD_HEADERS,
            license_url=license_url,
            license_headers=license_headers,
            output_path=out_path,
            drm_preference="widevine",
            ensure_audio=True,
        )
        result_path, need_stop = dash.start()
    except Exception as e:
        return False, str(e)

    if not result_path:
        err = getattr(dash, "error", None) or "Download failed or stopped."
        if err and ("decryption" in err.lower() or "key" in err.lower() or "license" in err.lower()):
            err = err + " Token may be expired or wrong video. Click Refresh token in the extension, play this video again, then Download."
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
        # Echo Origin so Firefox extension (moz-extension://...) gets a valid CORS response
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
        name = (data.get("name") or "").strip() or None
        if not mpd_url or ".mpd" not in mpd_url.lower():
            self.send_json(400, {"success": False, "error": "Missing or invalid mpd_url"})
            return
        if not license_url:
            self.send_json(400, {"success": False, "error": "Missing license_url"})
            return
        if not token:
            self.send_json(400, {"success": False, "error": "Missing token (pallycon-customdata-v2)"})
            return

        save_dir = os.environ.get("SKOOL_SAVE_DIR", DEFAULT_SAVE_DIR)
        os.makedirs(save_dir, exist_ok=True)

        # Run download in background so we respond immediately (browser would timeout otherwise)
        def run():
            ok, result = run_download(mpd_url, license_url, token, name, save_dir)
            if ok:
                print("[SkoolServer] Done: " + result)
            else:
                print("[SkoolServer] Failed: " + str(result))

        threading.Thread(target=run, daemon=True).start()
        self.send_json(200, {"success": True, "message": "Download started. Check save folder: " + save_dir})

    def do_GET(self):
        if self.path == "/status" or self.path == "/":
            self.send_json(200, {"status": "ok", "save_dir": os.environ.get("SKOOL_SAVE_DIR", DEFAULT_SAVE_DIR)})
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
            pass  # Client closed connection (e.g. timeout); don't crash

    def log_message(self, format, *args):
        print("[SkoolServer]", format % args)


def main():
    save_dir = os.environ.get("SKOOL_SAVE_DIR", DEFAULT_SAVE_DIR)
    print("Skool Video Downloader – local server")
    print("Save folder:", save_dir)
    print("Listening on http://127.0.0.1:%s" % PORT)
    print("POST /add with JSON: mpd_url, license_url, token, name (optional)")
    server = HTTPServer(("127.0.0.1", PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.shutdown()


if __name__ == "__main__":
    main()
