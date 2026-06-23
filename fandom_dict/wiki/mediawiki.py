"""MediaWiki/Fandom URL helpers and API client."""

from __future__ import annotations

import json
import logging
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any


RETRY_STATUS_CODES = {403, 408, 429, 500, 502, 503, 504}
LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class PageRef:
    """Minimal page identity returned by MediaWiki category listings."""

    pageid: int
    title: str
    ns: int


@dataclass(frozen=True)
class RequestConfig:
    """Network and retry settings shared by all MediaWiki API requests."""

    user_agent: str
    timeout: float
    max_retries: int
    initial_backoff: float
    max_backoff: float


class MediaWikiClient:
    """Small MediaWiki API client with retry/backoff behavior."""

    def __init__(self, fandom_slug: str, config: RequestConfig) -> None:
        self.fandom_slug = fandom_slug
        self.config = config

    def request(self, params: dict[str, Any]) -> dict[str, Any]:
        """Call the configured MediaWiki API endpoint with retry/backoff behavior."""

        return api_request(self.api_url, params, self.config)

    @property
    def api_url(self) -> str:
        """Return the MediaWiki API URL for the configured Fandom wiki."""

        return fandom_api_url(self.fandom_slug)

    def page_url(self, title: str) -> str:
        """Return the canonical wiki page URL for a title."""

        return wiki_page_url(self.fandom_slug, title)

    def category_title(self, category: str) -> str:
        """Return the canonical MediaWiki category title for a category name."""

        return wiki_category_title(category)

    def category_members(
        self,
        category: str,
        batch_size: int,
        max_pages: int,
        delay: float,
    ) -> list[PageRef]:
        """Return namespace-0 pages from a category, following API continuation."""

        pages: list[PageRef] = []
        continuation: dict[str, Any] = {}

        while True:
            params: dict[str, Any] = {
                "action": "query",
                "format": "json",
                "list": "categorymembers",
                "cmtitle": self.category_title(category),
                "cmnamespace": "0",
                "cmtype": "page",
                "cmprop": "ids|title|type",
                "cmlimit": str(batch_size),
            }
            params.update(continuation)
            data = self.request(params)
            members = data.get("query", {}).get("categorymembers", [])
            for item in members:
                pages.append(
                    PageRef(
                        pageid=int(item["pageid"]),
                        title=item["title"],
                        ns=int(item["ns"]),
                    )
                )
                if max_pages and len(pages) >= max_pages:
                    return pages

            continuation = data.get("continue") or {}
            if not continuation:
                return pages

            time.sleep(jitter(delay))

    def parse_page(self, page: PageRef) -> dict[str, Any]:
        """Fetch parsed HTML and metadata for a single page."""

        return self.request(
            {
                "action": "parse",
                "format": "json",
                "pageid": page.pageid,
                "prop": "text|revid|displaytitle|categories",
                "disableeditsection": "1",
                "disabletoc": "1",
                "redirects": "1",
            }
        )


def api_base(api_url: str) -> str:
    """Return the scheme and host portion of a MediaWiki API URL."""

    parsed = urllib.parse.urlparse(api_url)
    return f"{parsed.scheme}://{parsed.netloc}"


def fandom_api_url(fandom_slug: str) -> str:
    """Return the API endpoint for a Fandom wiki slug."""

    return f"https://{fandom_slug}.fandom.com/api.php"


def fandom_page_base_url(fandom_slug: str) -> str:
    """Return the wiki base URL for a Fandom wiki slug."""

    return f"https://{fandom_slug}.fandom.com/wiki/"


def wiki_category_title(category: str) -> str:
    """Return the canonical MediaWiki category title for a category name."""

    return category if category.startswith("Category:") else f"Category:{category}"


def wiki_page_url(fandom_slug: str, title: str) -> str:
    """Build a human-readable wiki page URL from a Fandom wiki slug and page title."""

    return f"{fandom_page_base_url(fandom_slug)}{urllib.parse.quote(title.replace(' ', '_'))}"


def api_request(
    api_url: str,
    params: dict[str, Any],
    config: RequestConfig,
) -> dict[str, Any]:
    """Perform one JSON API request with bounded exponential backoff."""

    query = urllib.parse.urlencode(params, doseq=True)
    url = f"{api_url}?{query}"
    delay = config.initial_backoff

    for attempt in range(config.max_retries + 1):
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": config.user_agent,
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=config.timeout) as response:
                encoding = response.headers.get_content_charset() or "utf-8"
                return json.loads(response.read().decode(encoding))
        except urllib.error.HTTPError as exc:
            if exc.code not in RETRY_STATUS_CODES or attempt == config.max_retries:
                raise
            retry_after = exc.headers.get("Retry-After")
            sleep_for = parse_retry_after(retry_after) or jitter(delay)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            if attempt == config.max_retries:
                raise
            sleep_for = jitter(delay)

        LOGGER.warning("request failed; retrying in %.1fs: %s", sleep_for, url)
        time.sleep(sleep_for)
        delay = min(delay * 2, config.max_backoff)

    raise RuntimeError("unreachable retry state")


def parse_retry_after(value: str | None) -> float | None:
    """Parse an HTTP Retry-After value when it is expressed as seconds."""

    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None


def jitter(seconds: float) -> float:
    """Return a randomized delay so repeated requests do not land mechanically."""

    return seconds * random.uniform(0.75, 1.25)
