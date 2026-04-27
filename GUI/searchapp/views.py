# 06.06.25


import os
import time
import json
import re
import threading
import atexit
import signal
import concurrent.futures
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# External utilities
from django.shortcuts import render, redirect
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import csrf_exempt, ensure_csrf_cookie
from django.contrib import messages
from django.utils import timezone


# Internal utilities
from .forms import SearchForm, DownloadForm
from .models import WatchlistItem
from .watchlist_auto import _get_interval_seconds
from .user_prefs import load_prefs, save_prefs, DUB_LANGUAGE_OPTIONS
from GUI.searchapp.api import get_api
from GUI.searchapp.api.base import Entries
from GUI.searchapp import drm_firefox_server


# CLI utilities
from StreamingCommunity.source.utils.tracker import download_tracker, context_tracker
from StreamingCommunity.utils.tmdb_client import tmdb_client
from StreamingCommunity.cli.run import execute_hooks
from StreamingCommunity.utils import config_manager


# Global download executor
download_executor = concurrent.futures.ThreadPoolExecutor(max_workers=10, thread_name_prefix="DownloadWorker")
scheduled_downloads: Dict[str, Dict[str, Any]] = {}
scheduled_downloads_lock = threading.Lock()
cancelled_scheduled_downloads: set[str] = set()


def _add_scheduled_download(download_id: str, title: str, site: str, media_type: str = "Film", season: str = None, episodes: str = None) -> None:
    with scheduled_downloads_lock:
        scheduled_downloads[download_id] = {
            "id": download_id,
            "title": title,
            "site": site,
            "type": media_type,
            "season": season,
            "episodes": episodes,
            "scheduled_at": time.time(),
        }
        cancelled_scheduled_downloads.discard(download_id)


def _remove_scheduled_download(download_id: str) -> None:
    with scheduled_downloads_lock:
        scheduled_downloads.pop(download_id, None)
        cancelled_scheduled_downloads.discard(download_id)


def _cancel_scheduled_download(download_id: str) -> None:
    with scheduled_downloads_lock:
        if download_id in scheduled_downloads:
            cancelled_scheduled_downloads.add(download_id)
        scheduled_downloads.pop(download_id, None)


def _is_scheduled_cancelled(download_id: str) -> bool:
    with scheduled_downloads_lock:
        return download_id in cancelled_scheduled_downloads


def _get_scheduled_downloads() -> List[Dict[str, Any]]:
    with scheduled_downloads_lock:
        return sorted(
            list(scheduled_downloads.values()),
            key=lambda item: item.get("scheduled_at", 0),
        )


def _prune_scheduled_downloads(_active_downloads: List[Dict[str, Any]], history: List[Dict[str, Any]]) -> None:
    history_ids = {item.get("id") for item in history if item.get("id")}
    now = time.time()
    max_age_seconds = 6 * 60 * 60

    with scheduled_downloads_lock:
        to_remove = []
        for download_id, item in scheduled_downloads.items():
            
            # Keep entries visible while not completed; remove only once they
            # reach history (completed/failed/cancelled) or become stale.
            if download_id in history_ids:
                to_remove.append(download_id)
                continue
            if now - float(item.get("scheduled_at", now)) > max_age_seconds:
                to_remove.append(download_id)

        for download_id in to_remove:
            scheduled_downloads.pop(download_id, None)
            cancelled_scheduled_downloads.discard(download_id)


def _format_bytes(num: Optional[float]) -> str:
    try:
        n = float(num or 0)
    except Exception:
        return "0B"
    units = ["B", "KB", "MB", "GB", "TB"]
    idx = 0
    while n >= 1024 and idx < len(units) - 1:
        n /= 1024.0
        idx += 1
    if idx == 0:
        return f"{int(n)}{units[idx]}"
    return f"{n:.2f}{units[idx]}"


def _parse_headers_text(raw: str) -> Dict[str, str]:
    headers: Dict[str, str] = {}
    if not raw:
        return headers
    for line in str(raw).splitlines():
        line = line.strip()
        if not line:
            continue
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        k = k.strip()
        v = v.strip()
        if not k:
            continue
        headers[k] = v
    return headers


def _safe_filename(name: str) -> str:
    name = (name or "").strip()
    if not name:
        return ""
    # keep it simple: remove path separators and control chars
    bad = ['\\', '/', ':', '*', '?', '"', '<', '>', '|']
    for ch in bad:
        name = name.replace(ch, "_")
    return re.sub(r"[\x00-\x1f]+", "_", name).strip(" ._")


def shutdown_downloads():
    """Shutdown downloads and kill processes on exit."""
    print("Shutting down downloads...")
    with scheduled_downloads_lock:
        scheduled_downloads.clear()
        cancelled_scheduled_downloads.clear()
    download_tracker.shutdown()
    download_executor.shutdown(wait=True)


# Ensure downloads are shut down on exit
atexit.register(shutdown_downloads)


# Handle SIGINT and SIGTERM to shutdown properly
def signal_handler(signum, frame):
    shutdown_thread = threading.Thread(target=shutdown_downloads, daemon=True)
    shutdown_thread.start()

    print("Running post-run hooks...")
    execute_hooks('post_run')

    print("Downloads shutdown started, exiting immediately...")
    os._exit(0)


if threading.current_thread() is threading.main_thread():
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)


def _resolve_original_title(item: Entries) -> str:
    """Return the original (usually English) title when TMDB can provide one.

    Falls back to ``item.name`` on any failure so we never break the UI.
    """
    try:
        prefs = load_prefs()
    except Exception:
        prefs = {"use_original_titles": True}

    if not prefs.get("use_original_titles", True):
        return item.name

    tmdb_type = "movie" if item.is_movie else "tv"
    tmdb_id = None
    try:
        tmdb_id = int(item.tmdb_id) if item.tmdb_id else None
    except Exception:
        tmdb_id = None

    # Keep this fast for the web UI:
    # - if we already have a TMDB id, fetch the cached original title
    # - otherwise, skip slug/year lookups (too slow and can stall the page)
    if tmdb_id:
        try:
            original = tmdb_client.get_original_title(tmdb_type, tmdb_id)
            if original:
                return original
        except Exception:
            return item.name

    return item.name


def _media_item_to_display_dict(item: Entries, source_alias: str) -> Dict[str, Any]:
    """Convert Entries to template-friendly dictionary."""
    poster_url = item.poster if item.poster else "https://via.placeholder.com/300x450?text=Search"
    original_title = _resolve_original_title(item)

    payload = {**item.__dict__, 'is_movie': item.is_movie}
    # Persist the original title alongside the payload so it survives the
    # round-trip through the download form and can be used for paths/logs.
    if original_title and original_title != item.name:
        payload['name_original'] = original_title

    result = {
        'display_title': original_title or item.name,
        'display_type': item.type.capitalize(),
        'source': source_alias.capitalize(),
        'source_alias': source_alias,
        'bg_image_url': poster_url,
        'is_movie': item.is_movie,
        'year': item.year,
    }
    result['payload_json'] = json.dumps(payload)
    return result


def _apply_original_title(item_payload: Dict[str, Any]) -> Dict[str, Any]:
    """If the payload carries a resolved original title, make it the active name.

    Mutates a copy of ``item_payload`` — the original Italian slug/id/url stay
    intact so scrapers still work, but ``name`` (which drives display + output
    path) becomes the English original.
    """
    if not isinstance(item_payload, dict):
        return item_payload
    try:
        prefs = load_prefs()
    except Exception:
        prefs = {"use_original_titles": True}
    if not prefs.get("use_original_titles", True):
        return item_payload

    original = item_payload.get('name_original')
    if not original:
        return item_payload

    updated = dict(item_payload)
    updated['name'] = original
    if isinstance(updated.get('raw_data'), dict):
        raw = dict(updated['raw_data'])
        raw['name'] = original
        updated['raw_data'] = raw
    return updated


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


@require_http_methods(["GET"])
def search_home(request: HttpRequest) -> HttpResponse:
    """Display search form."""
    form = SearchForm()
    return render(request, "searchapp/home.html", {"form": form})


@require_http_methods(["GET", "POST"])
def search(request: HttpRequest) -> HttpResponse:
    """Handle search requests."""
    if request.method == "POST":
        form = SearchForm(request.POST)
    else:
        query = request.GET.get('query')
        site = request.GET.get('site')
        if query and site:
            form = SearchForm({'query': query, 'site': site})
        else:
            return redirect("search_home")

    if not form.is_valid():
        messages.error(request, "Invalid data")
        return render(request, "searchapp/home.html", {"form": form})

    site = form.cleaned_data["site"]
    query = form.cleaned_data["query"]

    try:
        api = get_api(site)
        media_items = api.search(query)
        results = [_media_item_to_display_dict(item, site) for item in media_items]
    except Exception as e:
        messages.error(request, f"Search error: {e}")
        return render(request, "searchapp/home.html", {"form": form})

    download_form = DownloadForm()
    return render(
        request,
        "searchapp/results.html",
        {
            "form": SearchForm(initial={"site": site, "query": query}),
            "query": query,
            "download_form": download_form,
            "results": results,
            "selected_site": site,
        },
    )


def _run_download_in_thread(site: str, item_payload: Dict[str, Any], season: str = None, episodes: str = None, media_type: str = "Film") -> None:
    """Run download in background thread."""
    item_payload = _apply_original_title(item_payload)
    name = item_payload.get('name', 'Unknown')
    if season and episodes:
        title = f"{name} - S{season} E{episodes}"
    elif season:
        title = f"{name} - S{season}"
    else:
        title = name
        
    download_id = f"{site}_{int(time.time())}_{hash(title) % 10000}"
    _add_scheduled_download(download_id, title, site, media_type, season, episodes)
    
    def _task():
        try:
            # Some Windows shells use non-UTF8 encodings (cp1252) which can crash
            # Rich console output in service downloaders (e.g. the "→" arrow).
            # Ensure stdout/stderr can safely emit unicode without raising.
            try:
                import sys
                if hasattr(sys.stdout, "reconfigure"):
                    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
                if hasattr(sys.stderr, "reconfigure"):
                    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass

            if _is_scheduled_cancelled(download_id):
                _remove_scheduled_download(download_id)
                return

            # Set context for downloaders in this thread
            context_tracker.download_id = download_id
            context_tracker.site_name = site
            context_tracker.media_type = media_type
            context_tracker.is_gui = True
            
            api = get_api(site)
            
            # Create Entries from payload
            entries_fields = {k: v for k, v in item_payload.items() if k in Entries.__dataclass_fields__}
            media_item = Entries(**entries_fields)
            
            # Start download
            api.start_download(media_item, season=season, episodes=episodes)
        except Exception as e:
            error_msg = str(e) or "Unknown error"
            print(f"[Error] Download task failed: {error_msg}")
            import traceback
            traceback.print_exc()

            try:
                _remove_scheduled_download(download_id)
                
                # start it briefly just to mark it as failed in the history.
                if download_id not in download_tracker.downloads:
                    download_tracker.start_download(download_id, title, site, media_type)
                
                download_tracker.complete_download(download_id, success=False, error=error_msg)
            except Exception as tracker_err:
                print(f"[Error] Failed to update download tracker: {tracker_err}")

    download_executor.submit(_task)


@require_http_methods(["POST"])
def series_metadata(request: HttpRequest) -> JsonResponse:
    """
    API endpoint to get series metadata (seasons/episodes).
    Returns JSON with series information.
    """
    try:
        # Parse request
        if request.content_type and "application/json" in request.content_type:
            body = json.loads(request.body.decode("utf-8"))
            source_alias = body.get("source_alias") or body.get("site")
            item_payload = body.get("item_payload") or {}
        else:
            source_alias = request.POST.get("source_alias") or request.POST.get("site")
            item_payload_raw = request.POST.get("item_payload")
            item_payload = json.loads(item_payload_raw) if item_payload_raw else {}

        if not source_alias or not item_payload:
            return JsonResponse({"error": "Missing parameters"}, status=400)

        # Get API instance
        api = get_api(source_alias)
        
        # Convert to Entries
        entries_fields = {k: v for k, v in item_payload.items() if k in Entries.__dataclass_fields__}
        media_item = Entries(**entries_fields)
        
        # Check if it's a movie
        if media_item.is_movie:
            return JsonResponse({
                "isSeries": False,
                "seasonsCount": 0,
                "episodesPerSeason": {}
            })
        
        # Get series metadata
        seasons = api.get_series_metadata(media_item)
        
        if not seasons:
            return JsonResponse({
                "isSeries": False,
                "seasonsCount": 0,
                "episodesPerSeason": {}
            })
        
        # Build response
        episodes_per_season = {
            season.number: season.episode_count 
            for season in seasons
        }
        
        return JsonResponse({
            "isSeries": True,
            "seasonsCount": len(seasons),
            "episodesPerSeason": episodes_per_season
        })
        
    except Exception as e:
        return JsonResponse({"Error get metadata": str(e)}, status=500)


@require_http_methods(["POST"])
def start_download(request: HttpRequest) -> HttpResponse:
    """Handle download requests for movies or individual series selections."""
    form = DownloadForm(request.POST)
    if not form.is_valid():
        error_msg = f"Invalid data: {form.errors.as_text()}"
        print(f"[Error] {error_msg}")
        messages.error(request, error_msg)
        return redirect("search_home")

    source_alias = form.cleaned_data["source_alias"]
    item_payload_raw = form.cleaned_data["item_payload"]
    season = form.cleaned_data.get("season") or None
    episode = form.cleaned_data.get("episode") or None

    # Normalize
    if season:
        season = str(season).strip() or None
    if episode:
        episode = str(episode).strip() or None

    try:
        item_payload = json.loads(item_payload_raw)
    except Exception:
        messages.error(request, "Invalid payload")
        return redirect("search_home")

    # Determine media type
    media_type = "Movie" if item_payload.get("is_movie") else "Series"

    # Check for series episode selection
    if media_type == "Series" and season and not episode:
        messages.error(request, "Select at least one episode before downloading!")

    # Run download
    _run_download_in_thread(source_alias, item_payload, season, episode, media_type)
    return redirect("download_dashboard")


@require_http_methods(["GET", "POST"])
def series_detail(request: HttpRequest) -> HttpResponse:
    """
    Show series detail page with seasons and episodes.
    Handles POST for full series, full season, or episode-specific downloads.
    """
    # --- POST: handle download requests ---
    if request.method == "POST":
        return _handle_series_download(request)
    
    # --- GET: show series detail page ---
    source_alias = request.GET.get("source_alias")
    item_payload_raw = request.GET.get("item_payload")
    
    if not source_alias or not item_payload_raw:
        messages.error(request, "Missing parameters.")
        return redirect("search_home")
    
    try:
        item_payload = json.loads(item_payload_raw)
        api = get_api(source_alias)
        entries_fields = {k: v for k, v in item_payload.items() if k in Entries.__dataclass_fields__}
        media_item = Entries(**entries_fields)
        
        # Try to get TMDB backdrop for better background image
        backdrop_url = media_item.poster  # fallback to original poster
        if not media_item.is_movie:
            try:
                if media_item.tmdb_id:
                    backdrop = tmdb_client.get_backdrop_url('tv', int(media_item.tmdb_id), size="w1920")
                    if backdrop:
                        backdrop_url = backdrop
                
                else:
                    # Fallback to search by slug/year
                    slug = media_item.slug or tmdb_client._slugify(media_item.name)
                    year_str = str(media_item.year) if media_item.year else None
                    tmdb_result = tmdb_client.get_type_and_id_by_slug_year(slug, year_str, "tv")
                    if tmdb_result and tmdb_result.get('type') == 'tv':
                        backdrop = tmdb_client.get_backdrop_url('tv', tmdb_result['id'], size="w1920")
                        if backdrop:
                            backdrop_url = backdrop
                            
            except Exception:
                # If TMDB fails, keep original poster
                pass
        
        # Get series metadata
        seasons = api.get_series_metadata(media_item)
        
        if not seasons:
            messages.warning(request, "Unable to load season details right now. This may be due to active downloads. Please try again in a few minutes.")
            seasons = []  # Allow page to load with empty seasons
        
        series_info = {
            "name": media_item.name,
            "poster": media_item.poster,        # original source poster
            "backdrop": backdrop_url,           # TMDB backdrop or fallback to poster
            "year": media_item.year,
            "source_alias": source_alias,
            "item_payload": item_payload_raw,
        }
        
        seasons_data = []
        for season in seasons:
            seasons_data.append({
                "number": season.number,
                "episode_count": season.episode_count,
                "episodes": [ep.__dict__ for ep in season.episodes],
            })
        
        return render(
            request,
            "searchapp/series_detail.html",
            {
                "series": series_info,
                "seasons": seasons_data,
            }
        )
        
    except Exception as e:
        messages.error(request, f"Error loading details: {e}")
        return redirect("search_home")

def _handle_series_download(request: HttpRequest) -> HttpResponse:
    """Handle POST downloads from series_detail: full series, full season, or selected episodes."""
    source_alias = request.POST.get("source_alias")
    item_payload_raw = request.POST.get("item_payload")
    download_type = request.POST.get("download_type")
    season_number = request.POST.get("season_number")
    selected_episodes = request.POST.get("selected_episodes", "")

    if not all([source_alias, item_payload_raw]):
        messages.error(request, "Missing base parameters for the download.")
        return redirect("search_home")

    try:
        item_payload = json.loads(item_payload_raw)
    except Exception:
        messages.error(request, "Error parsing data.")
        return redirect("search_home")

    item_payload = _apply_original_title(item_payload)
    name = item_payload.get("name")
    media_type = (item_payload.get("type") or "tv").lower()

    # --- FULL SERIES DOWNLOAD (sequential, all seasons one after another) ---
    if download_type == "full_series":
        def _download_entire_series_task():
            try:
                api = get_api(source_alias)
                entries_fields = {k: v for k, v in item_payload.items() if k in Entries.__dataclass_fields__}
                media_item = Entries(**entries_fields)
                seasons = api.get_series_metadata(media_item)

                if not seasons:
                    return

                planned_seasons = []
                for season in seasons:
                    season_num = str(season.number)
                    season_title = f"{name} - S{season_num}"
                    planned_id = f"{source_alias}_{int(time.time())}_{hash(season_title + str(season_num)) % 10000}_{season_num}"
                    planned_seasons.append((planned_id, season_num))
                    _add_scheduled_download(
                        planned_id,
                        season_title,
                        source_alias,
                        media_type,
                        season=season_num,
                        episodes="*",
                    )

                for download_id, season_num in planned_seasons:
                    try:
                        if _is_scheduled_cancelled(download_id):
                            _remove_scheduled_download(download_id)
                            continue

                        context_tracker.download_id = download_id
                        context_tracker.site_name = source_alias
                        context_tracker.media_type = media_type
                        context_tracker.is_gui = True

                        api.start_download(media_item, season=season_num, episodes="*")
                    except Exception as e:
                        error_msg = str(e) or "Unknown error"
                        print(f"[Error] Download season {season_num}: {e}")
                        
                        try:
                            _remove_scheduled_download(download_id)
                            if download_id not in download_tracker.downloads:
                                season_title = f"{name} - S{season_num}"
                                download_tracker.start_download(download_id, season_title, source_alias, media_type)
                            download_tracker.complete_download(download_id, success=False, error=error_msg)
                        except Exception as tracker_err:
                            print(f"[Error] Failed to update download tracker: {tracker_err}")

            except Exception as e:
                print(f"[Error] Full series download task: {e}")

        download_executor.submit(_download_entire_series_task)

        return redirect("download_dashboard")

    # --- FULL SEASON DOWNLOAD ---
    elif download_type == "full_season":
        if not season_number:
            messages.error(request, "Missing season number.")
            return redirect("search_home")

        _run_download_in_thread(
            site=source_alias,
            item_payload=item_payload,
            season=season_number,
            episodes="*",
            media_type=media_type
        )

        return redirect("download_dashboard")

    # --- SELECTED EPISODES DOWNLOAD ---
    else:
        if not season_number:
            messages.error(request, "Missing season number.")
            return redirect("search_home")

        episode_param = selected_episodes.strip() if selected_episodes else None
        if not episode_param:
            messages.error(request, "No episodes selected.")
            from django.urls import reverse
            url = reverse('series_detail') + f"?source_alias={source_alias}&item_payload={item_payload_raw}"
            return redirect(url)

        _run_download_in_thread(
            site=source_alias,
            item_payload=item_payload,
            season=season_number,
            episodes=episode_param,
            media_type=media_type
        )

        return redirect("download_dashboard")


def download_dashboard(request: HttpRequest) -> HttpResponse:
    """Dashboard to view all active and completed downloads."""
    active_downloads = download_tracker.get_active_downloads()
    history = download_tracker.get_history()
    _prune_scheduled_downloads(active_downloads, history)
    active_ids = {d.get("id") for d in active_downloads if d.get("id")}
    scheduled = [s for s in _get_scheduled_downloads() if s.get("id") not in active_ids]

    return render(
        request, 
        "searchapp/downloads.html", 
        {
            "active_downloads": active_downloads,
            "scheduled_downloads": scheduled,
            "history": history,
            "active_count": len(active_downloads),
            "scheduled_count": len(scheduled),
        }
    )


@require_http_methods(["GET", "POST"])
def downloader_page(request: HttpRequest) -> HttpResponse:
    """
    Generic downloader (direct files + HLS/m3u8).
    Runs in background and reports progress to the Downloads dashboard.
    """
    if request.method == "GET":
        return render(request, "searchapp/downloader.html", {})

    url = (request.POST.get("url") or "").strip()
    filename_raw = (request.POST.get("filename") or "").strip()
    output_ext_raw = (request.POST.get("output_ext") or "").strip().lstrip(".")
    headers_raw = request.POST.get("headers") or ""

    if not url:
        messages.error(request, "Missing URL.")
        return redirect("downloader_page")

    parsed = urllib.parse.urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        messages.error(request, "Invalid URL.")
        return redirect("downloader_page")

    is_m3u8 = parsed.path.lower().endswith(".m3u8")
    safe_name = _safe_filename(filename_raw)
    if not safe_name:
        base = os.path.basename(parsed.path or "").strip()
        base = base or "download"
        # remove extension if present
        safe_name = _safe_filename(os.path.splitext(base)[0]) or "download"

    if output_ext_raw:
        out_ext = _safe_filename(output_ext_raw).lower()
    else:
        if is_m3u8:
            out_ext = "mp4"
        else:
            ext = os.path.splitext(parsed.path)[1].lstrip(".").lower()
            out_ext = ext if ext else "bin"

    title = f"{safe_name}.{out_ext}"
    download_id = f"manual_{int(time.time())}_{hash(url) % 100000}"
    _add_scheduled_download(download_id, title, "manual", "Downloader", None, None)

    headers = _parse_headers_text(headers_raw)

    root_path = config_manager.config.get("OUTPUT", "root_path")
    output_dir = Path(root_path) / "Downloader"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = str(output_dir / f"{safe_name}.{out_ext}")

    def _task():
        try:
            try:
                import sys

                if hasattr(sys.stdout, "reconfigure"):
                    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
                if hasattr(sys.stderr, "reconfigure"):
                    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass

            if _is_scheduled_cancelled(download_id):
                _remove_scheduled_download(download_id)
                return

            context_tracker.download_id = download_id
            context_tracker.site_name = "manual"
            context_tracker.media_type = "Downloader"
            context_tracker.is_gui = True

            download_tracker.start_download(download_id, title, "manual", "Downloader", path=output_path)
            download_tracker.update_status(download_id, "downloading")
            _remove_scheduled_download(download_id)

            if is_m3u8:
                _download_hls_ffmpeg(download_id, url, output_path, headers=headers)
            else:
                _download_direct_file(download_id, url, output_path, headers=headers)

            if download_tracker.is_stopped(download_id):
                download_tracker.complete_download(download_id, success=False, error="cancelled", path=output_path)
                return

            download_tracker.complete_download(download_id, success=True, path=output_path)
        except Exception as e:
            error_msg = str(e) or "Unknown error"
            try:
                _remove_scheduled_download(download_id)
            except Exception:
                pass
            try:
                if download_id not in download_tracker.downloads:
                    download_tracker.start_download(download_id, title, "manual", "Downloader", path=output_path)
                download_tracker.complete_download(download_id, success=False, error=error_msg, path=output_path)
            except Exception:
                pass

    download_executor.submit(_task)
    messages.success(request, f"Started '{title}' — watch progress on Downloads.")
    return redirect("download_dashboard")


def _download_direct_file(download_id: str, url: str, output_path: str, headers: Dict[str, str]) -> None:
    task_key = "video_downloader"
    req = urllib.request.Request(url, headers=headers or {})
    start = time.time()
    last_tick = start
    downloaded = 0

    with urllib.request.urlopen(req, timeout=60) as resp:
        total = None
        try:
            total_raw = resp.headers.get("Content-Length")
            total = int(total_raw) if total_raw else None
        except Exception:
            total = None

        with open(output_path, "wb") as f:
            while True:
                if download_tracker.is_stopped(download_id):
                    break

                chunk = resp.read(1024 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                downloaded += len(chunk)

                now = time.time()
                if now - last_tick >= 0.8:
                    elapsed = max(now - start, 0.001)
                    speed_bps = downloaded / elapsed
                    speed = f"{_format_bytes(speed_bps)}/s"
                    if total and total > 0:
                        progress = (downloaded / total) * 100.0
                        size = f"{_format_bytes(downloaded)}/{_format_bytes(total)}"
                    else:
                        progress = 0.0
                        size = f"{_format_bytes(downloaded)}/?"
                    download_tracker.update_progress(
                        download_id,
                        task_key,
                        progress=progress,
                        speed=speed,
                        size=size,
                        segments="",
                        status="downloading",
                    )
                    last_tick = now


def _download_hls_ffmpeg(download_id: str, url: str, output_path: str, headers: Dict[str, str]) -> None:
    task_key = "video_downloader"
    header_blob = ""
    if headers:
        # ffmpeg expects CRLF separated headers
        header_blob = "".join([f"{k}: {v}\r\n" for k, v in headers.items() if k and v])

    cmd: List[str] = ["ffmpeg", "-y"]
    if header_blob:
        cmd += ["-headers", header_blob]
    cmd += ["-i", url, "-c", "copy", output_path, "-progress", "pipe:1", "-nostats"]

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError:
        raise RuntimeError("ffmpeg not found. Install ffmpeg and ensure it's in PATH.")

    download_tracker.register_process(download_id, proc)

    total_size = 0
    speed = ""

    if not proc.stdout:
        proc.wait()
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg failed (exit {proc.returncode}).")
        return

    for raw_line in proc.stdout:
        if download_tracker.is_stopped(download_id):
            try:
                proc.terminate()
            except Exception:
                pass
            break

        line = (raw_line or "").strip()
        if not line or "=" not in line:
            continue

        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip()

        if k == "total_size":
            try:
                total_size = int(v)
            except Exception:
                pass
        elif k == "speed":
            speed = v
        elif k == "progress":
            size = f"{_format_bytes(total_size)}/?"
            download_tracker.update_progress(
                download_id,
                task_key,
                progress=0.0,
                speed=speed or "",
                size=size,
                segments="",
                status="downloading",
            )

    proc.wait()
    if download_tracker.is_stopped(download_id):
        return
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed (exit {proc.returncode}).")


def get_downloads_json(request: HttpRequest) -> JsonResponse:
    """API endpoint to get real-time download progress."""
    active_downloads = download_tracker.get_active_downloads()
    history = download_tracker.get_history()
    _prune_scheduled_downloads(active_downloads, history)
    active_ids = {d.get("id") for d in active_downloads if d.get("id")}
    # Don't list the same job as both "active" and "pending" (e.g. audiobook / mappl).
    scheduled = [s for s in _get_scheduled_downloads() if s.get("id") not in active_ids]

    return JsonResponse({
        "active": active_downloads,
        "scheduled": scheduled,
        "history": history
    })

@csrf_exempt
def kill_download(request: HttpRequest) -> JsonResponse:
    """API view to cancel a download."""
    if request.method == "POST":
        try:
            data = json.loads(request.body)
            download_id = data.get("download_id")
            if download_id:
                _cancel_scheduled_download(download_id)
                download_tracker.request_stop(download_id)
                return JsonResponse({"status": "success"})
        
        except Exception as e:
            return JsonResponse({"status": "error", "message": str(e)}, status=400)
    
    return JsonResponse({"status": "error", "message": "Method not allowed", "status_code": 405}, status=405)


@csrf_exempt
def clear_download_history(request: HttpRequest) -> JsonResponse:
    """API view to clear the download history."""
    if request.method == "POST":
        try:
            download_tracker.clear_history()
            return JsonResponse({"status": "success"})
        except Exception as e:
            return JsonResponse({"status": "error", "message": str(e)}, status=500)
    return JsonResponse({"status": "error", "message": "Method not allowed"}, status=405)


@require_http_methods(["GET"])
def watchlist(request: HttpRequest) -> HttpResponse:
    """Display the watchlist."""
    items = WatchlistItem.objects.all()
    for item in items:
        item.season_numbers = list(range(1, item.num_seasons + 1))
    poll_interval_seconds = _get_interval_seconds()
    return render(
        request,
        "searchapp/watchlist.html",
        {"items": items, "poll_interval_seconds": poll_interval_seconds},
    )


@require_http_methods(["POST"])
def set_watchlist_polling_interval(request: HttpRequest) -> HttpResponse:
    """Update the watchlist auto-check interval for this process."""
    raw = request.POST.get("poll_interval", "")
    try:
        value = int(raw)
    except Exception:
        value = None

    allowed = {300, 900, 1800, 3600, 21600, 43200, 86400}
    if value not in allowed:
        messages.error(request, "Invalid interval.")
        return redirect("watchlist")

    os.environ["WATCHLIST_AUTO_INTERVAL_SECONDS"] = str(value)
    messages.success(request, "Check interval updated.")
    return redirect("watchlist")


@require_http_methods(["POST"])
def add_to_watchlist(request: HttpRequest) -> HttpResponse:
    """Add a media item to the watchlist."""
    source_alias = request.POST.get("source_alias")
    item_payload_raw = request.POST.get("item_payload")
    search_query = request.POST.get("search_query")
    search_site = request.POST.get("search_site")
    
    if not source_alias or not item_payload_raw:
        messages.error(request, "Missing parameters for the watchlist.")
        return redirect('search_home')
    
    try:
        item_payload = json.loads(item_payload_raw)
        name = item_payload.get("name")
        poster = item_payload.get("poster")
        tmdb_id = item_payload.get("tmdb_id")
        is_movie = _to_bool(item_payload.get("is_movie"))
        
        # Check if already in watchlist
        existing = WatchlistItem.objects.filter(name=name, source_alias=source_alias).first()
        
        if existing:
            messages.info(request, f"'{name}' is already in the watchlist.")
        else:
            item = WatchlistItem.objects.create(
                name=name,
                source_alias=source_alias,
                item_payload=item_payload_raw,
                is_movie=is_movie,
                poster_url=poster,
                tmdb_id=tmdb_id,
                num_seasons=0,
                last_season_episodes=0
            )
            
            # Update metadata in background to keep GUI fast
            def _bg_update():
                _update_single_item(item)
            
            threading.Thread(target=_bg_update, daemon=True).start()
            
    except Exception as e:
        messages.error(request, f"Error adding to watchlist: {e}")
    
    # Redirect back to search results if we have the params
    if search_query and search_site:
        from django.urls import reverse
        return redirect(f"{reverse('search')}?site={search_site}&query={search_query}")
        
    return redirect(request.META.get('HTTP_REFERER', 'search_home'))


@require_http_methods(["POST"])
def remove_from_watchlist(request: HttpRequest, item_id: int) -> HttpResponse:
    """Remove an item from the watchlist."""
    try:
        item = WatchlistItem.objects.get(id=item_id)
        name = item.name
        item.delete()
        messages.success(request, f"'{name}' removed from watchlist.")
    except WatchlistItem.DoesNotExist:
        messages.error(request, "Item not found.")
    
    return redirect("watchlist")


@require_http_methods(["POST"])
def clear_watchlist(request: HttpRequest) -> HttpResponse:
    """Remove all items from the watchlist."""
    WatchlistItem.objects.all().delete()
    messages.success(request, "Watchlist cleared.")
    return redirect("watchlist")


@require_http_methods(["POST"])
def update_watchlist_auto(request: HttpRequest, item_id: int) -> HttpResponse:
    """Update auto-download settings for a watchlist item."""
    try:
        item = WatchlistItem.objects.get(id=item_id)
    except WatchlistItem.DoesNotExist:
        messages.error(request, "Item not found.")
        return redirect("watchlist")

    if item.is_movie:
        if item.auto_enabled or item.auto_season:
            item.auto_enabled = False
            item.auto_season = None
            item.auto_last_episode_count = 0
            item.auto_last_downloaded_at = None
            item.save(
                update_fields=[
                    "auto_enabled",
                    "auto_season",
                    "auto_last_episode_count",
                    "auto_last_downloaded_at",
                ]
            )
        messages.error(request, "Auto-download is not available for movies.")
        return redirect("watchlist")

    auto_enabled = request.POST.get("auto_enabled") == "on"
    auto_season_raw = request.POST.get("auto_season")
    auto_season = None
    if auto_season_raw:
        try:
            auto_season = int(auto_season_raw)
        except Exception:
            auto_season = None

    if auto_enabled and not auto_season:
        messages.error(request, "Select a season for auto-download.")
        return redirect("watchlist")

    if item.auto_season != auto_season:
        item.auto_last_episode_count = 0
        item.auto_last_downloaded_at = None

    item.auto_enabled = auto_enabled
    item.auto_season = auto_season if auto_enabled else None

    if not auto_enabled:
        item.auto_last_episode_count = 0

    item.save()
    messages.success(request, "Auto-download settings updated.")
    return redirect("watchlist")


def _update_single_item(item: WatchlistItem) -> bool:
    """Internal helper to update a single watchlist item."""
    try:
        if item.is_movie:
            item.last_checked_at = timezone.now()
            item.has_new_seasons = False
            item.has_new_episodes = False
            item.save(update_fields=["last_checked_at", "has_new_seasons", "has_new_episodes"])
            return False

        api = get_api(item.source_alias)
        item_payload = json.loads(item.item_payload)
        entries_fields = {k: v for k, v in item_payload.items() if k in Entries.__dataclass_fields__}
        media_item = Entries(**entries_fields)

        if media_item.is_movie:
            item.is_movie = True
            item.last_checked_at = timezone.now()
            item.has_new_seasons = False
            item.has_new_episodes = False
            item.save(update_fields=["is_movie", "last_checked_at", "has_new_seasons", "has_new_episodes"])
            return False

        seasons = api.get_series_metadata(media_item)
        
        if not seasons:
            return False
            
        current_num_seasons = len(seasons)
        last_season = seasons[-1]
        current_last_season_episodes = last_season.episode_count
        
        changed = False

        # If item has 0 seasons (first add), just set the initial values without marking as "new"
        if item.num_seasons == 0:
            item.num_seasons = current_num_seasons
            item.last_season_episodes = current_last_season_episodes
            changed = True
        else:
            if current_num_seasons > item.num_seasons:
                item.has_new_seasons = True
                item.num_seasons = current_num_seasons
                changed = True
            
            if current_last_season_episodes > item.last_season_episodes:
                item.has_new_episodes = True
                item.last_season_episodes = current_last_season_episodes
                changed = True
            
        item.last_checked_at = timezone.now()
        item.save()
        return changed
    except Exception as e:
        print(f"Error updating {item.name}: {e}")
        return False


@require_http_methods(["POST"])
def update_watchlist_item(request: HttpRequest, item_id: int) -> HttpResponse:
    """Update a specific watchlist item."""
    try:
        item = WatchlistItem.objects.get(id=item_id)
        threading.Thread(target=_update_single_item, args=(item,), daemon=True).start()
        messages.info(request, f"Update for '{item.name}' started in the background.")
    except WatchlistItem.DoesNotExist:
        messages.error(request, "Item not found.")
    
    return redirect("watchlist")


@require_http_methods(["POST"])
def update_all_watchlist(request: HttpRequest) -> HttpResponse:
    """Update all items in the watchlist."""
    items = WatchlistItem.objects.all()
    
    def _update_all():
        for item in items:
            _update_single_item(item)
            
    threading.Thread(target=_update_all, daemon=True).start()
    messages.info(request, "Global update started in the background. Reload in a moment.")
    return redirect("watchlist")


@require_http_methods(["POST"])
def run_watchlist_auto_now(request: HttpRequest) -> HttpResponse:
    """Trigger the auto-download scan immediately."""
    from .watchlist_auto import run_watchlist_auto_once

    threading.Thread(target=run_watchlist_auto_once, daemon=True).start()
    messages.info(request, "Auto-download started immediately in the background.")
    return redirect("watchlist")


def watchlist_status(request: HttpRequest) -> JsonResponse:
    """API endpoint to check if any watchlist item was updated recently."""
    last_update = WatchlistItem.objects.order_by('-last_checked_at').first()
    if last_update:
        return JsonResponse({
            "last_checked": last_update.last_checked_at.timestamp(),
            "items_count": WatchlistItem.objects.count()
        })
    return JsonResponse({"last_checked": 0, "items_count": 0})


@require_http_methods(["GET", "POST"])
def settings_page(request: HttpRequest) -> HttpResponse:
    """Simple settings page: original titles + additional audio dubs."""
    if request.method == "POST":
        prefs = {
            "use_original_titles": request.POST.get("use_original_titles") == "on",
            "additional_dubs": request.POST.getlist("additional_dubs"),
        }
        save_prefs(prefs)
        messages.success(request, "Settings saved.")
        return redirect("settings_page")

    prefs = load_prefs()
    enabled = set(prefs.get("additional_dubs", []) or [])
    dub_options = [
        {**opt, "enabled": opt["code"] in enabled}
        for opt in DUB_LANGUAGE_OPTIONS
    ]
    return render(
        request,
        "searchapp/settings.html",
        {
            "use_original_titles": prefs.get("use_original_titles", True),
            "dub_options": dub_options,
        },
    )


def _safe_embed_host(url: str) -> bool:
    """Whitelist hosts we're willing to iframe on the live-sports page."""
    if not url or not isinstance(url, str):
        return False
    return any(
        h in url for h in (
            "embedsports.top", "pooembed.eu", "airflix1.com",
            "mappl.tv", "nbabite.is", "totalsportek.fyi",
            "piratecat.online", "nbamonster.com",
        )
    )


def live_sports(request: HttpRequest) -> HttpResponse:
    """List live sports events from all sources: HydraHD, Mappl.tv, NBAMonster."""
    # --- HydraHD ---
    hydrahd_login_required = False
    hydrahd_events: List[Dict[str, Any]] = []
    try:
        from StreamingCommunity.services.hydrahd.live_sports import (
            list_events as hydrahd_list, LiveSportsLoginRequired,
        )
        for ev in hydrahd_list():
            d = ev.to_dict()
            # Normalize field names so the template works uniformly across sources
            d["source"] = "hydrahd"
            d["home_team"] = d.pop("home", None)
            d["away_team"] = d.pop("away", None)
            d["event_id"] = ""
            hydrahd_events.append(d)
    except Exception as exc:  # noqa: BLE001
        if "LiveSportsLoginRequired" in type(exc).__name__ or "login" in str(exc).lower():
            hydrahd_login_required = True
            messages.warning(request, f"HydraHD: {exc}")
        else:
            messages.warning(request, f"HydraHD live events unavailable: {exc}")

    # --- Mappl.tv ---
    mappl_login_required = False
    mappl_events: List[Dict[str, Any]] = []
    mappl_channels: List[Dict[str, Any]] = []
    try:
        from StreamingCommunity.services.mappl.source_api import (
            list_sports_events, list_channels,
        )
        mappl_raw = list_sports_events()
        for ev in mappl_raw:
            mappl_events.append({
                "category": ev.get("category", "Live"),
                "title": ev.get("title", ""),
                "when": "",
                "home_team": ev.get("home_team"),
                "away_team": ev.get("away_team"),
                "servers": [{"number": 1, "embed_url": ""}],
                "poster": None,
                "source": "mappl",
                "event_id": ev.get("id", ""),
            })
        mappl_channels = list_channels()
    except Exception as exc:  # noqa: BLE001
        if "session_cookie" in str(exc).lower() or "cookie" in str(exc).lower():
            mappl_login_required = True
        messages.warning(request, f"Mappl.tv live events: {exc}")

    # --- NBAMonster / NBAbite ---
    nba_events: List[Dict[str, Any]] = []
    try:
        from StreamingCommunity.services.mappl.nbamonster import list_events as nba_list
        for ev in nba_list():
            d = ev.to_dict()
            # to_dict() already returns home_team, away_team, servers (ch01-ch06)
            d["source"] = "nbamonster"
            nba_events.append(d)
    except Exception as exc:  # noqa: BLE001
        messages.warning(request, f"NBABite events unavailable: {exc}")

    # Merge all events and group by category
    all_events = hydrahd_events + mappl_events + nba_events
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for event in all_events:
        category = event.get("category") or "Live"
        grouped.setdefault(category, []).append(event)

    return render(
        request,
        "searchapp/live_sports.html",
        {
            "grouped_events": [
                {"category": cat, "events": evs}
                for cat, evs in sorted(grouped.items())
            ],
            "event_count": len(all_events),
            "login_required": hydrahd_login_required,
            "mappl_login_required": mappl_login_required,
            "mappl_channels": mappl_channels,
        },
    )


def live_sports_watch(request: HttpRequest) -> HttpResponse:
    """Render a single live-sports watch page that iframes the embed URL."""
    embed_url = request.GET.get("embed") or ""
    title = request.GET.get("title") or "Live event"
    source = request.GET.get("source") or ""

    # Build the full ordered server list. For hydrahd/nbamonster the list page
    # encodes every server URL as &also=URL&also_n=NUM query params.
    # For mappl.tv we resolve server URLs server-side via the API.
    all_servers: List[Dict[str, Any]] = []

    if source == "mappl" and embed_url and not embed_url.startswith("http"):
        try:
            from StreamingCommunity.services.mappl.source_api import get_sports_embed_urls
            resolved = get_sports_embed_urls(embed_url)
            if resolved:
                embed_url = resolved[0]["embed_url"]
                all_servers = resolved
            else:
                messages.warning(request, "No live stream found for this event right now.")
                return redirect("live_sports")
        except Exception as exc:
            messages.error(request, f"Could not resolve live stream: {exc}")
            return redirect("live_sports")
    else:
        # Collect any extra server URLs passed from the list page (&also=URL&also_n=N)
        also_urls = request.GET.getlist("also")
        also_nums = request.GET.getlist("also_n")
        if embed_url:
            all_servers.append({"number": 1, "embed_url": embed_url})
        for idx, url in enumerate(also_urls):
            num = int(also_nums[idx]) if idx < len(also_nums) and also_nums[idx].isdigit() else idx + 2
            all_servers.append({"number": num, "embed_url": url})

    if not _safe_embed_host(embed_url):
        messages.error(request, "Refusing to embed an untrusted URL.")
        return redirect("live_sports")

    return render(
        request,
        "searchapp/live_sports_watch.html",
        {
            "embed_url": embed_url,
            "title": title,
            "all_servers": all_servers,
        },
    )


# ---------------------------------------------------------------------------
# Audiobooks (Mappl.tv)
# ---------------------------------------------------------------------------

def audiobooks(request: HttpRequest) -> HttpResponse:
    """Browse / search audiobooks from mappl.tv."""
    query = request.GET.get("q", "").strip()
    books: List[Dict[str, Any]] = []
    error_msg = ""

    if query:
        try:
            from StreamingCommunity.utils.http_client import create_client_curl, get_userAgent
            from StreamingCommunity.utils import config_manager
            import re

            cookie = config_manager.login.get("mappl", "session_cookie", default="") or ""
            headers = {
                "user-agent": get_userAgent(),
                "accept": "text/x-component",
                "rsc": "1",
                "referer": "https://mappl.tv/",
            }
            if cookie:
                headers["cookie"] = cookie

            url = f"https://mappl.tv/audiobooks"
            resp = create_client_curl(headers=headers).get(
                url, params={"search": query, "type": "audiobook"}, timeout=20
            )
            resp.raise_for_status()
            raw = getattr(resp, "content", None)
            text = raw.decode("utf-8", errors="replace") if isinstance(raw, (bytes, bytearray)) else (resp.text or "")

            # Extract items array from RSC: {"items":[{"id":"...","type":"audiobook","title":"...","image":"...","subtitle":"...","authors":"...","bookId":"...","bookTitle":"..."},...]}
            m = re.search(r'"items":\s*(\[.*?\])\s*(?:,"categories"|,"total"|\})', text, re.DOTALL)
            if m:
                import json
                try:
                    items = json.loads(m.group(1))
                    for item in items:
                        if item.get("type") == "audiobook":
                            books.append({
                                "id": item.get("id") or item.get("bookId", ""),
                                "title": item.get("title") or item.get("bookTitle", ""),
                                "authors": item.get("authors", ""),
                                "subtitle": item.get("subtitle", ""),
                                "image": item.get("image", ""),
                                "slug": (item.get("bookTitle") or item.get("title") or "audiobook").lower().replace(" ", "+"),
                            })
                except Exception:
                    pass
        except Exception as exc:
            error_msg = f"Search failed: {exc}"

    return render(request, "searchapp/audiobooks.html", {
        "query": query,
        "books": books,
        "error_msg": error_msg,
    })


@require_http_methods(["POST"])
def audiobook_download(request: HttpRequest) -> HttpResponse:
    """Trigger MP3 download for an audiobook; shows live progress on the Downloads page."""
    book_id = request.POST.get("book_id", "").strip()
    slug = request.POST.get("slug", "").strip()
    title = request.POST.get("title", "Audiobook").strip()

    if not book_id or not slug:
        messages.error(request, "Missing book_id or slug.")
        return redirect("audiobooks")

    display_title = title or "Audiobook"
    download_id = f"mappl_ab_{int(time.time())}_{hash(display_title + book_id) % 100000}"
    _add_scheduled_download(download_id, display_title, "mappl", "Audiobook", None, None)

    def _task():
        try:
            try:
                import sys

                if hasattr(sys.stdout, "reconfigure"):
                    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
                if hasattr(sys.stderr, "reconfigure"):
                    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass

            if _is_scheduled_cancelled(download_id):
                _remove_scheduled_download(download_id)
                return

            context_tracker.download_id = download_id
            context_tracker.site_name = "mappl"
            context_tracker.media_type = "Audiobook"
            context_tracker.is_gui = True

            from StreamingCommunity.services.mappl.downloader import download_audiobook

            download_audiobook(book_id, slug, title)
        except Exception as e:
            error_msg = str(e) or "Unknown error"
            print(f"[audiobook_download] error: {error_msg}")
            import traceback

            traceback.print_exc()
            try:
                _remove_scheduled_download(download_id)
                if download_id not in download_tracker.downloads:
                    download_tracker.start_download(download_id, display_title, "mappl", "Audiobook")
                download_tracker.complete_download(download_id, success=False, error=error_msg)
            except Exception as tracker_err:
                print(f"[audiobook_download] tracker error: {tracker_err}")

    download_executor.submit(_task)
    messages.success(request, f"Audiobook '{display_title}' — watch progress on Downloads.")
    return redirect("download_dashboard")


# --- DRM: Firefox Skool download server (same as `python firefox/server/download_server.py`) ---


@ensure_csrf_cookie
@require_http_methods(["GET"])
def drm_page(request: HttpRequest) -> HttpResponse:
    """Control panel for the local Firefox extension HTTP server + live logs."""
    st = drm_firefox_server.get_status()
    return render(
        request,
        "searchapp/drm.html",
        {
            "drm_status": st,
            "drm_port": drm_firefox_server.DEFAULT_PORT,
        },
    )


@require_http_methods(["GET"])
def api_drm_status(request: HttpRequest) -> JsonResponse:
    return JsonResponse(drm_firefox_server.get_status())


@require_http_methods(["GET"])
def api_drm_logs(request: HttpRequest) -> JsonResponse:
    try:
        after = int(request.GET.get("after", "0"))
    except ValueError:
        after = 0
    lines = drm_firefox_server.fetch_logs_after(after, limit=800)
    return JsonResponse({"lines": lines, "status": drm_firefox_server.get_status()})


@require_http_methods(["POST"])
def api_drm_action(request: HttpRequest) -> JsonResponse:
    """Actions: run, stop, pause, resume, refresh, clear_logs."""
    try:
        data = json.loads(request.body.decode("utf-8"))
    except Exception:
        data = {}
    action = (data.get("action") or "").strip().lower()

    if action == "run":
        ok, msg = drm_firefox_server.start_server()
        return JsonResponse({"ok": ok, "message": msg, "status": drm_firefox_server.get_status()})

    if action == "stop":
        ok, msg = drm_firefox_server.stop_server()
        return JsonResponse({"ok": ok, "message": msg, "status": drm_firefox_server.get_status()})

    if action == "pause":
        ok, msg = drm_firefox_server.pause_server()
        return JsonResponse({"ok": ok, "message": msg, "status": drm_firefox_server.get_status()})

    if action == "resume":
        ok, msg = drm_firefox_server.resume_server()
        return JsonResponse({"ok": ok, "message": msg, "status": drm_firefox_server.get_status()})

    if action == "refresh":
        ok, msg = drm_firefox_server.refresh_server()
        return JsonResponse({"ok": ok, "message": msg, "status": drm_firefox_server.get_status()})

    if action == "clear_logs":
        drm_firefox_server.clear_logs()
        return JsonResponse({"ok": True, "message": "Logs cleared.", "status": drm_firefox_server.get_status()})

    return JsonResponse({"ok": False, "message": "Unknown action.", "status": drm_firefox_server.get_status()}, status=400)
