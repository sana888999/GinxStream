# 21.04.26
#
# Live NBA streams via nbamonster.com.
#
# Structure (from HAR analysis):
#   nbamonster.com/teams/{team-slug}-live/  →  team stream page with 6 tabs
#   Their tab UI iframes piratecat.online → embedsports.top → pooembed.eu → HLS
#
# Live-game schedule comes from the same public ESPN API that nbamonster.com
# uses for its scoreboard widget. We return one event per game and iframe the
# nbamonster team page itself (not the downstream player) to avoid upstream
# anti-embed referrer/origin blocks like:
#   \"Forbidden — Access denied from this domain.\"

from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from typing import Any, Dict, List

from rich.console import Console

from StreamingCommunity.utils.http_client import create_client_curl, get_userAgent

console = Console()

_NBAMONSTER_BASE = "https://nbamonster.com"
_ESPN_API        = "https://site.api.espn.com/apis/v2/scoreboard/header"
_ESPN_PARAMS     = {
    "sport": "basketball",
    "league": "nba",
    "lang": "en",
    "region": "ww",
    "contentorigin": "espn",
}

# --------------------------------------------------------------------------- #
# ESPN abbreviation → nbamonster.com team slug                                 #
# --------------------------------------------------------------------------- #
_ABBR_TO_SLUG: Dict[str, str] = {
    "ATL": "atlanta-hawks",
    "BOS": "boston-celtics",
    "BKN": "brooklyn-nets",
    "CHA": "charlotte-hornets",
    "CHI": "chicago-bulls",
    "CLE": "cleveland-cavaliers",
    "DAL": "dallas-mavericks",
    "DEN": "denver-nuggets",
    "DET": "detroit-pistons",
    "GSW": "golden-state-warriors",
    "HOU": "houston-rockets",
    "IND": "indiana-pacers",
    "LAC": "la-clipper",          # note: nbamonster uses singular "clipper"
    "LAL": "los-angeles-lakers",
    "MEM": "memphis-grizzlies",
    "MIA": "miami-heat",
    "MIL": "milwaukee-bucks",
    "MIN": "minnesota-timberwolves",
    "NOP": "new-orleans-pelicans",
    "NYK": "new-york-knicks",
    "OKC": "oklahoma-city-thunder",
    "ORL": "orlando-magic",
    "PHI": "philadelphia-76ers",
    "PHX": "phoenix-suns",
    "POR": "portland-trail-blazers",
    "SAC": "sacramento-kings",
    "SAS": "san-antonio-spurs",
    "TOR": "toronto-raptors",
    "UTA": "utah-jazz",
    "WAS": "washington-wizards",
}


def _slug_from_name(display_name: str, abbr: str = "") -> str:
    """Map a team display name / abbreviation to a nbamonster.com slug."""
    if abbr and abbr.upper() in _ABBR_TO_SLUG:
        return _ABBR_TO_SLUG[abbr.upper()]
    # Fallback: lowercase + spaces → hyphens
    return re.sub(r"\s+", "-", display_name.strip().lower())


def _team_page_url(team_slug: str) -> str:
    return f"{_NBAMONSTER_BASE}/teams/{team_slug}-live/"


@dataclass
class NBAEvent:
    title: str
    home_team: str
    away_team: str
    home_slug: str
    category: str = "Basketball"

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        # Embed nbamonster directly; their page contains the working server tabs.
        d["servers"]    = [{"number": 1, "embed_url": _team_page_url(self.home_slug)}]
        d["poster"]     = None
        d["when"]       = ""
        d["event_id"]   = ""
        return d


def list_events() -> List[NBAEvent]:
    """Return today's live / upcoming NBA games from the ESPN scoreboard API.

    Falls back to an empty list (never raises) so the caller can show a
    "no games today" placeholder without breaking the rest of the page.
    """
    headers = {
        "user-agent":       get_userAgent(),
        "accept":           "application/json, text/javascript, */*; q=0.01",
        "accept-language":  "en-US,en;q=0.9",
        "referer":          f"{_NBAMONSTER_BASE}/",
    }
    try:
        resp = create_client_curl(headers=headers).get(
            _ESPN_API, params=_ESPN_PARAMS, timeout=15
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        console.print(f"[yellow]nbamonster: ESPN API failed ({exc})")
        return []

    events: List[NBAEvent] = []
    for sport in data.get("sports", []):
        for league in sport.get("leagues", []):
            for ev in league.get("events", []):
                competitors = ev.get("competitors", [])
                if len(competitors) < 2:
                    continue

                # ESPN marks each competitor as home / away
                home = next((c for c in competitors if c.get("homeAway") == "home"), competitors[0])
                away = next((c for c in competitors if c.get("homeAway") == "away"), competitors[1])

                home_name = home.get("displayName") or home.get("name", "")
                away_name = away.get("displayName") or away.get("name", "")
                home_abbr = home.get("abbreviation", "")
                away_abbr = away.get("abbreviation", "")  # noqa: F841 (kept for future use)

                if not home_name:
                    continue

                home_slug = _slug_from_name(home_name, home_abbr)

                events.append(NBAEvent(
                    title=f"{away_name} vs {home_name}",
                    home_team=home_name,
                    away_team=away_name,
                    home_slug=home_slug,
                ))

    if not events:
        console.print("[dim]nbamonster: no NBA games today from ESPN API[/dim]")

    return events


def events_as_dicts() -> List[Dict[str, Any]]:
    return [ev.to_dict() for ev in list_events()]
