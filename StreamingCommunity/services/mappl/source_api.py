# 21.04.26
#
# mappl.tv playback pipeline.
#
# The site protects VOD streams behind three layers:
#   1. /api/encrypt  - request signing (opaque `data` parameter)
#   2. /api/stream-token - Proof-of-Work (SHA-256, difficulty ~18 bits)
#   3. /api/stream / /api/stream-encrypted - returns the HLS URL
#
# Every request needs:
#   - _mapple_site cookie (auto-set by server on first visit – no login needed)
#   - window.__REQUEST_TOKEN__  (injected into the site HTML, valid ~6h)
#   - Correct Origin/Referer headers on the final HLS CDN request.
#
# Cookies are managed automatically by a module-level persistent curl_cffi
# Session.  User-provided cookies in Conf/login.json are optional and only
# used as a seed (e.g. to supply a pre-solved cf_clearance).
#
# No DRM on the HLS streams - they are served from source.heistotron.uk.

from __future__ import annotations

import hashlib
import re
import time
from typing import Any, Dict, Optional, Tuple


from rich.console import Console


from StreamingCommunity.utils import config_manager
from StreamingCommunity.utils.http_client import get_userAgent


console = Console()

_BASE_URL = "https://mappl.tv"
_API_KEY = "mptv_sk_a8f29c4e7b3d1f"
_ORIGIN = "https://mappl.tv"
_REFERER = "https://mappl.tv/"

# Cached (request_token, fetched_at) so we don't hammer the homepage
_token_cache: Tuple[Optional[str], float] = (None, 0.0)
_TOKEN_TTL = 3600  # seconds (token is valid 6h but we refresh after 1h to be safe)

# Module-level persistent curl_cffi session – cookies accumulate automatically
_session_obj: Optional[Any] = None


def _get_session() -> Any:
    """Return (and lazily create) the persistent curl_cffi session.

    On first call the session is optionally seeded with any cookies the user
    has stored in Conf/login.json → mappl.session_cookie.  The server will
    then set _mapple_site (and possibly cf_clearance) on the first real
    request, just as a browser would.
    """
    global _session_obj
    if _session_obj is None:
        try:
            from curl_cffi import requests as cffi_requests
            _session_obj = cffi_requests.Session(impersonate="chrome142")
        except Exception:
            # Fallback: use create_client_curl factory (stateless, but still works)
            from StreamingCommunity.utils.http_client import create_client_curl
            _session_obj = create_client_curl()

        # Seed with optional user-supplied cookies (login.json)
        try:
            raw = config_manager.login.get("mappl", "session_cookie", default="") or ""
            raw = raw.strip()
        except Exception:
            raw = ""
        if raw and hasattr(_session_obj, "cookies"):
            for part in raw.split(";"):
                part = part.strip()
                if "=" in part:
                    k, v = part.split("=", 1)
                    try:
                        _session_obj.cookies.set(k.strip(), v.strip(), domain="mappl.tv")
                    except Exception:
                        pass

    return _session_obj


def _build_headers(extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """Base request headers.  Cookies are carried by the persistent session."""
    h: Dict[str, str] = {
        "user-agent": get_userAgent(),
        "accept": "*/*",
        "origin": _ORIGIN,
        "referer": _REFERER,
        "accept-language": "en-US,en;q=0.9",
    }
    if extra:
        h.update(extra)
    return h


def _get_request_token() -> Optional[str]:
    """Return a valid window.__REQUEST_TOKEN__ from the mappl.tv homepage."""
    global _token_cache
    cached_tok, cached_at = _token_cache
    if cached_tok and (time.time() - cached_at) < _TOKEN_TTL:
        return cached_tok

    try:
        headers = _build_headers({"accept": "text/html,application/xhtml+xml,*/*;q=0.8"})
        resp = _get_session().get(_BASE_URL + "/", headers=headers, timeout=20)
        resp.raise_for_status()
    except Exception as exc:
        console.print(f"[red]mappl: failed to fetch homepage for requestToken: {exc}")
        return None

    raw = getattr(resp, "content", None)
    if isinstance(raw, (bytes, bytearray)):
        text = raw.decode("utf-8", errors="replace")
    else:
        text = resp.text or ""

    m = re.search(r'window\.__REQUEST_TOKEN__\s*=\s*"([^"]+)"', text)
    if not m:
        console.print("[yellow]mappl: could not find window.__REQUEST_TOKEN__ in homepage HTML")
        return None

    tok = m.group(1)
    _token_cache = (tok, time.time())
    return tok


def _solve_pow(challenge: str, difficulty: int) -> str:
    """Return the integer nonce (as string) satisfying SHA-256 hashcash.

    We need SHA256(challenge + str(nonce)) to have ``difficulty`` leading zero
    bits.  Difficulty 18 means on average ~262k iterations; Python solves this
    in well under a second.
    """
    full_bytes = difficulty // 8
    remaining_bits = difficulty % 8
    nonce = 0
    while True:
        candidate = (challenge + str(nonce)).encode()
        digest = hashlib.sha256(candidate).digest()
        if all(b == 0 for b in digest[:full_bytes]):
            if remaining_bits == 0 or (digest[full_bytes] >> (8 - remaining_bits)) == 0:
                return str(nonce)
        nonce += 1


def _post_json(path: str, payload: Any) -> Optional[Dict[str, Any]]:
    headers = _build_headers({"content-type": "application/json"})
    try:
        resp = _get_session().post(_BASE_URL + path, json=payload, headers=headers, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        console.print(f"[red]mappl: POST {path} failed: {exc}")
        return None


def _get_json(path: str, params: Optional[Dict[str, str]] = None) -> Optional[Dict[str, Any]]:
    headers = _build_headers()
    try:
        resp = _get_session().get(_BASE_URL + path, params=params or {}, headers=headers, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        console.print(f"[red]mappl: GET {path} failed: {exc}")
        return None


def _resolve_stream(tmdb_id: int, media_type: str, season: int = 0, episode: int = 0) -> Optional[str]:
    """Full pipeline → HLS URL for a VOD item.

    Returns the direct HLS URL on success, None on any failure.
    """
    request_token = _get_request_token()
    if not request_token:
        console.print("[red]mappl: no requestToken available - add session_cookie to Conf/login.json")
        return None

    # 1. /api/encrypt - get the signed data blob for the stream URL
    tv_slug = f"{season}-{episode}" if media_type == "tv" else ""
    encrypt_payload = {
        "data": {
            "mediaId": tmdb_id,
            "mediaType": media_type,
            "tv_slug": tv_slug,
            "source": "mapple",
        },
        "endpoint": "stream-encrypted",
    }
    enc_resp = _post_json("/api/encrypt", encrypt_payload)
    if not enc_resp or not enc_resp.get("encrypted"):
        console.print(f"[red]mappl: /api/encrypt returned no data for tmdb={tmdb_id}")
        return None
    encrypted = enc_resp["encrypted"]

    # 2. /api/stream-token - may require PoW
    stream_token_payload: Dict[str, Any] = {
        "mediaId": tmdb_id,
        "mediaType": media_type,
        "requestToken": request_token,
    }
    for attempt in range(5):
        st_resp = _post_json("/api/stream-token", stream_token_payload)
        if not st_resp:
            return None
        if st_resp.get("success") and not st_resp.get("requiresPow"):
            stream_token = st_resp.get("token")
            break
        if st_resp.get("requiresPow"):
            pow_data = st_resp.get("pow") or {}
            challenge_id = pow_data.get("challengeId")
            challenge = pow_data.get("challenge", "")
            difficulty = int(pow_data.get("difficulty", 18))
            console.print(f"[yellow]mappl: solving PoW (difficulty={difficulty})…")
            nonce = _solve_pow(challenge, difficulty)
            stream_token_payload = {
                "mediaId": tmdb_id,
                "mediaType": media_type,
                "requestToken": request_token,
                "pow": {
                    "challengeId": challenge_id,
                    "nonce": nonce,
                },
            }
        else:
            console.print(f"[red]mappl: /api/stream-token unexpected response: {st_resp}")
            return None
    else:
        console.print("[red]mappl: failed to get stream token after 5 attempts")
        return None

    if not stream_token:
        console.print("[red]mappl: /api/stream-token returned empty token")
        return None

    # 3. GET /api/stream-encrypted → stream_url
    params: Dict[str, str] = {
        "data": encrypted,
        "apikey": _API_KEY,
        "requestToken": request_token,
        "token": stream_token,
    }
    if media_type == "tv" and tv_slug:
        params["tv_slug"] = tv_slug
    stream_resp = _get_json("/api/stream-encrypted", params)
    if not stream_resp or not stream_resp.get("success"):
        # Fallback: /api/stream
        fallback_params: Dict[str, str] = {
            "mediaId": str(tmdb_id),
            "mediaType": media_type,
            "source": "mapple",
            "apikey": _API_KEY,
            "requestToken": request_token,
            "token": stream_token,
        }
        if tv_slug:
            fallback_params["tv_slug"] = tv_slug
        stream_resp = _get_json("/api/stream", fallback_params)

    if not stream_resp or not stream_resp.get("success"):
        console.print(f"[red]mappl: stream API returned no URL for tmdb={tmdb_id}")
        return None

    data = stream_resp.get("data") or {}
    url = data.get("stream_url") or ""
    if not url.startswith("http"):
        console.print(f"[red]mappl: stream_url not a valid URL: {url[:100]}")
        return None
    return url


def resolve_movie(tmdb_id: int) -> Optional[str]:
    """Return HLS URL for a movie, or None."""
    return _resolve_stream(tmdb_id, "movie")


def resolve_episode(tmdb_id: int, season: int, episode: int) -> Optional[str]:
    """Return HLS URL for a TV episode, or None."""
    return _resolve_stream(tmdb_id, "tv", season, episode)


def playback_headers() -> Dict[str, str]:
    """Required headers when requesting the HLS playlist from heistotron.uk."""
    return {
        "user-agent": get_userAgent(),
        "accept": "*/*",
        "origin": _ORIGIN,
        "referer": _REFERER,
        "accept-language": "en-US,en;q=0.9",
    }


# ---------------------------------------------------------------------------
# Audiobook helpers
# ---------------------------------------------------------------------------

def get_audiobook_parts(book_id: str, slug: str) -> list[str]:
    """Return list of absolute audio URLs from the audiobook-listen RSC page.

    The server embeds ``audio_items`` (list of ``/api/audiobook/u/{token}``
    paths) in the RSC payload.  We extract them with a simple regex.
    """
    headers = _build_headers({
        "accept": "text/x-component",
        "next-router-state-tree": "%5B%22%22%2C%7B%7D%5D",
        "rsc": "1",
    })
    url = f"{_BASE_URL}/audiobook-listen/{book_id}/{slug}"
    try:
        resp = _get_session().get(url, headers=headers, timeout=20)
        resp.raise_for_status()
    except Exception as exc:
        console.print(f"[red]mappl: failed to fetch audiobook page {url}: {exc}")
        return []

    raw = getattr(resp, "content", None)
    text = raw.decode("utf-8", errors="replace") if isinstance(raw, (bytes, bytearray)) else (resp.text or "")

    parts = re.findall(r'"/api/audiobook/u/([^"]+)"', text)
    return [f"{_BASE_URL}/api/audiobook/u/{p}" for p in parts]


def audiobook_download_headers() -> Dict[str, str]:
    """Headers for downloading from /api/audiobook/u/…"""
    h = _build_headers({"accept": "audio/*,*/*"})
    return h


# ---------------------------------------------------------------------------
# Live sports helpers
# ---------------------------------------------------------------------------

def list_sports_events() -> list[Dict[str, Any]]:
    """Scrape the live-tv-sports RSC listing page for current events.

    Returns a list of dicts with keys: id, title, category, date, poster,
    home_team, away_team.
    """
    headers = _build_headers({
        "accept": "text/x-component",
        "rsc": "1",
    })
    try:
        resp = _get_session().get(_BASE_URL + "/live-tv-sports", headers=headers, timeout=20)
        resp.raise_for_status()
    except Exception as exc:
        console.print(f"[red]mappl: failed to fetch live-tv-sports: {exc}")
        return []

    raw = getattr(resp, "content", None)
    text = raw.decode("utf-8", errors="replace") if isinstance(raw, (bytes, bytearray)) else (resp.text or "")

    # Events are embedded as JSON objects in the RSC wire format.
    # Pattern: {"id":"<slug>","title":"...","category":"...","date":..., ...}
    events: list[Dict[str, Any]] = []
    seen: set[str] = set()
    for m in re.finditer(
        r'\{"id":"([a-z0-9\-]+)","title":"([^"]+)","category":"([^"]+)","date":(\d+)',
        text,
    ):
        eid, title, category, date_ms = m.group(1), m.group(2), m.group(3), int(m.group(4))
        if eid in seen:
            continue
        seen.add(eid)
        # Extract teams if "vs" pattern
        home = away = None
        vs = re.split(r"\s+vs\.?\s+", title, maxsplit=1, flags=re.IGNORECASE)
        if len(vs) == 2:
            home, away = vs[0].strip(), vs[1].strip()
        events.append({
            "id": eid,
            "title": title,
            "category": category,
            "date_ms": date_ms,
            "home_team": home,
            "away_team": away,
        })

    return events


def get_sports_embed_urls(event_id: str) -> list[Dict[str, Any]]:
    """Return list of {number, language, embed_url} for a live sports event.

    Calls ``/api/sports?e={random_hex}&m={event_id}`` which returns a list of
    embed options; then resolves each through ``/api/iframe/u/{token}`` →
    redirect to embedsports.top.
    """
    import os
    random_e = os.urandom(8).hex()[:16]
    params = {"e": random_e, "m": event_id}
    headers = _build_headers()
    try:
        resp = _get_session().get(_BASE_URL + "/api/sports", params=params, headers=headers, timeout=15)
        resp.raise_for_status()
        servers_raw = resp.json()
    except Exception as exc:
        console.print(f"[red]mappl: /api/sports failed for {event_id}: {exc}")
        return []

    if not isinstance(servers_raw, list):
        return []

    results: list[Dict[str, Any]] = []
    for item in servers_raw:
        stream_no = item.get("streamNo", len(results) + 1)
        language = item.get("language", "English")
        embed_token = item.get("embedUrl", "")
        if not embed_token:
            continue
        # Resolve the embed token to an embedsports.top URL
        embed_url = _resolve_iframe(embed_token)
        if embed_url:
            results.append({
                "number": stream_no,
                "language": language,
                "embed_url": embed_url,
            })

    return results


def _resolve_iframe(token: str) -> Optional[str]:
    """GET /api/iframe/u/{token} and follow the 302 redirect to embedsports."""
    headers = _build_headers({"accept": "text/html,*/*"})
    try:
        resp = _get_session().get(
            _BASE_URL + f"/api/iframe/u/{token}",
            headers=headers,
            timeout=15,
            allow_redirects=False,
        )
    except Exception as exc:
        console.print(f"[yellow]mappl: /api/iframe/u/ failed: {exc}")
        return None

    # Follow redirect manually - curl_cffi may have already followed it
    location = None
    if hasattr(resp, "headers"):
        location = resp.headers.get("location") or resp.headers.get("Location")
    if not location:
        # Maybe curl_cffi followed the redirect - check final URL
        final = str(getattr(resp, "url", "") or "")
        if "embedsports" in final or "pooembed" in final:
            return final
        # Try parsing text body for iframe src
        raw = getattr(resp, "content", None)
        text = raw.decode("utf-8", errors="replace") if isinstance(raw, (bytes, bytearray)) else ""
        m = re.search(r'src="(https://(?:embedsports|pooembed)[^"]+)"', text)
        if m:
            return m.group(1)
        return None
    return location


def list_channels() -> list[Dict[str, Any]]:
    """Scrape the live-tv-premium RSC page for channel list.

    Returns list of dicts with keys: id (numeric), name, category, logo.
    """
    headers = _build_headers({
        "accept": "text/x-component",
        "rsc": "1",
    })
    try:
        resp = _get_session().get(_BASE_URL + "/live-tv-premium", headers=headers, timeout=20)
        resp.raise_for_status()
    except Exception as exc:
        console.print(f"[red]mappl: failed to fetch live-tv-premium: {exc}")
        return []

    raw = getattr(resp, "content", None)
    text = raw.decode("utf-8", errors="replace") if isinstance(raw, (bytes, bytearray)) else (resp.text or "")

    channels: list[Dict[str, Any]] = []
    seen: set[str] = set()
    # RSC format embeds channel objects like {"id":477,"name":"ESPN","category":"...","logo":"..."}
    for m in re.finditer(
        r'\{"id":(\d+),"name":"([^"]+)"(?:,"category":"([^"]*)")?(?:,"logo":"([^"]*)")?',
        text,
    ):
        cid = m.group(1)
        if cid in seen:
            continue
        seen.add(cid)
        channels.append({
            "id": int(cid),
            "name": m.group(2),
            "category": m.group(3) or "TV",
            "logo": m.group(4) or "",
            "watch_url": f"{_BASE_URL}/live-tv-premium/{cid}",
        })

    return channels
