# tantifilm.online adapter
#
# Search: HTML scrape of /pages/search.php?query={q}&type=multi
# Metadata: TMDB-backed (TMDB ID embedded in page URLs)
# Streaming: resolved via mappl.tv source_api (tantifilm embeds mappletv.uk player)

import re

from bs4 import BeautifulSoup
from rich.console import Console
from rich.prompt import Prompt

from StreamingCommunity.utils import TVShowManager
from StreamingCommunity.utils.http_client import create_client, get_userAgent
from StreamingCommunity.services._base import site_constants, EntriesManager, Entries
from StreamingCommunity.services._base.site_search_manager import base_process_search_result, base_search

from .downloader import download_series, download_film


indice = 16
_useFor = "Film_Serie"

msg = Prompt()
console = Console()
entries_manager = EntriesManager()
table_show_manager = TVShowManager()


def _extract_tmdb_id(path: str) -> int:
    """Extract the TMDB numeric ID from a tantifilm URL path like /tv/vikings-44217."""
    m = re.search(r'-(\d+)$', path.rstrip('/'))
    return int(m.group(1)) if m else None


def title_search(query: str) -> int:
    """Search tantifilm.online for movies and TV shows."""
    entries_manager.clear()
    table_show_manager.clear()

    base_url = site_constants.FULL_URL.rstrip('/')
    search_url = f"{base_url}/pages/search.php?query={query}&type=multi"
    console.print(f"[cyan]Searching: [yellow]{search_url}")

    try:
        headers = {'user-agent': get_userAgent(), 'referer': base_url + '/'}
        resp = create_client(headers=headers).get(search_url, timeout=15)
        resp.raise_for_status()
        html = resp.text
    except Exception as e:
        console.print(f"[red]tantifilm search error: {e}")
        return 0

    soup = BeautifulSoup(html, 'html.parser')

    # Each result card is <div class="movie-card ..."><a href="/tv/{slug}-{id}">...
    seen_ids = set()
    for card in soup.select('div.movie-card a[href]'):
        href = card.get('href', '')
        if not re.match(r'/(tv|movie)/', href):
            continue

        tmdb_id = _extract_tmdb_id(href)
        if not tmdb_id or tmdb_id in seen_ids:
            continue
        seen_ids.add(tmdb_id)

        media_type = 'tv' if href.startswith('/tv/') else 'film'
        slug = href.split('/')[-1]

        # Title from <h3>
        h3 = card.find('h3')
        title_name = h3.get_text(strip=True) if h3 else slug

        # Poster from <img src="...tmdb...">
        img = card.find('img')
        poster = img.get('src', '') if img else ''
        if not poster.startswith('http'):
            poster = None

        # Year from first 4-digit number near a date pattern
        year_m = re.search(r'\b(19|20)\d{2}\b', card.get_text())
        year = year_m.group(0) if year_m else "9999"

        entries_manager.add(Entries(
            id=tmdb_id,
            slug=slug,
            name=title_name,
            type=media_type,
            image=poster,
            year=year,
            tmdb_id=tmdb_id,
            provider_language='it',
        ))

    console.print(f"[cyan]tantifilm found [yellow]{len(entries_manager)}[cyan] results")
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
