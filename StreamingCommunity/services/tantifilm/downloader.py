# tantifilm downloader
# Delegates stream resolution to mappl.tv source_api because tantifilm
# embeds the mappletv.uk player, which is the same pipeline as mappl.tv.

import os

from rich.console import Console

from StreamingCommunity.utils import config_manager, start_message
from StreamingCommunity.services._base import site_constants, Entries
from StreamingCommunity.services._base.tv_display_manager import (
    map_movie_title, map_episode_title, map_season_name,
)
from StreamingCommunity.services._base.tv_download_manager import (
    process_season_selection, process_episode_download,
)
from StreamingCommunity.core.downloader import HLS_Downloader

from StreamingCommunity.services.mappl import source_api as mappl_api
from .scrapper import GetSerieInfo


console = Console()
extension_output = config_manager.config.get("PROCESS", "extension")


def download_film(select_title: Entries) -> str:
    start_message()
    console.print(f"\n[yellow]Download: [red]{site_constants.SITE_NAME} → [cyan]{select_title.name}\n")

    tmdb_id = select_title.tmdb_id or select_title.id
    if not tmdb_id:
        console.print("[red]tantifilm: no TMDB ID available for this title")
        return None

    hls_url = mappl_api.resolve_movie(int(tmdb_id))
    if not hls_url:
        console.print(f"[red]tantifilm: could not resolve stream for tmdb={tmdb_id}")
        return None

    title_name = f"{map_movie_title(select_title.name, select_title.year)}.{extension_output}"
    title_path = os.path.join(site_constants.MOVIE_FOLDER,
                              title_name.replace(f".{extension_output}", ""))

    return HLS_Downloader(
        m3u8_url=hls_url,
        output_path=os.path.join(title_path, title_name),
        headers=mappl_api.playback_headers(),
    ).start()


def download_episode(obj_episode, index_season_selected, index_episode_selected, scrape_serie, _video_source=None):
    start_message()
    series_display = getattr(scrape_serie, 'series_display_name', None) or scrape_serie.series_name
    console.print(
        f"\n[yellow]Download: [red]{site_constants.SITE_NAME} → [cyan]{series_display} "
        f"[white]\\ [magenta]{obj_episode.name} ([cyan]S{index_season_selected}E{index_episode_selected})\n"
    )

    tmdb_id = getattr(scrape_serie, 'tmdb_id', None)
    if not tmdb_id:
        console.print("[red]tantifilm: no TMDB ID available for series")
        return None

    hls_url = mappl_api.resolve_episode(int(tmdb_id), index_season_selected, index_episode_selected)
    if not hls_url:
        console.print(f"[red]tantifilm: could not resolve stream for tmdb={tmdb_id} S{index_season_selected}E{index_episode_selected}")
        return None

    episode_name = f"{map_episode_title(series_display, index_season_selected, index_episode_selected, obj_episode.name)}.{extension_output}"
    episode_path = os.path.join(site_constants.SERIES_FOLDER, series_display, map_season_name(index_season_selected))

    return HLS_Downloader(
        m3u8_url=hls_url,
        output_path=os.path.join(episode_path, episode_name),
        headers=mappl_api.playback_headers(),
    ).start()


def download_series(select_season: Entries, season_selection: str = None,
                    episode_selection: str = None, scrape_serie=None) -> None:
    start_message()

    if scrape_serie is None:
        tmdb_id = select_season.tmdb_id or select_season.id
        scrape_serie = GetSerieInfo(
            tmdb_id=tmdb_id,
            series_display_name=select_season.name,
            year=select_season.year,
        )
        scrape_serie.getNumberSeason()
        scrape_serie.series_display_name = select_season.name

    seasons_count = len(scrape_serie.seasons_manager)

    def download_episode_callback(season_number: int, download_all: bool, episode_selection: str = None):
        def download_video_callback(obj_episode, season_idx, episode_idx):
            return download_episode(obj_episode, season_idx, episode_idx, scrape_serie)

        process_episode_download(
            index_season_selected=season_number,
            scrape_serie=scrape_serie,
            download_video_callback=download_video_callback,
            download_all=download_all,
            episode_selection=episode_selection,
        )

    process_season_selection(
        scrape_serie=scrape_serie,
        seasons_count=seasons_count,
        season_selection=season_selection,
        episode_selection=episode_selection,
        download_episode_callback=download_episode_callback,
    )
