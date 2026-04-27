# 21.04.26
#
# mappl.tv service adapter.
# Covers: movies, TV shows, audiobooks, live sports (watch-only), live TV channels (watch-only).
#
# Search strategy:
#   1. api.mappl.tv/3/search/multi  (TMDB-compatible API hosted by mappl.tv)
#   2. Fallback to our real TMDB client if that fails.

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional


from rich.console import Console
from rich.prompt import Prompt


from StreamingCommunity.utils import TVShowManager
from StreamingCommunity.utils.http_client import create_client_curl, get_userAgent
from StreamingCommunity.utils.tmdb_client import tmdb_client
from StreamingCommunity.services._base import site_constants, EntriesManager, Entries
from StreamingCommunity.services._base.site_search_manager import base_process_search_result, base_search


from .downloader import download_film, download_series


indice = 14
_useFor = "Film_Serie"

msg = Prompt()
console = Console()
entries_manager = EntriesManager()
table_show_manager = TVShowManager()

_MAPPL_API_BASE = "https://api.mappl.tv"


def _mappl_search(query: str) -> List[Dict[str, Any]]:
    """Try api.mappl.tv/3/search/multi (TMDB-compatible)."""
    headers = {
        "user-agent": get_userAgent(),
        "accept": "application/json",
        "referer": "https://mappl.tv/",
        "origin": "https://mappl.tv",
    }
    try:
        resp = create_client_curl(headers=headers).get(
            f"{_MAPPL_API_BASE}/3/search/multi",
            params={"query": query, "language": "en-US"},
            timeout=15,
        )
        resp.raise_for_status()
        payload = resp.json()
        if isinstance(payload, dict) and isinstance(payload.get("results"), list):
            return payload["results"]
    except Exception as exc:
        console.print(f"[yellow]mappl: api.mappl.tv search failed ({exc}); falling back to TMDB")
    return []


def _tmdb_multi_search(query: str) -> List[Dict[str, Any]]:
    """Fallback TMDB search."""
    try:
        payload = tmdb_client._make_request("search/multi", {"query": query, "language": "en-US"}) or {}
        return payload.get("results") or []
    except Exception as exc:
        console.print(f"[yellow]mappl: TMDB multi-search failed for '{query}' ({exc})")
        return []


def _classify(item: Dict[str, Any]) -> Optional[str]:
    mt = item.get("media_type")
    if mt == "movie":
        return "movie"
    if mt == "tv":
        return "tv"
    return None


def title_search(query: str) -> int:
    entries_manager.clear()
    table_show_manager.clear()

    raw_results = _mappl_search(query) or _tmdb_multi_search(query)
    seen: set[tuple] = set()

    for item in raw_results:
        if not isinstance(item, dict):
            continue
        media_type = _classify(item)
        if not media_type:
            continue

        tmdb_id = item.get("id")
        if not tmdb_id:
            continue

        is_tv = media_type == "tv"
        name = (item.get("name") if is_tv else item.get("title")) or ""
        if not name:
            continue

        date = item.get("first_air_date") if is_tv else item.get("release_date")
        year_int: Optional[int] = None
        if date and len(date) >= 4 and date[:4].isdigit():
            year_int = int(date[:4])

        key = (media_type, int(tmdb_id))
        if key in seen:
            continue
        seen.add(key)

        poster_path = item.get("poster_path") or ""
        image = f"https://image.tmdb.org/t/p/w342{poster_path}" if poster_path else None

        entries_manager.add(Entries(
            id=tmdb_id,
            tmdb_id=tmdb_id,
            name=name,
            type="tv" if is_tv else "film",
            slug=None,
            url=None,
            image=image,
            year=str(year_int) if year_int else "9999",
            provider_language="en",
        ))

    return len(entries_manager)


def process_search_result(select_title, selections=None, scrape_serie=None):
    return base_process_search_result(
        select_title=select_title,
        download_film_func=download_film,
        download_series_func=download_series,
        media_search_manager=entries_manager,
        table_show_manager=table_show_manager,
        selections=selections,
        scrape_serie=scrape_serie,
    )


def search(string_to_search: str = None, get_onlyDatabase: bool = False, direct_item: dict = None,
           selections: dict = None, scrape_serie=None):
    return base_search(
        title_search_func=title_search,
        process_result_func=process_search_result,
        media_search_manager=entries_manager,
        table_show_manager=table_show_manager,
        site_name=site_constants.SITE_NAME,
        string_to_search=string_to_search,
        get_onlyDatabase=get_onlyDatabase,
        direct_item=direct_item,
        selections=selections,
        scrape_serie=scrape_serie,
    )
