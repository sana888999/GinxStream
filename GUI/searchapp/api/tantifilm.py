# GUI API wrapper for tantifilm.online
# Search via HTML scraping; streams resolved through mappl.tv pipeline.

import importlib
from typing import List, Optional

from .base import BaseStreamingAPI, Entries, Season, Episode

from StreamingCommunity.utils import config_manager
from StreamingCommunity.services._base.site_loader import get_folder_name
from StreamingCommunity.services.tantifilm.scrapper import GetSerieInfo


class TantifilmAPI(BaseStreamingAPI):
    def __init__(self):
        super().__init__()
        self.site_name = "tantifilm"
        self._load_config()
        self._search_fn = None

    def _load_config(self):
        self.base_url = config_manager.domain.get(self.site_name, "full_url",
                                                    default="https://tantifilm.online")
        print(f"[{self.site_name}] Configuration loaded: base_url={self.base_url}")

    def _get_search_fn(self):
        if self._search_fn is None:
            module = importlib.import_module(
                f"StreamingCommunity.{get_folder_name()}.{self.site_name}"
            )
            self._search_fn = getattr(module, "search")
        return self._search_fn

    def search(self, query: str) -> List[Entries]:
        search_fn = self._get_search_fn()
        database = search_fn(query, get_onlyDatabase=True)

        results: List[Entries] = []
        if database and hasattr(database, "media_list"):
            for element in list(database.media_list):
                item_dict = element.__dict__.copy() if hasattr(element, "__dict__") else {}
                results.append(Entries(
                    id=item_dict.get("id"),
                    name=item_dict.get("name"),
                    slug=item_dict.get("slug", ""),
                    type=item_dict.get("type") or "film",
                    url=item_dict.get("url"),
                    poster=item_dict.get("image"),
                    year=item_dict.get("year"),
                    tmdb_id=item_dict.get("tmdb_id"),
                    provider_language=item_dict.get("provider_language", "it"),
                    raw_data=item_dict,
                ))
        return results

    def get_series_metadata(self, media_item: Entries) -> Optional[List[Season]]:
        if media_item.is_movie:
            return None

        tmdb_id = media_item.tmdb_id or (media_item.raw_data or {}).get("tmdb_id")
        if not tmdb_id:
            return None

        scrape_serie = self.get_cached_scraper(media_item)
        if not scrape_serie:
            scrape_serie = GetSerieInfo(
                tmdb_id=tmdb_id,
                series_display_name=media_item.name,
                year=media_item.year,
            )
            self.set_cached_scraper(media_item, scrape_serie)

        seasons_count = scrape_serie.getNumberSeason()
        if not seasons_count:
            return None

        seasons: List[Season] = []
        for season in scrape_serie.seasons_manager.seasons:
            episodes_raw = scrape_serie.getEpisodeSeasons(season.number) or []
            episodes = [
                Episode(
                    number=getattr(ep, "number", idx),
                    name=getattr(ep, "name", f"Episode {idx}"),
                    id=getattr(ep, "id", idx),
                )
                for idx, ep in enumerate(episodes_raw, 1)
            ]
            seasons.append(Season(number=season.number, episodes=episodes,
                                  name=season.name))
            print(f"[tantifilm] Season {season.number} ({season.name}): {len(episodes)} episodes")

        return seasons or None

    def start_download(self, media_item: Entries, season: Optional[str] = None,
                       episodes: Optional[str] = None) -> bool:
        search_fn = self._get_search_fn()
        selections = None
        if season or episodes:
            selections = {"season": season, "episode": episodes}
        scrape_serie = self.get_cached_scraper(media_item)
        search_fn(direct_item=media_item.raw_data, selections=selections,
                  scrape_serie=scrape_serie)
        return True
