# 21.04.26

from __future__ import annotations

import os
import re


from rich.console import Console
from rich.prompt import Prompt


from StreamingCommunity.utils import config_manager, start_message
from StreamingCommunity.services._base import site_constants, Entries
from StreamingCommunity.services._base.tv_display_manager import map_movie_title, map_episode_title, map_season_name
from StreamingCommunity.services._base.tv_download_manager import process_season_selection, process_episode_download
from StreamingCommunity.core.downloader import HLS_Downloader
from StreamingCommunity.source.utils.tracker import download_tracker, context_tracker

from .source_api import (
    resolve_movie, resolve_episode, playback_headers,
    get_audiobook_parts, audiobook_download_headers,
)
from .scrapper import GetSerieInfo


console = Console()
msg = Prompt()
extension_output = config_manager.config.get("PROCESS", "extension")


def _tmdb_from_entry(select_title: Entries) -> int | None:
    raw = getattr(select_title, "tmdb_id", None)
    try:
        return int(raw) if raw is not None and str(raw).strip() else None
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# VOD
# ---------------------------------------------------------------------------

def download_film(select_title: Entries) -> str | None:
    start_message()
    console.print(f"\n[yellow]Download: [red]{site_constants.SITE_NAME} → [cyan]{select_title.name}\n")

    tmdb_id = _tmdb_from_entry(select_title)
    if not tmdb_id:
        console.print(f"[red]mappl: no TMDB id for '{select_title.name}'")
        return None

    stream_url = resolve_movie(tmdb_id)
    if not stream_url:
        console.print(f"[red]mappl: source API returned no stream for tmdb={tmdb_id}")
        return None

    title_name = f"{map_movie_title(select_title.name, select_title.year)}.{extension_output}"
    title_path = os.path.join(site_constants.MOVIE_FOLDER, title_name.replace(f".{extension_output}", ""))

    return HLS_Downloader(
        m3u8_url=stream_url,
        output_path=os.path.join(title_path, title_name),
        headers=playback_headers(),
    ).start()


def _download_episode(obj_episode, index_season_selected: int, index_episode_selected: int, scrape_serie):
    start_message()
    series_display = getattr(scrape_serie, "series_display_name", None) or scrape_serie.series_name
    console.print(
        f"\n[yellow]Download: [red]{site_constants.SITE_NAME} → [cyan]{series_display} "
        f"[white]\\ [magenta]{obj_episode.name} ([cyan]S{index_season_selected}E{index_episode_selected})\n"
    )

    stream_url = resolve_episode(scrape_serie.tmdb_id, index_season_selected, index_episode_selected)
    if not stream_url:
        console.print(f"[red]mappl: no stream for S{index_season_selected}E{index_episode_selected}")
        return None, False

    episode_name = (
        f"{map_episode_title(series_display, index_season_selected, index_episode_selected, obj_episode.name)}"
        f".{extension_output}"
    )
    episode_path = os.path.join(
        site_constants.SERIES_FOLDER, series_display, map_season_name(index_season_selected)
    )

    return HLS_Downloader(
        m3u8_url=stream_url,
        output_path=os.path.join(episode_path, episode_name),
        headers=playback_headers(),
    ).start()


def download_series(select_season: Entries, season_selection: str | None = None,
                    episode_selection: str | None = None, scrape_serie=None) -> None:
    start_message()

    tmdb_id = _tmdb_from_entry(select_season)
    if not tmdb_id:
        console.print(f"[red]mappl: no TMDB id for '{select_season.name}'")
        return

    if scrape_serie is None:
        scrape_serie = GetSerieInfo(
            tmdb_id=tmdb_id,
            series_display_name=select_season.name,
            year=getattr(select_season, "year", None),
            imdb_id=getattr(select_season, "imdb_id", None),
        )

    scrape_serie.getNumberSeason()

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
        seasons_count=len(scrape_serie.seasons_manager),
        season_selection=season_selection,
        episode_selection=episode_selection,
        download_episode_callback=download_episode_callback,
    )


# ---------------------------------------------------------------------------
# Audiobooks
# ---------------------------------------------------------------------------

def download_audiobook(book_id: str, slug: str, title: str) -> list[str | None]:
    """Download all parts of an audiobook as MP3 files.

    Returns list of output paths (one per part).

    When ``context_tracker.download_id`` is set (Cryter GUI thread), registers with
    ``download_tracker`` for live progress / kill between parts.
    """
    gui_id = getattr(context_tracker, "download_id", None)
    gui_site = getattr(context_tracker, "site_name", None) or site_constants.SITE_NAME
    gui_media = getattr(context_tracker, "media_type", None) or "Audiobook"

    if not gui_id:
        start_message()

    safe_title = re.sub(r'[\\/:*?"<>|]', "_", title)
    output_dir = os.path.join(site_constants.MOVIE_FOLDER, "Audiobooks", safe_title)
    os.makedirs(output_dir, exist_ok=True)

    parts = get_audiobook_parts(book_id, slug)
    if not parts:
        console.print(f"[red]mappl: no audio parts found for audiobook '{title}' ({book_id})")
        if gui_id:
            download_tracker.start_download(gui_id, title, gui_site, gui_media)
            download_tracker.complete_download(gui_id, success=False, error="No audio parts found")
        return []

    if gui_id:
        download_tracker.start_download(gui_id, title, gui_site, gui_media)
        download_tracker.update_status(gui_id, "downloading")

    console.print(f"[cyan]mappl: downloading audiobook '{title}' — {len(parts)} part(s)")
    headers = audiobook_download_headers()
    results: list[str | None] = []
    total = len(parts)

    for idx, part_url in enumerate(parts, 1):
        if gui_id and download_tracker.is_stopped(gui_id):
            download_tracker.complete_download(gui_id, success=False, error="cancelled")
            return results

        out_path = os.path.join(output_dir, f"{safe_title}_part{idx:02d}.mp3")
        console.print(f"  [cyan]Part {idx}/{total}: {out_path}")
        if gui_id:
            download_tracker.update_status(gui_id, f"Downloading part {idx}/{total} …")
            download_tracker.update_progress(
                gui_id,
                "audio_audiobook",
                progress=max(0.0, ((idx - 1) / total) * 100),
                segments=f"{idx}/{total}",
                size="—",
                speed="—",
            )

        try:
            from StreamingCommunity.utils.http_client import create_client_curl

            resp = create_client_curl(headers=headers).get(part_url, timeout=300)
            resp.raise_for_status()
            content = getattr(resp, "content", None) or resp.text.encode()
            with open(out_path, "wb") as f:
                f.write(content)
            nbytes = len(content)
            console.print(f"  [green]✓ Part {idx} saved ({nbytes:,} bytes)")
            results.append(out_path)
            if gui_id:
                download_tracker.update_progress(
                    gui_id,
                    "audio_audiobook",
                    progress=(idx / total) * 100,
                    segments=f"{idx}/{total}",
                    size=f"{nbytes / (1024 * 1024):.2f} MiB",
                    speed="—",
                )
        except Exception as exc:
            console.print(f"  [red]✗ Part {idx} failed: {exc}")
            results.append(None)

    if gui_id:
        any_ok = any(r for r in results)
        all_ok = results and all(r for r in results)
        if all_ok:
            download_tracker.complete_download(gui_id, success=True, path=output_dir)
        elif any_ok:
            download_tracker.complete_download(
                gui_id, success=False, error="Some audiobook parts failed"
            )
        else:
            download_tracker.complete_download(
                gui_id, success=False, error="Audiobook download failed"
            )

    return results
