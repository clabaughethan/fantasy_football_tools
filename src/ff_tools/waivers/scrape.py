"""Optional scraper for waiver wire articles.

These are fragile and may break when sites change their layout.
Use as supplementary data only.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from urllib.parse import urljoin

import requests

# Anchor tags whose href mentions the waiver wire. FantasyPros' advice index
# mixes its own relative links with syndicated partner articles, so the href
# shape is matched loosely and the anchor text carries the headline.
_WAIVER_LINK = re.compile(
    r'<a\b[^>]*href="([^"]*waiver[^"]*)"[^>]*>(.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)
_TAGS = re.compile(r"<[^>]+>")

# Index pages listing current waiver-wire advice, tried in order.
FANTASYPROS_INDEXES = (
    "https://www.fantasypros.com/nfl/advice/",
    "https://www.fantasypros.com/nfl/waiver-wire-assistant.php",
)

# Links that mention "waiver" but are navigation, not articles.
_NAV_TITLES = frozenset({
    "waiver wire", "waiver wire assistant", "waivers", "waiver assistant",
    "waiver wire advice", "waiver wire pickups", "more waiver wire",
})

# Manual fallbacks, printed when nothing could be scraped. ESPN's fantasy pages
# reject scripted requests outright, so they are listed here rather than
# scraped - a scraper that always returns nothing is worse than a link.
MANUAL_SOURCES = (
    "https://www.fantasypros.com/nfl/waiver-wire.php",
    "https://www.espn.com/fantasy/football/",
)


@dataclass
class WaiverArticle:
    """A scraped waiver wire recommendation."""

    source: str
    title: str
    url: str = ""
    players: list[str] = field(default_factory=list)
    summary: str = ""


class WaiverScraper:
    """Scrape waiver wire article links from public sources.

    WARNING: These scrapers are fragile and may break when sites change their
    layout. They are provided as optional supplements to the API-based analysis.
    Nothing here is load-bearing - every method returns an empty list on
    failure rather than raising.
    """

    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        })

    def _fetch(self, url: str) -> str:
        """GET a page, returning an empty string on any failure."""
        try:
            resp = self.session.get(url, timeout=10)
            resp.raise_for_status()
            return resp.text
        except Exception:
            return ""

    def _extract(
        self, body: str, base_url: str, source: str, limit: int
    ) -> list[WaiverArticle]:
        """Pull waiver-wire article links out of an index page.

        Returns nothing when the page yields no usable links, rather than
        inventing a placeholder - a fabricated "see full article" entry looks
        like a successful scrape and hides the fact that the layout changed.
        """
        articles: list[WaiverArticle] = []
        seen: set[str] = set()

        for href, inner in _WAIVER_LINK.findall(body):
            url = urljoin(base_url, html.unescape(href))
            if url in seen:
                continue

            title = html.unescape(_TAGS.sub(" ", inner))
            title = re.sub(r"\s+", " ", title).strip()
            # Navigation links and bare icons carry no headline worth showing.
            if len(title) < 15 or title.lower() in _NAV_TITLES:
                continue

            seen.add(url)
            articles.append(WaiverArticle(source=source, title=title, url=url))
            if len(articles) >= limit:
                break

        return articles

    def scrape_fantasypros_waiver(self, limit: int = 8) -> list[WaiverArticle]:
        """Scrape current waiver wire articles from the FantasyPros index."""
        for index in FANTASYPROS_INDEXES:
            body = self._fetch(index)
            if not body:
                continue
            found = self._extract(body, index, "FantasyPros", limit)
            if found:
                return found
        return []

    def get_all_articles(self) -> list[WaiverArticle]:
        """Try to scrape all sources, return what succeeds."""
        return self.scrape_fantasypros_waiver()

    def print_articles(self) -> None:
        """Print scraped waiver wire articles."""
        articles = self.get_all_articles()
        if not articles:
            print("\n  No waiver articles could be fetched.")
            print("  Check these sources manually:")
            for url in MANUAL_SOURCES:
                print(f"    - {url}")
            return

        print("\n" + "=" * 60)
        print("  WAIVER WIRE ARTICLES")
        print("=" * 60)
        for a in articles:
            print(f"\n  [{a.source}] {a.title}")
            if a.url:
                print(f"    {a.url}")
            if a.summary:
                print(f"    {a.summary}")
        print("\n  Also worth a look:")
        for url in MANUAL_SOURCES:
            print(f"    - {url}")
        print()
