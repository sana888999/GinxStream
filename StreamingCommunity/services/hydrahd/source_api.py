# 21.04.26
#
# Thin client around the public `streamdata.vaplayer.ru/api.php` source API
# that HydraHD's embedded player consumes. Given a TMDB id (and, for TV, the
# season/episode numbers) it returns the direct HLS URLs that the front-end
# would otherwise hand to the browser's video element.
#
# There is NO DRM on these streams - they're regular HLS playlists served
# from rotating CDN hosts (`highperformancebrands.site`, `tmstrd.justhd.tv`,
# ...). The server enforces an `Origin`/`Referer` check, but only for
# browsers; server-to-server calls work unchanged.

from __future__ import annotations

from typing import Any, Dict, List, Optional


from rich.console import Console


from StreamingCommunity.utils.http_client import create_client_curl, get_userAgent


console = Console()


# Observed during capture - these headers mimic the real embed host.
# The site rotates the referrer (airflix1.com / brightpathsignals.com /
# highperformancebrands.site) but the source API accepts any of them.
_EMBED_ORIGIN = "https://brightpathsignals.com"
_EMBED_REFERER = "https://brightpathsignals.com/"
_SOURCE_URL = "https://streamdata.vaplayer.ru/api.php"


def _build_headers() -> Dict[str, str]:
    return {
        "user-agent": get_userAgent(),
        "accept": "*/*",
        "origin": _EMBED_ORIGIN,
        "referer": _EMBED_REFERER,
        "accept-language": "en-US,en;q=0.9",
    }


def resolve_movie(tmdb_id: int | str) -> Optional[Dict[str, Any]]:
    """Return the source API payload for a movie TMDB id, or None on failure."""
    return _resolve({"tmdb": str(tmdb_id), "type": "movie"})


def resolve_episode(tmdb_id: int | str, season: int, episode: int) -> Optional[Dict[str, Any]]:
    """Return the source API payload for a TV episode TMDB id."""
    return _resolve({
        "tmdb": str(tmdb_id),
        "type": "tv",
        "season": str(season),
        "episode": str(episode),
    })


def _resolve(params: Dict[str, str]) -> Optional[Dict[str, Any]]:
    try:
        response = create_client_curl(headers=_build_headers()).get(_SOURCE_URL, params=params, timeout=20)
        response.raise_for_status()
    except Exception as exc:
        console.print(f"[red]hydrahd: streamdata.vaplayer.ru request failed ({params}): {exc}")
        return None

    try:
        payload = response.json()
    except Exception as exc:
        console.print(f"[red]hydrahd: streamdata.vaplayer.ru returned non-JSON body: {exc}")
        return None

    if not isinstance(payload, dict):
        return None
    if str(payload.get("status_code")) not in ("200", "ok", "OK"):
        console.print(f"[yellow]hydrahd: streamdata.vaplayer.ru status={payload.get('status_code')} for {params}")
    data = payload.get("data") or {}
    if not isinstance(data, dict):
        return None
    return {
        "title": data.get("title"),
        "imdb_id": data.get("imdb_id"),
        "season": data.get("season"),
        "episode": data.get("episode"),
        "file_name": data.get("file_name"),
        "backdrop": data.get("backdrop"),
        "stream_urls": _normalize_urls(data.get("stream_urls")),
        "subtitles": payload.get("default_subs") or [],
    }


def _normalize_urls(raw: Any) -> List[str]:
    if not raw:
        return []
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, list):
        return [u for u in raw if isinstance(u, str) and u.startswith("http")]
    return []


def pick_best_stream(payload: Optional[Dict[str, Any]]) -> Optional[str]:
    """Pick the first working HLS URL from a source API payload.

    The list is returned in quality/CDN preference order by the server. We
    simply take the first `https://` URL; the HLS downloader handles master
    playlist parsing.
    """
    if not payload:
        return None
    for url in payload.get("stream_urls", []):
        if isinstance(url, str) and url.startswith("https://") and ".m3u8" in url:
            return url
    return None


# Required HTTP headers so the downloader can replay the CDN request (the
# edge nodes reject missing Origin/Referer). Exposed as a helper so both
# the downloader and any future restreamer can use it.
def playback_headers() -> Dict[str, str]:
    return {
        "user-agent": get_userAgent(),
        "accept": "*/*",
        "origin": _EMBED_ORIGIN,
        "referer": _EMBED_REFERER,
        "accept-language": "en-US,en;q=0.9",
    }
