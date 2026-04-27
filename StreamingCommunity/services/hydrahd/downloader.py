# 21.04.26

import os


from rich.console import Console
from rich.prompt import Prompt


from StreamingCommunity.utils import config_manager, start_message
from StreamingCommunity.services._base import site_constants, Entries
from StreamingCommunity.services._base.tv_display_manager import map_movie_title, map_episode_title, map_season_name
from StreamingCommunity.services._base.tv_download_manager import process_season_selection, process_episode_download


from StreamingCommunity.core.downloader import HLS_Downloader


from .source_api import resolve_movie, resolve_episode, pick_best_stream, playback_headers
from .scrapper import GetSerieInfo


console = Console()
msg = Prompt()
extension_output = config_manager.config.get("PROCESS", "extension")


def _resolve_tmdb_from_entry(select_title: Entries) -> int | None:
    """HydraHD entries always carry a ``tmdb_id`` (we only surface titles we
    could resolve against TMDB). Accept numeric or numeric-string values.
    """
    raw = getattr(select_title, 'tmdb_id', None)
    try:
        return int(raw) if raw is not None and str(raw).strip() else None
    except (TypeError, ValueError):
        return None


def download_film(select_title: Entries) -> str | None:
    """Download a movie using the HydraHD source API."""
    start_message()
    console.print(f"\n[yellow]Download: [red]{site_constants.SITE_NAME} → [cyan]{select_title.name} \n")

    tmdb_id = _resolve_tmdb_from_entry(select_title)
    if not tmdb_id:
        console.print(f"[red]hydrahd: no TMDB id for '{select_title.name}' - cannot resolve stream.")
        return None

    payload = resolve_movie(tmdb_id)
    stream_url = pick_best_stream(payload)
    if not stream_url:
        console.print(f"[red]hydrahd: source API returned no playable stream for tmdb={tmdb_id}")
        return None

    title_name = f"{map_movie_title(select_title.name, select_title.year)}.{extension_output}"
    title_path = os.path.join(site_constants.MOVIE_FOLDER, title_name.replace(f".{extension_output}", ""))

    return HLS_Downloader(
        m3u8_url=stream_url,
        output_path=os.path.join(title_path, title_name),
        headers=playback_headers(),
    ).start()


def _download_episode(obj_episode, index_season_selected, index_episode_selected, scrape_serie):
    start_message()
    series_display = getattr(scrape_serie, 'series_display_name', None) or scrape_serie.series_name
    console.print(
        f"\n[yellow]Download: [red]{site_constants.SITE_NAME} → [cyan]{series_display} "
        f"[white]\\ [magenta]{obj_episode.name} ([cyan]S{index_season_selected}E{index_episode_selected})\n"
    )

    payload = resolve_episode(scrape_serie.tmdb_id, index_season_selected, index_episode_selected)
    stream_url = pick_best_stream(payload)
    if not stream_url:
        console.print(f"[red]hydrahd: no stream for S{index_season_selected}E{index_episode_selected}")
        return None, False

    episode_name = f"{map_episode_title(series_display, index_season_selected, index_episode_selected, obj_episode.name)}.{extension_output}"
    episode_path = os.path.join(site_constants.SERIES_FOLDER, series_display, map_season_name(index_season_selected))

    return HLS_Downloader(
        m3u8_url=stream_url,
        output_path=os.path.join(episode_path, episode_name),
        headers=playback_headers(),
    ).start()


def download_series(select_season: Entries, season_selection: str | None = None,
                    episode_selection: str | None = None, scrape_serie=None) -> None:
    """Handle downloading a complete series (or a selection)."""
    start_message()

    tmdb_id = _resolve_tmdb_from_entry(select_season)
    if not tmdb_id:
        console.print(f"[red]hydrahd: no TMDB id for '{select_season.name}' - cannot fetch seasons.")
        return

    if scrape_serie is None:
        scrape_serie = GetSerieInfo(
            tmdb_id=tmdb_id,
            series_display_name=select_season.name,
            year=getattr(select_season, 'year', None),
            imdb_id=getattr(select_season, 'imdb_id', None),
        )

    scrape_serie.getNumberSeason()
    seasons_count = len(scrape_serie.seasons_manager)

    def download_episode_callback(season_number: int, download_all: bool, episode_selection: str = None):
        def download_video_callback(obj_episode, season_idx, episode_idx):
            return _download_episode(obj_episode, season_idx, episode_idx, scrape_serie)

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
