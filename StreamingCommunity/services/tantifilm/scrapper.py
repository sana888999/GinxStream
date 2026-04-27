# 21.04.26
#
# TMDB-backed season/episode metadata for mappl.tv.
# mappl.tv uses TMDB IDs so we query TMDB directly.
# Identical pattern to hydrahd scrapper.

from __future__ import annotations

from typing import Dict, List, Optional


from rich.console import Console


from StreamingCommunity.services._base.object import SeasonManager, Season, Episode
from StreamingCommunity.utils.tmdb_client import tmdb_client


console = Console()


class GetSerieInfo:
    """TMDB-backed season/episode metadata loader for mappl.tv."""

    def __init__(self, tmdb_id: int | str, series_display_name: str,
                 year: Optional[str] = None, imdb_id: Optional[str] = None,
                 provider_language: str = "en"):
        self.tmdb_id = int(tmdb_id) if tmdb_id else None
        self.series_display_name = series_display_name
        self.series_name = series_display_name
        self.year = year
        self.imdb_id = imdb_id
        self.provider_language = provider_language
        self.seasons_manager = SeasonManager()
        self._loaded = False
        self._episode_cache: Dict[int, List[Episode]] = {}

    def _ensure_seasons_loaded(self) -> None:
        if self._loaded or not self.tmdb_id:
            return

        details = tmdb_client._make_request(f"tv/{self.tmdb_id}", {"language": "en-US"})
        if not isinstance(details, dict):
            return

        if not self.imdb_id:
            external = tmdb_client._make_request(f"tv/{self.tmdb_id}/external_ids", {})
            if isinstance(external, dict):
                self.imdb_id = external.get("imdb_id") or self.imdb_id

        for season_data in details.get("seasons") or []:
            number = season_data.get("season_number")
            if number is None or int(number) == 0:
                continue
            self.seasons_manager.add(Season(
                id=season_data.get("id"),
                number=int(number),
                name=season_data.get("name") or f"Season {number}",
            ))

        self._loaded = True

    def getNumberSeason(self) -> int:
        self._ensure_seasons_loaded()
        return len(self.seasons_manager)

    def getEpisodeSeasons(self, season_number: int) -> List[Episode]:
        self._ensure_seasons_loaded()
        season_number = int(season_number)
        if season_number in self._episode_cache:
            return self._episode_cache[season_number]

        payload = tmdb_client._make_request(
            f"tv/{self.tmdb_id}/season/{season_number}",
            {"language": "en-US"},
        )
        episodes: List[Episode] = []
        if isinstance(payload, dict):
            for data in payload.get("episodes", []) or []:
                if not isinstance(data, dict):
                    continue
                number = data.get("episode_number")
                if number is None:
                    continue
                episodes.append(Episode(
                    id=data.get("id") or number,
                    number=int(number),
                    name=data.get("name") or f"Episode {number}",
                ))

        season = self.seasons_manager.get_season_by_number(season_number)
        if season is not None:
            for ep in episodes:
                season.episodes.add(ep)

        self._episode_cache[season_number] = episodes
        return episodes
