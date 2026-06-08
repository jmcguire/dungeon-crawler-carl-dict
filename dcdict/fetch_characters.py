#!/usr/bin/env python3
"""Fetch raw character page data from a MediaWiki/Fandom category."""

from __future__ import annotations

import argparse
import html as html_lib
import json
import random
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any


DEFAULT_API_URL = "https://dungeon-crawler-carl.fandom.com/api.php"
DEFAULT_CATEGORY = "Category:Characters"
DEFAULT_USER_AGENT = "KindleDictionaryCreationCrawler/0.1"
RETRY_STATUS_CODES = {403, 408, 429, 500, 502, 503, 504}


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


@dataclass(frozen=True)
class CrawlConfig:
    """Category traversal settings for a crawler run."""

    category: str
    delay: float
    max_pages: int
    category_batch_size: int
    refresh: bool


class MediaWikiClient:
    """Small MediaWiki API client with retry/backoff behavior."""

    def __init__(self, api_url: str, config: RequestConfig) -> None:
        self.api_url = api_url
        self.config = config

    def request(self, params: dict[str, Any]) -> dict[str, Any]:
        """Call the configured MediaWiki API endpoint with retry/backoff behavior."""

        return api_request(self.api_url, params, self.config)

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
                "cmtitle": category,
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


class FirstParagraphParser(HTMLParser):
    """Extract the first meaningful paragraph while ignoring common chrome."""

    ALLOWED_INLINE_TAGS = {"b": "b", "strong": "b", "i": "i", "em": "i"}
    SKIP_TAGS = {"aside", "blockquote", "dl", "figure", "script", "style", "sup", "table"}
    VOID_TAGS = {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "source",
        "track",
        "wbr",
    }
    INLINE_TAGS = {"a", "b", "cite", "code", "em", "i", "small", "span", "strong"}
    SKIP_CLASSES = (
        "infobox",
        "portable-infobox",
        "toc",
        "mw-editsection",
        "reference",
        "noprint",
        "dcc-highlight",
    )

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_until: list[str] = []
        self._paragraph_depth = 0
        self._chunks: list[str] = []
        self._loose_chunks: list[str] = []
        self._inline_stack: list[str] = []
        self.first_paragraph = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        # Fandom pages can contain malformed or loosely nested generated HTML.
        # Tracking the tag that opened a skipped region is more tolerant than
        # blindly counting every nested start/end tag.
        if self._skip_until:
            if tag == self._skip_until[-1] and tag not in self.VOID_TAGS:
                self._skip_until.append(tag)
            return

        attrs_dict = {key: value or "" for key, value in attrs}
        classes = attrs_dict.get("class", "")
        if not self._paragraph_depth and tag not in self.INLINE_TAGS:
            self._finalize_loose_text()
        if (
            tag in self.SKIP_TAGS
            or "mw-empty-elt" in classes
            or any(name in classes for name in self.SKIP_CLASSES)
        ):
            if tag not in self.VOID_TAGS:
                self._skip_until.append(tag)
            return
        if tag == "p" and not self.first_paragraph:
            self._paragraph_depth += 1
            self._chunks = []
        elif self._paragraph_depth and tag == "br":
            self._chunks.append(" ")
        elif self._paragraph_depth and tag in self.ALLOWED_INLINE_TAGS:
            self._open_inline_tag(tag, self._chunks)
        elif not self._paragraph_depth and tag in self.ALLOWED_INLINE_TAGS:
            self._open_inline_tag(tag, self._loose_chunks)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._paragraph_depth and tag == "br":
            self._chunks.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if self._skip_until:
            if tag == self._skip_until[-1]:
                self._skip_until.pop()
            return
        if self._paragraph_depth and tag in self.ALLOWED_INLINE_TAGS:
            self._close_inline_tag(tag, self._chunks)
            return
        if not self._paragraph_depth and tag in self.ALLOWED_INLINE_TAGS:
            self._close_inline_tag(tag, self._loose_chunks)
            return
        if tag == "p" and self._paragraph_depth:
            self._close_open_inline_tags(self._chunks)
            text = normalize_inline_html("".join(self._chunks))
            plain_text = text_from_inline_html(text)
            self._paragraph_depth -= 1
            self._chunks = []
            if plain_text and not is_non_summary_paragraph(plain_text):
                self.first_paragraph = text

    def handle_data(self, data: str) -> None:
        if not self._skip_until and self._paragraph_depth and not self.first_paragraph:
            self._chunks.append(html_lib.escape(data, quote=False))
        elif not self._skip_until and not self.first_paragraph:
            self._loose_chunks.append(html_lib.escape(data, quote=False))

    def close(self) -> None:
        self._finalize_loose_text()
        super().close()

    def _finalize_loose_text(self) -> None:
        """Accept summary text that appears outside paragraph tags."""

        if self.first_paragraph or not self._loose_chunks:
            self._loose_chunks = []
            return
        self._close_open_inline_tags(self._loose_chunks)
        text = normalize_inline_html("".join(self._loose_chunks))
        plain_text = text_from_inline_html(text)
        self._loose_chunks = []
        if len(plain_text) >= 20 and not is_non_summary_paragraph(plain_text):
            self.first_paragraph = text

    def _open_inline_tag(self, tag: str, chunks: list[str]) -> None:
        kindle_tag = self.ALLOWED_INLINE_TAGS[tag]
        chunks.append(f"<{kindle_tag}>")
        self._inline_stack.append(kindle_tag)

    def _close_inline_tag(self, tag: str, chunks: list[str]) -> None:
        kindle_tag = self.ALLOWED_INLINE_TAGS[tag]
        if kindle_tag not in self._inline_stack:
            return
        while self._inline_stack:
            open_tag = self._inline_stack.pop()
            chunks.append(f"</{open_tag}>")
            if open_tag == kindle_tag:
                return

    def _close_open_inline_tags(self, chunks: list[str]) -> None:
        while self._inline_stack:
            chunks.append(f"</{self._inline_stack.pop()}>")


class InfoboxSummaryParser(HTMLParser):
    """Build a terse fallback summary from portable infobox fields."""

    WANTED_SOURCES = {
        "species": "race/species",
        "race": "race/species",
        "class": "class",
        "occupation": "occupation",
        "origin": "origin",
        "first_appearance": "first scene",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._source_stack: list[str] = []
        self._value_depth = 0
        self._chunks: list[str] = []
        self.values: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Capture values from portable-infobox data blocks."""

        attrs_dict = {key: value or "" for key, value in attrs}
        classes = attrs_dict.get("class", "")
        source = attrs_dict.get("data-source", "")
        if tag == "div" and "pi-data" in classes and source in self.WANTED_SOURCES:
            self._source_stack.append(source)
            return
        if self._source_stack and tag == "div" and "pi-data-value" in classes:
            self._value_depth += 1
            self._chunks = []

    def handle_endtag(self, tag: str) -> None:
        if self._value_depth and tag == "div":
            text = normalize_text("".join(self._chunks))
            source = self._source_stack[-1]
            if text:
                self.values.setdefault(self.WANTED_SOURCES[source], text)
            self._value_depth -= 1
            self._chunks = []
            return
        if self._source_stack and tag == "div":
            self._source_stack.pop()

    def handle_data(self, data: str) -> None:
        if self._value_depth:
            self._chunks.append(data)


def normalize_text(text: str) -> str:
    """Collapse wiki whitespace and non-breaking spaces into plain text spacing."""

    return " ".join(text.replace("\xa0", " ").split())


def normalize_inline_html(fragment: str) -> str:
    """Collapse whitespace in safe inline XHTML while preserving emphasis tags."""

    return " ".join(fragment.replace("\xa0", " ").split())


class InlineTextParser(HTMLParser):
    """Strip safe inline XHTML back to text for filtering and length checks."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.chunks: list[str] = []

    def handle_data(self, data: str) -> None:
        self.chunks.append(data)


def text_from_inline_html(fragment: str) -> str:
    """Return the plain text contained in a safe inline XHTML fragment."""

    parser = InlineTextParser()
    parser.feed(fragment)
    parser.close()
    return normalize_text("".join(parser.chunks))


def is_non_summary_paragraph(text: str) -> bool:
    """Return true for wiki boilerplate paragraphs that are not page summaries."""

    lowered = text.lower()
    return (
        lowered.startswith("system message.")
        or "posting book 9 spoilers" in lowered
        or lowered.startswith("spoilers for book")
    )


def first_paragraph_from_html(html: str) -> str:
    """Extract the first useful article paragraph from parsed wiki HTML."""

    parser = FirstParagraphParser()
    parser.feed(html)
    parser.close()
    return parser.first_paragraph


def summary_from_infobox(title: str, html: str) -> str:
    """Build a short fallback summary from portable-infobox fields."""

    parser = InfoboxSummaryParser()
    parser.feed(html)
    parser.close()
    if not parser.values:
        return ""
    parts = [
        f"{html_lib.escape(label, quote=False)}: {html_lib.escape(value, quote=False)}"
        for label, value in parser.values.items()
    ]
    return f"{html_lib.escape(title, quote=False)}: {'; '.join(parts)}."


def summary_from_html(title: str, html: str) -> str:
    """Extract a page summary, falling back to infobox fields when needed."""

    return first_paragraph_from_html(html) or summary_from_infobox(title, html)


def api_base(api_url: str) -> str:
    """Return the scheme and host portion of a MediaWiki API URL."""

    parsed = urllib.parse.urlparse(api_url)
    return f"{parsed.scheme}://{parsed.netloc}"


def wiki_page_url(api_url: str, title: str) -> str:
    """Build a human-readable wiki page URL from an API URL and page title."""

    return f"{api_base(api_url)}/wiki/{urllib.parse.quote(title.replace(' ', '_'))}"


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

        print(f"request failed; retrying in {sleep_for:.1f}s: {url}", file=sys.stderr)
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


def init_db(path: Path) -> sqlite3.Connection:
    """Create or open the crawl database and ensure its tables exist."""

    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS pages (
            pageid INTEGER PRIMARY KEY,
            title TEXT NOT NULL UNIQUE,
            ns INTEGER NOT NULL,
            url TEXT NOT NULL,
            raw_json TEXT,
            raw_html TEXT,
            first_paragraph TEXT,
            status TEXT NOT NULL,
            error TEXT,
            fetched_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS crawl_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    conn.commit()
    return conn


def save_meta(conn: sqlite3.Connection, values: dict[str, str]) -> None:
    """Persist crawl metadata such as API URL, category, and user agent."""

    conn.executemany(
        "INSERT OR REPLACE INTO crawl_meta (key, value) VALUES (?, ?)",
        sorted(values.items()),
    )
    conn.commit()


def already_fetched(conn: sqlite3.Connection, pageid: int) -> bool:
    """Return true when a page has already been fetched successfully."""

    row = conn.execute(
        "SELECT status FROM pages WHERE pageid = ? AND status = 'ok'",
        (pageid,),
    ).fetchone()
    return row is not None


def upsert_page(
    conn: sqlite3.Connection,
    page: PageRef,
    url: str,
    status: str,
    raw_json: dict[str, Any] | None = None,
    raw_html: str | None = None,
    first_paragraph: str | None = None,
    error: str | None = None,
) -> None:
    """Insert or update a fetched page row."""

    conn.execute(
        """
        INSERT INTO pages (
            pageid, title, ns, url, raw_json, raw_html, first_paragraph, status, error, fetched_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(pageid) DO UPDATE SET
            title = excluded.title,
            ns = excluded.ns,
            url = excluded.url,
            raw_json = excluded.raw_json,
            raw_html = excluded.raw_html,
            first_paragraph = excluded.first_paragraph,
            status = excluded.status,
            error = excluded.error,
            fetched_at = CURRENT_TIMESTAMP
        """,
        (
            page.pageid,
            page.title,
            page.ns,
            url,
            json.dumps(raw_json, ensure_ascii=False, sort_keys=True) if raw_json else None,
            raw_html,
            first_paragraph,
            status,
            error,
        ),
    )
    conn.commit()


def reextract_first_paragraphs(conn: sqlite3.Connection) -> int:
    """Refresh derived summaries from stored raw HTML without network access."""

    rows = conn.execute(
        "SELECT pageid, title, raw_html FROM pages WHERE status = 'ok' AND COALESCE(raw_html, '') != ''"
    ).fetchall()
    for pageid, title, raw_html in rows:
        conn.execute(
            "UPDATE pages SET first_paragraph = ? WHERE pageid = ?",
            (summary_from_html(title, raw_html), pageid),
        )
    conn.commit()
    return len(rows)


def load_category_members(client: MediaWikiClient, config: CrawlConfig) -> list[PageRef]:
    """Load crawl targets from the configured wiki category."""

    return client.category_members(
        config.category,
        config.category_batch_size,
        config.max_pages,
        config.delay,
    )


def fetch_page(client: MediaWikiClient, page: PageRef) -> dict[str, Any]:
    """Fetch raw parsed page data for one crawl target."""

    return client.parse_page(page)


def request_config_from_args(args: argparse.Namespace) -> RequestConfig:
    """Translate CLI arguments into request configuration."""

    return RequestConfig(
        user_agent=args.user_agent,
        timeout=args.timeout,
        max_retries=args.max_retries,
        initial_backoff=args.initial_backoff,
        max_backoff=args.max_backoff,
    )


def crawl_config_from_args(args: argparse.Namespace) -> CrawlConfig:
    """Translate CLI arguments into crawl configuration."""

    return CrawlConfig(
        category=args.category,
        delay=args.delay,
        max_pages=args.max_pages,
        category_batch_size=args.category_batch_size,
        refresh=args.refresh,
    )


def crawl_pages(
    conn: sqlite3.Connection,
    client: MediaWikiClient,
    pages: list[PageRef],
    config: CrawlConfig,
) -> None:
    """Fetch and store each page, respecting resume and delay settings."""

    for index, page in enumerate(pages, start=1):
        if not config.refresh and already_fetched(conn, page.pageid):
            print(f"[{index}/{len(pages)}] skip {page.title}")
            continue

        print(f"[{index}/{len(pages)}] fetch {page.title}")
        fetch_and_store_page(conn, client, page)
        time.sleep(jitter(config.delay))


def fetch_and_store_page(conn: sqlite3.Connection, client: MediaWikiClient, page: PageRef) -> None:
    """Fetch one page and record either the successful content or the error."""

    url = wiki_page_url(client.api_url, page.title)
    try:
        data = fetch_page(client, page)
        parsed = data.get("parse", {})
        html = parsed.get("text", {}).get("*", "")
        first_paragraph = summary_from_html(page.title, html)
        upsert_page(conn, page, url, "ok", data, html, first_paragraph)
    except Exception as exc:  # noqa: BLE001 - keep crawling and record failures.
        upsert_page(conn, page, url, "error", error=repr(exc))
        print(f"error fetching {page.title}: {exc!r}", file=sys.stderr)


def print_crawl_summary(conn: sqlite3.Connection, output: Path) -> None:
    """Print final counts for successful and failed page fetches."""

    ok_count = conn.execute("SELECT COUNT(*) FROM pages WHERE status = 'ok'").fetchone()[0]
    error_count = conn.execute("SELECT COUNT(*) FROM pages WHERE status = 'error'").fetchone()[0]
    print(f"done: {ok_count} ok, {error_count} error; wrote {output}")


def assert_robots_allowed(api_url: str, user_agent: str) -> None:
    """Stop when robots.txt disallows fetching the API URL for this user agent."""

    robots_url = f"{api_base(api_url)}/robots.txt"
    parser = urllib.robotparser.RobotFileParser(robots_url)
    parser.read()
    if not parser.can_fetch(user_agent, api_url):
        raise SystemExit(f"robots.txt disallows fetching API URL for {user_agent}: {api_url}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments for the crawler."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", default=DEFAULT_API_URL)
    parser.add_argument("--category", default=DEFAULT_CATEGORY)
    parser.add_argument("--output", type=Path, default=Path("data/characters.sqlite"))
    parser.add_argument("--user-agent", default=DEFAULT_USER_AGENT)
    parser.add_argument("--delay", type=float, default=1.5, help="Base delay between page requests.")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--max-retries", type=int, default=5)
    parser.add_argument("--initial-backoff", type=float, default=2.0)
    parser.add_argument("--max-backoff", type=float, default=120.0)
    parser.add_argument("--max-pages", type=int, default=0, help="Stop after this many pages; 0 means no cap.")
    parser.add_argument("--category-batch-size", type=int, default=200)
    parser.add_argument("--refresh", action="store_true", help="Refetch pages that already succeeded.")
    parser.add_argument("--ignore-robots", action="store_true", help="Skip robots.txt check.")
    parser.add_argument(
        "--reextract-only",
        action="store_true",
        help="Update first_paragraph from stored raw_html without making network requests.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the crawler command-line workflow."""

    args = parse_args(argv)
    conn = init_db(args.output)
    if args.reextract_only:
        count = reextract_first_paragraphs(conn)
        print(f"re-extracted first paragraphs for {count} stored pages in {args.output}")
        return 0

    if not args.ignore_robots:
        assert_robots_allowed(args.api_url, args.user_agent)

    save_meta(
        conn,
        {
            "api_url": args.api_url,
            "category": args.category,
            "user_agent": args.user_agent,
            "fetched_by": "fetch_characters.py",
        },
    )

    client = MediaWikiClient(args.api_url, request_config_from_args(args))
    crawl_config = crawl_config_from_args(args)
    pages = load_category_members(client, crawl_config)
    print(f"found {len(pages)} pages in {args.category}")
    crawl_pages(conn, client, pages, crawl_config)
    print_crawl_summary(conn, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
