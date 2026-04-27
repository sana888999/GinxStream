# streamingunity.autos adapter
# Identical backend to streamingcommunity (Inertia.js + vixcloud.co).
# site_constants.FULL_URL resolves to https://streamingunity.autos/ via domains.json.

import json


# External library
from bs4 import BeautifulSoup
from rich.console import Console
from rich.prompt import Prompt


# Internal utilities
from StreamingCommunity.utils import TVShowManager
from StreamingCommunity.source.utils.tracker import context_tracker
from StreamingCommunity.utils.http_client import create_client, get_userAgent
from StreamingCommunity.services._base import site_constants, EntriesManager, Entries
from StreamingCommunity.services._base.site_search_manager import base_process_search_result, base_search


# Logic
from .downloader import download_series, download_film


# Variable
indice = 15
_useFor = "Film_Serie"


msg = Prompt()
console = Console()
entries_manager = EntriesManager()
table_show_manager = TVShowManager()


def title_search(query: str) -> int:
    """Search for titles on streamingunity.autos in both IT and EN languages."""
    entries_manager.clear()
    table_show_manager.clear()

    seen_ids = set()
    # Web GUI should be English-only (no Italian entries at all).
    languages = ['en'] if getattr(context_tracker, "is_gui", False) else ['it', 'en']

    for lang in languages:
        console.print(f"[cyan]Searching in language: [yellow]{lang}")

        try:
            response = create_client(headers={'user-agent': get_userAgent()}).get(
                f"{site_constants.FULL_URL}/{lang}"
            )
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')
            version = json.loads(soup.find('div', {'id': "app"}).get("data-page"))['version']
        except Exception as e:
            console.print(f"[red]Site: {site_constants.SITE_NAME} version ({lang}), request error: {e}")
            continue

        search_url = f"{site_constants.FULL_URL}/{lang}/search?q={query}"
        console.print(f"[cyan]Search url: [yellow]{search_url}")

        try:
            response = create_client(headers={
                'user-agent': get_userAgent(),
                'x-inertia': 'true',
                'x-inertia-version': version,
            }).get(search_url)
            response.raise_for_status()
        except Exception as e:
            console.print(f"[red]Site: {site_constants.SITE_NAME} ({lang}), request search error: {e}")
            continue

        try:
            data = response.json().get('props', {}).get('titles', [])
        except Exception as e:
            console.print(f"[red]Error parsing JSON response ({lang}): {e}")
            continue

        for dict_title in data:
            try:
                title_id = dict_title.get('id')
                if title_id in seen_ids:
                    continue
                seen_ids.add(title_id)

                images = dict_title.get('images') or []
                filename = None
                for ptype in ['poster', 'cover', 'cover_mobile', 'background']:
                    for img in images:
                        if img.get('type') == ptype and img.get('filename'):
                            filename = img.get('filename')
                            break
                    if filename:
                        break
                if not filename and images:
                    filename = images[0].get('filename')

                image_url = None
                if filename:
                    cdn = site_constants.FULL_URL.rstrip('/')
                    # streamingunity.autos → cdn.streamingunity.autos
                    cdn = cdn.replace('https://', 'https://cdn.', 1)
                    image_url = f"{cdn}/images/{filename}"

                year = None
                for trans in dict_title.get('translations') or []:
                    if trans.get('key') in ('first_air_date', 'release_date') and trans.get('value'):
                        year = trans['value']
                        break
                if not year:
                    year = dict_title.get('last_air_date') or dict_title.get('release_date')

                entries_manager.add(Entries(
                    id=title_id,
                    slug=dict_title.get('slug'),
                    name=dict_title.get('name'),
                    type=dict_title.get('type'),
                    image=image_url,
                    year=year.split("-")[0] if year and "-" in year else "9999",
                    provider_language=lang,
                ))
            except Exception as e:
                console.print(f"[red]Error parsing entry ({lang}): {e}")

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


def search(string_to_search: str = None, get_onlyDatabase: bool = False,
           direct_item: dict = None, selections: dict = None, scrape_serie=None):
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
