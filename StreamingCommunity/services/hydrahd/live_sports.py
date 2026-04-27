# 21.04.26
#
# Scrape https://hydrahd.ru/livesports for the current event board.
# This is strictly "watch only" - each event provides one or more embed URLs
# (pointing at ``embedsports.top``) that the web UI drops into an iframe.
#
# The final HLS stream is behind a WASM-decrypted POST response served by
# ``pooembed.eu``; decoding it client-side in Python would mean running the
# ``gasm.wasm`` module, which is out of scope here. Iframing bypasses all of
# that complexity because the browser does the decrypt work itself.

from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional


from bs4 import BeautifulSoup
from rich.console import Console


from StreamingCommunity.utils import config_manager
from StreamingCommunity.utils.http_client import create_client_curl, get_userAgent


console = Console()

_LIVE_SPORTS_PATH = "/livesports"


class LiveSportsLoginRequired(Exception):
    """Raised when the live-sports page redirects us to /login."""


def _load_session_cookie() -> Optional[str]:
    """Return an optional HydraHD cookie string for authenticated scraping.

    Configure via ``Conf/login.json`` under ``hydrahd.session_cookie``. The
    expected value is the raw ``Cookie`` header copied from DevTools (at a
    minimum, ``PHPSESSID=...; cf_clearance=...``).

    As a convenience we also accept a bare PHPSESSID value (a 26-char
    alphanumeric string) and wrap it as ``PHPSESSID=<value>``.
    """
    try:
        raw = config_manager.login.get("hydrahd", "session_cookie", default="") or ""
    except Exception:
        raw = ""
    raw = raw.strip()
    if not raw:
        return None
    if "=" not in raw:
        # Bare token - assume it's the PHPSESSID.
        return f"PHPSESSID={raw}"
    return raw


@dataclass
class LiveServer:
    number: int
    embed_url: str


@dataclass
class LiveEvent:
    category: str
    title: str
    when: str
    home: Optional[str] = None
    away: Optional[str] = None
    servers: List[LiveServer] = field(default_factory=list)
    poster: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["servers"] = [asdict(s) for s in self.servers]
        return data


def _site_base_url() -> str:
    """Return the current HydraHD base URL from the shared domain config."""
    # Lazy import to keep this module importable without config side-effects.
    from StreamingCommunity.services._base import site_constants  # noqa: WPS433
    return site_constants.FULL_URL


def _fetch_live_html() -> Optional[str]:
    base = _site_base_url().rstrip("/")
    url = f"{base}{_LIVE_SPORTS_PATH}"
    headers = {
        "user-agent": get_userAgent(),
        "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "accept-language": "en-US,en;q=0.9",
        "referer": base + "/",
    }
    cookie = _load_session_cookie()
    if cookie:
        headers["cookie"] = cookie
    try:
        response = create_client_curl(headers=headers).get(url, timeout=20)
        response.raise_for_status()
    except Exception as exc:
        console.print(f"[red]hydrahd.live: failed to load {url}: {exc}")
        return None

    # HydraHD serves UTF-8 but doesn't always advertise it in the
    # Content-Type header - force the decoding so team/event names with
    # non-ASCII characters (Fenerbahçe, São Paulo, ...) render correctly.
    raw_bytes = getattr(response, "content", None)
    if isinstance(raw_bytes, (bytes, bytearray)) and raw_bytes:
        try:
            text = raw_bytes.decode("utf-8")
        except UnicodeDecodeError:
            text = raw_bytes.decode("utf-8", errors="replace")
    else:
        text = response.text or ""
    # HydraHD silently redirects unauthenticated visitors to /login, keeping
    # a 200 status but swapping the body. Detect that so the UI can explain.
    final_url = str(getattr(response, "url", "") or "")
    is_login_page = "/login" in final_url or "<title>Login to HydraHD" in text
    if is_login_page:
        raise LiveSportsLoginRequired(
            "HydraHD requires an authenticated session to view /livesports. "
            "Paste your browser's Cookie header into Conf/login.json -> hydrahd.session_cookie."
        )
    return text


def _clean(text: Optional[str]) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()


def _prettify_category(raw: str) -> str:
    """Turn ``motor-sports`` into ``Motor Sports`` for UI display."""
    if not raw:
        return "Live"
    cleaned = raw.replace("_", "-").replace("-", " ").strip()
    return cleaned.title()


# Matches the ``<p>`` line inside every event tile:
#   "Baseball: Cleveland Guardians vs Houston Astros - 2026-04-21 23:10 (GMT)"
_EVENT_LINE_RE = re.compile(
    r"^(?P<category>[^:]+):\s*(?P<title>.+?)\s*-\s*(?P<when>\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}\s*\([^)]+\))\s*$"
)

# A placeholder date HydraHD uses for always-on / no-schedule channels.
_PLACEHOLDER_DATE = "1970-01-01"


def list_events() -> List[LiveEvent]:
    """Parse the live-sports board.

    Returns an empty list when the site is unreachable rather than raising -
    the UI can just show a placeholder. Raises
    :class:`LiveSportsLoginRequired` when HydraHD redirects us to /login so
    the caller can surface a specific "set your cookie" message.
    """
    html = _fetch_live_html()
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    events: List[LiveEvent] = []

    for node in soup.find_all("div", class_="match"):
        # HydraHD conveniently stamps the category + title directly on the
        # element. Fall back to parsing the visible <p> line only if needed.
        data_category = node.get("data-category") or ""
        data_title = node.get("data-title") or ""

        when = ""
        p_el = node.find("p")
        if p_el:
            match = _EVENT_LINE_RE.match(_clean(p_el.get_text()))
            if match:
                when = match.group("when")
                if not data_category:
                    data_category = match.group("category")
                if not data_title:
                    data_title = match.group("title")

        # Drop the unhelpful 1970 placeholder the site uses for 24/7 channels.
        if when.startswith(_PLACEHOLDER_DATE):
            when = ""

        servers: List[LiveServer] = []
        for btn in node.find_all("button", class_="watch-now-btn"):
            embed = (btn.get("data-embed-url") or "").strip()
            if not embed.startswith("http"):
                continue
            label = _clean(btn.get_text())
            match_num = re.search(r"(\d+)", label)
            num = int(match_num.group(1)) if match_num else (len(servers) + 1)
            servers.append(LiveServer(number=num, embed_url=embed))

        if not servers:
            continue

        # Deduce "home vs away" when the title looks like ``A vs B``.
        home = away = None
        vs_split = re.split(r"\s+vs\.?\s+", data_title or "", maxsplit=1, flags=re.IGNORECASE)
        if len(vs_split) == 2:
            home, away = vs_split[0].strip(), vs_split[1].strip()

        events.append(LiveEvent(
            category=_prettify_category(data_category),
            title=data_title or (f"{home} vs {away}" if home and away else "Live event"),
            when=when,
            home=home,
            away=away,
            servers=sorted(servers, key=lambda s: s.number),
            poster=None,
        ))

    return events


def events_as_dicts() -> List[Dict[str, Any]]:
    return [event.to_dict() for event in list_events()]
