# 29.07.25
# ruff: noqa: E402
#
# How to get these values (e.g. for learn.editingskool.com / TagMango):
# 1. Open the course video page in a browser and log in.
# 2. Start playing the DRM video.
# 3. Capture network: in Cursor, ask to "capture network for DASH" and the
#    MPD URL and license URL will be filled below. Or use Chrome DevTools:
#    Network tab -> filter by "media" / "XHR" -> find the .mpd request and the
#    license POST request -> copy URL and Request Headers.
# 4. Paste headers into mpd_headers / license_headers if the site requires
#    cookies or Authorization (e.g. from Copy as cURL or Request Headers).

import os
import sys


# Fix import
src_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.append(src_path)


from StreamingCommunity.utils import config_manager, start_message
from StreamingCommunity.core.downloader import DASH_Downloader


start_message()
conf_extension = config_manager.config.get("PROCESS", "extension")


# --- Filled from browser (TagMango + Pallycon DRM). Refresh pallycon-customdata-v2 if it expires. ---
mpd_url = "https://tagmango.com/assets/1732186249013/master.mpd"
mpd_headers = {
    "Accept": "*/*",
    "Origin": "https://learn.editingskool.com",
    "Referer": "https://learn.editingskool.com/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:148.0) Gecko/20100101 Firefox/148.0",
}
license_url = "https://license-global.pallycon.com/ri/licenseManager.do"
license_headers = {
    "Content-Type": "application/octet-stream",
    "Origin": "https://learn.editingskool.com",
    "Referer": "https://learn.editingskool.com/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:148.0) Gecko/20100101 Firefox/148.0",
    "pallycon-customdata-v2": "eyJkcm1fdHlwZSI6IldpZGV2aW5lIiwic2l0ZV9pZCI6IktMNDgiLCJ1c2VyX2lkIjoiNjgxZmVhYzY5ZjYwZGFlYzBmNjNkODU4IiwiY2lkIjoiMTczMjE4NjI0OTAxMyIsInBvbGljeSI6ImVtTm5oWHFhYW4yWFZRLy9UcjRXK2FSb2FaYTBudGJ3WnFvVzJXNDc3Q0JiTXV5eGlCejFsbU1GYm9waGdGRUNpNGhOOHdYbytMUThOczNwR0xuTHlRPT0iLCJ0aW1lc3RhbXAiOiIyMDI2LTAzLTAyVDAwOjU4OjQxWiIsImhhc2giOiJFY0Z0ZEppTkpVUU1SYTVLTHdrVmNkQzdRN2xWWWFDaFYxTGY2Q0pEM1RJPSIsInJlc3BvbnNlX2Zvcm1hdCI6Im9yaWdpbmFsIiwia2V5X3JvdGF0aW9uIjpmYWxzZX0=",
}
license_key = None

dash_process = DASH_Downloader(
    mpd_url=mpd_url,
    mpd_headers=mpd_headers,
    license_url=license_url,
    license_headers=license_headers,
    output_path=fr".\Video\Prova.{conf_extension}",
    key=license_key,
    drm_preference="widevine",
    ensure_audio=True,
)

out_path, need_stop = dash_process.start()
print(f"Output path: {out_path}, Need stop: {need_stop}")