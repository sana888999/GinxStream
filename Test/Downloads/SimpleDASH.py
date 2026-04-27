# Simple DASH downloader with queue: add MPD + license info, get video+audio.
# Run: python SimpleDASH.py
# Add more lines to dash_queue.txt while it's running to download non-stop.
# Optional: third column = custom name for the file; else auto from MPD URL.

import os
import re
import sys
import time

src_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, src_path)

from StreamingCommunity.utils import config_manager, start_message
from StreamingCommunity.core.downloader import DASH_Downloader

# --- CONFIG: edit these once (and refresh pallycon token when it expires) ---
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "Video")
QUEUE_FILE = os.path.join(os.path.dirname(__file__), "dash_queue.txt")

# Default headers for MPD and license (TagMango + Pallycon).
# pallycon-customdata-v2 is per-video: play the video in browser, copy from license POST request, paste here.
MPD_HEADERS = {
    "Accept": "*/*",
    "Origin": "https://learn.editingskool.com",
    "Referer": "https://learn.editingskool.com/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:148.0) Gecko/20100101 Firefox/148.0",
}
LICENSE_HEADERS = {
    "Content-Type": "application/octet-stream",
    "Origin": "https://learn.editingskool.com",
    "Referer": "https://learn.editingskool.com/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:148.0) Gecko/20100101 Firefox/148.0",
    "pallycon-customdata-v2": "eyJkcm1fdHlwZSI6IldpZGV2aW5lIiwic2l0ZV9pZCI6IktMNDgiLCJ1c2VyX2lkIjoiNjgxZmVhYzY5ZjYwZGFlYzBmNjNkODU4IiwiY2lkIjoiMTczMjE4NjI0OTAxMyIsInBvbGljeSI6ImVtTm5oWHFhYW4yWFZRLy9UcjRXK2FSb2FaYTBudGJ3WnFvVzJXNDc3Q0JiTXV5eGlCejFsbU1GYm9waGdGRUNpNGhOOHdYbytMUThOczNwR0xuTHlRPT0iLCJ0aW1lc3RhbXAiOiIyMDI2LTAzLTAyVDAwOjU4OjQxWiIsImhhc2giOiJFY0Z0ZEppTkpVUU1SYTVLTHdrVmNkQzdRN2xWWWFDaFYxTGY2Q0pEM1RJPSIsInJlc3BvbnNlX2Zvcm1hdCI6Im9yaWdpbmFsIiwia2V5X3JvdGF0aW9uIjpmYWxzZX0=",  # Refresh from browser when expired
}

# Per-job override: if a line in the queue has 4th column (pipe-separated), use as JSON for extra license headers (e.g. new token).
# Format: mpd_url | license_url | optional_name | (optional, not used yet)


def _default_name(mpd_url: str, index: int) -> str:
    try:
        from urllib.parse import urlparse
        path = urlparse(mpd_url.strip()).path or ""
        name = path.rstrip("/").split("/")[-1] or ""
        name = name.replace(".mpd", "").strip()
        if name:
            return re.sub(r"[^\w\-.]", "_", name)[:80]
    except Exception:
        pass
    return f"video_{index}"


def _parse_queue_line(line: str) -> dict | None:
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    parts = [p.strip() for p in line.replace("\t", "|").split("|")]
    if len(parts) < 2:
        return None
    mpd_url, license_url = parts[0], parts[1]
    name = (parts[2].strip() or None) if len(parts) > 2 else None
    return {"mpd_url": mpd_url, "license_url": license_url, "name": name}


def _read_queue(path: str):
    if not os.path.isfile(path):
        return []
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return [j for line in f for j in (_parse_queue_line(line),) if j]


def _remove_first_job_from_file(path: str, job: dict):
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()
    new_lines = []
    removed = False
    for L in lines:
        if not removed:
            parsed = _parse_queue_line(L)
            if parsed and parsed.get("mpd_url") == job.get("mpd_url") and parsed.get("license_url") == job.get("license_url"):
                removed = True
                continue
        new_lines.append(L)
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(new_lines)


def main():
    start_message()
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    conf_ext = config_manager.config.get("PROCESS", "extension")
    index = 0

    while True:
        jobs = _read_queue(QUEUE_FILE)
        if not jobs:
            print("\nQueue empty. Add lines to dash_queue.txt and save (or run again). Format:")
            print("  mpd_url | license_url | optional_name")
            print("Example:")
            print("  https://tagmango.com/assets/123/master.mpd | https://license-global.pallycon.com/ri/licenseManager.do | MyVideo")
            time.sleep(5)
            continue

        job = jobs[0]
        index += 1
        name = job.get("name") or _default_name(job["mpd_url"], index)
        out_path = os.path.join(OUTPUT_DIR, f"{name}.{conf_ext}")

        print(f"\n[{index}] Downloading: {name}")
        print(f"    MPD: {job['mpd_url'][:60]}...")
        dash = DASH_Downloader(
            mpd_url=job["mpd_url"],
            mpd_headers=MPD_HEADERS,
            license_url=job["license_url"],
            license_headers=LICENSE_HEADERS,
            output_path=out_path,
            drm_preference="widevine",
            ensure_audio=True,
        )
        out_path_result, need_stop = dash.start()
        if out_path_result:
            print(f"    Done: {out_path_result}")
            # Only remove job from queue on success so you can retry after fixing token
            _remove_first_job_from_file(QUEUE_FILE, job)
        else:
            print(f"    Failed or stopped: {need_stop}")
            print(f"    Job left in queue. Refresh pallycon-customdata-v2 in SimpleDASH.py for this video and run again.")


if __name__ == "__main__":
    main()
