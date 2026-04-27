# 21.04.26


import json
import re
import time
from typing import Any, Dict, List, Optional, Tuple


from rich.console import Console
from rich.prompt import Prompt


from StreamingCommunity.utils import TVShowManager
from StreamingCommunity.utils.http_client import create_client_curl, get_userAgent
from StreamingCommunity.utils.tmdb_client import tmdb_client
from StreamingCommunity.services._base import site_constants, EntriesManager, Entries
from StreamingCommunity.services._base.site_search_manager import base_process_search_result, base_search


from .downloader import download_film, download_series


# Adapter metadata consumed by the lazy loader.
indice = 13
_useFor = "Film_Serie"


msg = Prompt()
console = Console()
entries_manager = EntriesManager()
table_show_manager = TVShowManager()

# Small in-memory caches to keep HydraHD search snappy in the GUI.
# (Safe: TTL-based, process-local, no persistence.)
_TMDB_MULTI_CACHE: Dict[str, Tuple[float, List[Dict[str, Any]]]] = {}
_SITE_SEARCH_CACHE: Dict[str, Tuple[float, List[Dict[str, Any]]]] = {}


def _search_request_headers() -> Dict[str, str]:
    return {
        "user-agent": get_userAgent(),
        "accept": "*/*",
        "x-requested-with": "XMLHttpRequest",
        "accept-language": "en-US,en;q=0.9",
        "referer": site_constants.FULL_URL + "/",
    }


def _site_search(query: str) -> List[Dict[str, Any]]:
    """Hit the site's own autocomplete endpoint.

    It's technically Cloudflare-gated, but ``create_client_curl`` impersonates
    a real Chrome TLS fingerprint which gets past the default challenge most
    of the time. Failures silently fall back to a TMDB-only search path.
    """
    url = f"{site_constants.FULL_URL}/ajax/search.php"
    cache_key = query.lower().strip()
    cached = _SITE_SEARCH_CACHE.get(cache_key)
    if cached and (time.time() - cached[0]) < 90:
        return cached[1]
    try:
        response = create_client_curl(headers=_search_request_headers()).get(url, params={"q": query}, timeout=15)
        response.raise_for_status()
    except Exception as exc:
        console.print(f"[yellow]hydrahd: site search unavailable ({exc}); falling back to TMDB lookup.")
        return []

    raw = response.text or ""
    if not raw.strip():
        return []
    try:
        data = json.loads(raw)
    except Exception:
        console.print("[yellow]hydrahd: site search returned non-JSON payload; falling back to TMDB.")
        return []
    if not isinstance(data, list):
        return []
    _SITE_SEARCH_CACHE[cache_key] = (time.time(), data)
    return data


def _classify_meta(meta: Any) -> str:
    text = str(meta or "").lower()
    if "tv" in text or "series" in text or "show" in text:
        return "tv"
    return "film"


def _tmdb_lookup(name: str, year: Optional[Any], media_kind: str) -> Optional[Dict[str, Any]]:
    """Resolve a TMDB id/imdb id for a HydraHD result.

    ``media_kind`` is ``'tv'`` or ``'movie'`` (per TMDB terminology).
    """
    try:
        endpoint = "search/tv" if media_kind == "tv" else "search/movie"
        params = {"query": name, "language": "en-US"}
        if year and str(year).isdigit():
            params["first_air_date_year" if media_kind == "tv" else "year"] = str(year)
        payload = tmdb_client._make_request(endpoint, params) or {}
    except Exception as exc:
        console.print(f"[yellow]hydrahd: TMDB search failed for '{name}' ({exc}).")
        return None

    results = payload.get("results") or []
    if not results:
        return None

    def _year_of(item: Dict[str, Any]) -> Optional[int]:
        date = item.get("first_air_date") if media_kind == "tv" else item.get("release_date")
        if date and len(date) >= 4 and date[:4].isdigit():
            return int(date[:4])
        return None

    target_year = int(year) if year and str(year).isdigit() else None
    target_name = (name or "").lower().strip()

    best = None
    best_score = -1
    for item in results:
        item_name = (item.get("name") if media_kind == "tv" else item.get("title")) or ""
        score = 0
        if item_name.lower().strip() == target_name:
            score += 5
        elif target_name in item_name.lower():
            score += 2
        item_year = _year_of(item)
        if target_year and item_year == target_year:
            score += 3
        elif target_year and item_year and abs(item_year - target_year) <= 1:
            score += 1
        if score > best_score:
            best_score = score
            best = item

    if not best:
        return None
    return {
        "tmdb_id": best.get("id"),
        "year": _year_of(best),
        "original_name": best.get("original_name") if media_kind == "tv" else best.get("original_title"),
        "display_name": best.get("name") if media_kind == "tv" else best.get("title"),
        "poster": f"https://image.tmdb.org/t/p/w342{best.get('poster_path')}" if best.get("poster_path") else None,
    }


def _tmdb_multi_search(query: str) -> List[Dict[str, Any]]:
    """Fallback: use TMDB's ``search/multi`` to produce HydraHD-style rows."""
    # Cache to keep repeated GUI searches snappy.
    cached = _TMDB_MULTI_CACHE.get(query.lower().strip())
    if cached and (time.time() - cached[0]) < 300:
        return cached[1]
    try:
        payload = tmdb_client._make_request("search/multi", {"query": query, "language": "en-US"}) or {}
    except Exception as exc:
        console.print(f"[yellow]hydrahd: TMDB multi search failed for '{query}' ({exc}).")
        return []

    out: List[Dict[str, Any]] = []
    for item in payload.get("results") or []:
        mtype = item.get("media_type")
        if mtype not in ("movie", "tv"):
            continue
        is_tv = mtype == "tv"
        date = item.get("first_air_date") if is_tv else item.get("release_date")
        year_val = None
        if date and len(date) >= 4 and date[:4].isdigit():
            year_val = int(date[:4])
        out.append({
            "title": (item.get("name") if is_tv else item.get("title")) or "",
            "year": year_val,
            "meta": "TV show" if is_tv else "Movie",
            "image": f"https://image.tmdb.org/t/p/w342{item.get('poster_path')}" if item.get("poster_path") else None,
            "permalink": None,
            "_tmdb_id": item.get("id"),
        })
    _TMDB_MULTI_CACHE[query.lower().strip()] = (time.time(), out)
    return out


def _bulk_resolve_tmdb_ids(query: str, rows: List[Dict[str, Any]]) -> Dict[Tuple[str, str, Optional[int]], int]:
    """Resolve many rows with a single TMDB request.

    Returns a mapping:
      (media_kind, normalized_title, year_int|None) -> tmdb_id

    Strategy:
      - Use search/multi once for the query
      - Build a best-match map by title + year for tv/movie separately
      - Rows that still can't be resolved can fall back to `_tmdb_lookup`
    """
    multi = _tmdb_multi_search(query)
    by_key: Dict[Tuple[str, str, Optional[int]], int] = {}

    def norm(s: str) -> str:
        return re.sub(r"\s+", " ", (s or "").strip().lower())

    for item in multi:
        title = norm(item.get("title") or "")
        if not title:
            continue
        is_tv = "tv" in str(item.get("meta") or "").lower()
        media_kind = "tv" if is_tv else "movie"
        y = item.get("year")
        try:
            y_int = int(y) if y is not None else None
        except Exception:
            y_int = None
        tmdb_id = item.get("_tmdb_id")
        if not tmdb_id:
            continue
        try:
            tmdb_id_int = int(tmdb_id)
        except Exception:
            continue

        # Prefer exact year match, otherwise keep a year-less fallback.
        k_exact = (media_kind, title, y_int)
        k_any = (media_kind, title, None)
        if k_exact not in by_key:
            by_key[k_exact] = tmdb_id_int
        if k_any not in by_key:
            by_key[k_any] = tmdb_id_int

    # Also add direct row titles to avoid repeated normalization work.
    # (No return side-effects; mapping is used by caller.)
    return by_key


def _permalink_id(permalink: Optional[str]) -> Optional[str]:
    if not permalink:
        return None
    match = re.search(r"/(?:movie|watchseries)/([^/?#]+)", permalink)
    return match.group(1) if match else None


def title_search(query: str) -> int:
    """Populate ``entries_manager`` with results for ``query``.

    Strategy:
      1. Try the site's autocomplete (JSON list of ``{title, year, meta,
         permalink, image}``).
      2. If that fails or is empty, fall back to TMDB multi-search.
      3. For every row, resolve a TMDB id (needed by the source API).
    """
    entries_manager.clear()
    table_show_manager.clear()

    raw_results = _site_search(query) or _tmdb_multi_search(query)
    seen_ids: set[tuple[str, int]] = set()

    bulk_map: Dict[Tuple[str, str, Optional[int]], int] = {}
    try:
        # Only helps when rows don't carry _tmdb_id (site search path).
        bulk_map = _bulk_resolve_tmdb_ids(query, raw_results)
    except Exception:
        bulk_map = {}

    def _norm_title(s: str) -> str:
        return re.sub(r"\s+", " ", (s or "").strip().lower())

    for row in raw_results:
        if not isinstance(row, dict):
            continue
        name = row.get("title") or ""
        if not name:
            continue
        year = row.get("year")
        try:
            year_int = int(year) if year and str(year).isdigit() else None
        except (TypeError, ValueError):
            year_int = None

        kind = _classify_meta(row.get("meta"))
        media_kind = "tv" if kind == "tv" else "movie"

        tmdb_id = row.get("_tmdb_id")
        if not tmdb_id:
            # Fast path: resolve from bulk multi-search mapping.
            n = _norm_title(name)
            mapped = None
            if bulk_map:
                if year_int is not None:
                    mapped = bulk_map.get((media_kind, n, year_int))
                if mapped is None:
                    mapped = bulk_map.get((media_kind, n, None))
            if mapped is not None:
                tmdb_id = mapped
            else:
                # Slow fallback (preserves previous behavior for hard cases)
                resolved = _tmdb_lookup(name, year_int, media_kind) or {}
                tmdb_id = resolved.get("tmdb_id")
                if not year_int:
                    year_int = resolved.get("year")

        if not tmdb_id:
            # Without a TMDB id we can't resolve a stream, so skip.
            continue

        key = (media_kind, int(tmdb_id))
        if key in seen_ids:
            continue
        seen_ids.add(key)

        entries_manager.add(Entries(
            id=tmdb_id,
            tmdb_id=tmdb_id,
            name=name,
            type="tv" if media_kind == "tv" else "film",
            slug=_permalink_id(row.get("permalink")),
            url=row.get("permalink"),
            image=row.get("image"),
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
