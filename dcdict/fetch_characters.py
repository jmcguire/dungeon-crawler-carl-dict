#!/usr/bin/env python3
"""Fetch raw character page data from a MediaWiki/Fandom category."""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import time
import urllib.robotparser
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dcdict.extraction import (
    SHORT_DESCRIPTION_THRESHOLD,
    FirstParagraphParser,
    InfoboxSummaryParser,
    InlineTextParser,
    ai_description_paragraph_from_html,
    expand_small_description,
    extract_summary_status,
    first_paragraph_from_html,
    is_ai_statline_paragraph,
    is_generic_small_description,
    is_non_summary_paragraph,
    is_small_description,
    is_stub_like_description,
    is_truncated_description,
    lowercase_first_text_character,
    normalize_inline_html,
    normalize_text,
    summary_blocks_from_html,
    summary_from_html,
    summary_from_infobox,
    text_from_inline_html,
)
from dcdict.mediawiki import (
    MediaWikiClient,
    PageRef,
    RequestConfig,
    api_base,
    api_request,
    fandom_api_url,
    fandom_page_base_url,
    jitter,
    parse_retry_after,
    wiki_category_title,
    wiki_page_url,
)


DEFAULT_FANDOM = "dungeon-crawler-carl"
DEFAULT_CATEGORY = "Characters"
DEFAULT_USER_AGENT = "KindleDictionaryCreationCrawler/0.1"
LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class CrawlConfig:
    """Category traversal settings for a crawler run."""

    categories: tuple[str, ...]
    delay: float
    max_pages: int
    category_batch_size: int
    refresh: bool


@dataclass(frozen=True)
class CrawlTarget:
    """A page to fetch plus the categories that led us to it."""

    pageid: int
    title: str
    ns: int
    source_categories: tuple[str, ...]


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
            source_category TEXT,
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
    columns = {
        row[1]
        for row in conn.execute("PRAGMA table_info(pages)").fetchall()
    }
    if "source_category" not in columns:
        conn.execute("ALTER TABLE pages ADD COLUMN source_category TEXT")
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
    page: PageRef | CrawlTarget,
    url: str,
    source_category: str,
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
            pageid, title, ns, url, source_category, raw_json, raw_html, first_paragraph, status, error, fetched_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(pageid) DO UPDATE SET
            title = excluded.title,
            ns = excluded.ns,
            url = excluded.url,
            source_category = excluded.source_category,
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
            source_category,
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
        "SELECT pageid, title, raw_html FROM pages WHERE status != 'error' AND COALESCE(raw_html, '') != ''"
    ).fetchall()
    for pageid, title, raw_html in rows:
        status, first_paragraph = extract_summary_status(title, raw_html)
        if status == "empty":
            LOGGER.info("empty entry %s", title)
        conn.execute(
            "UPDATE pages SET first_paragraph = ?, status = ? WHERE pageid = ?",
            (first_paragraph, status, pageid),
        )
    conn.commit()
    return len(rows)


def load_category_members(client: MediaWikiClient, config: CrawlConfig) -> list[CrawlTarget]:
    """Load and deduplicate crawl targets from the configured wiki categories."""

    targets: dict[int, CrawlTarget] = {}
    order: list[int] = []
    for category in config.categories:
        try:
            pages = client.category_members(
                category,
                config.category_batch_size,
                config.max_pages,
                config.delay,
            )
        except Exception as exc:  # noqa: BLE001 - keep later categories moving.
            LOGGER.error("error listing category %s: %r", category, exc)
            continue
        for page in pages:
            if page.pageid not in targets:
                targets[page.pageid] = CrawlTarget(
                    pageid=page.pageid,
                    title=page.title,
                    ns=page.ns,
                    source_categories=(category,),
                )
                order.append(page.pageid)
            else:
                existing = targets[page.pageid]
                if category not in existing.source_categories:
                    targets[page.pageid] = CrawlTarget(
                        pageid=existing.pageid,
                        title=existing.title,
                        ns=existing.ns,
                        source_categories=existing.source_categories + (category,),
                    )
    return [targets[pageid] for pageid in order]


def fetch_page(client: MediaWikiClient, page: CrawlTarget) -> dict[str, Any]:
    """Fetch raw parsed page data for one crawl target."""

    return client.parse_page(PageRef(page.pageid, page.title, page.ns))


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
        categories=tuple(args.categories or [DEFAULT_CATEGORY]),
        delay=args.delay,
        max_pages=args.max_pages,
        category_batch_size=args.category_batch_size,
        refresh=args.refresh,
    )


def crawl_pages(
    conn: sqlite3.Connection,
    client: MediaWikiClient,
    pages: list[CrawlTarget],
    config: CrawlConfig,
) -> None:
    """Fetch and store each page, respecting resume and delay settings."""

    for index, page in enumerate(pages, start=1):
        if not config.refresh and already_fetched(conn, page.pageid):
            LOGGER.info("[%s/%s] skip %s", index, len(pages), page.title)
            continue

        LOGGER.info("[%s/%s] fetch %s", index, len(pages), page.title)
        fetch_and_store_page(conn, client, page)
        time.sleep(jitter(config.delay))


def fetch_and_store_page(conn: sqlite3.Connection, client: MediaWikiClient, page: CrawlTarget) -> None:
    """Fetch one page and record either the successful content or the error."""

    url = client.page_url(page.title)
    source_category = ", ".join(page.source_categories)
    try:
        data = fetch_page(client, page)
        parsed = data.get("parse", {})
        html = parsed.get("text", {}).get("*", "")
        status, first_paragraph = extract_summary_status(page.title, html)
        upsert_page(conn, page, url, source_category, status, data, html, first_paragraph or None)
        if status == "empty":
            LOGGER.info("empty entry %s", page.title)
    except Exception as exc:  # noqa: BLE001 - keep crawling and record failures.
        upsert_page(conn, page, url, source_category, "error", error=repr(exc))
        LOGGER.error("error fetching %s: %r", page.title, exc)


def print_crawl_summary(conn: sqlite3.Connection, output: Path) -> None:
    """Print final counts for successful and failed page fetches."""

    ok_count = conn.execute("SELECT COUNT(*) FROM pages WHERE status = 'ok'").fetchone()[0]
    empty_count = conn.execute("SELECT COUNT(*) FROM pages WHERE status = 'empty'").fetchone()[0]
    error_count = conn.execute("SELECT COUNT(*) FROM pages WHERE status = 'error'").fetchone()[0]
    LOGGER.info("done: %s ok, %s empty, %s error; wrote %s", ok_count, empty_count, error_count, output)


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
    parser.add_argument("--fandom", default=DEFAULT_FANDOM, help="Fandom wiki slug, like dungeon-crawler-carl.")
    parser.add_argument(
        "--category",
        dest="categories",
        action="append",
        default=None,
        help="Category name without the Category: prefix. May be repeated. Defaults to Characters.",
    )
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
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    conn = init_db(args.output)
    if args.reextract_only:
        count = reextract_first_paragraphs(conn)
        LOGGER.info("re-extracted first paragraphs for %s stored pages in %s", count, args.output)
        return 0

    if not args.ignore_robots:
        assert_robots_allowed(fandom_api_url(args.fandom), args.user_agent)

    args.categories = args.categories or [DEFAULT_CATEGORY]
    api_url = fandom_api_url(args.fandom)

    save_meta(
        conn,
        {
            "api_url": api_url,
            "fandom": args.fandom,
            "categories": ",".join(args.categories),
            "user_agent": args.user_agent,
            "fetched_by": "fetch_characters.py",
        },
    )

    client = MediaWikiClient(args.fandom, request_config_from_args(args))
    crawl_config = crawl_config_from_args(args)
    pages = load_category_members(client, crawl_config)
    LOGGER.info("found %s pages across %s", len(pages), ", ".join(args.categories))
    crawl_pages(conn, client, pages, crawl_config)
    print_crawl_summary(conn, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
