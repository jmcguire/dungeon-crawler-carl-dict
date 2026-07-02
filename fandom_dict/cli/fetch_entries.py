#!/usr/bin/env python3
"""Fetch raw entry page data from a MediaWiki/Fandom category."""

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

from fandom_dict.cli.output import add_output_arguments, configure_logging, output_from_args
from fandom_dict.config import DEFAULT_CONFIG_PATH, load_default_project_config, load_project_config
from fandom_dict.extraction import (
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
from fandom_dict.wiki.mediawiki import (
    MediaWikiClient,
    PageRef,
    RedirectRef,
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


DEFAULT_PROJECT = load_default_project_config()
DEFAULT_FANDOM = DEFAULT_PROJECT.fandom
DEFAULT_CATEGORIES = DEFAULT_PROJECT.categories
DEFAULT_DATABASE_PATH = DEFAULT_PROJECT.database_path
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
    max_summary_length: int | None = None
    include_redirects: bool = True
    max_redirects: int = 0


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
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS redirects (
            source_title TEXT PRIMARY KEY,
            target_title TEXT,
            source_url TEXT NOT NULL,
            status TEXT NOT NULL,
            fetched_at TEXT DEFAULT CURRENT_TIMESTAMP
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


def upsert_redirect(
    conn: sqlite3.Connection,
    source_title: str,
    target_title: str | None,
    source_url: str,
    status: str,
) -> None:
    """Insert or update one redirect row."""

    conn.execute(
        """
        INSERT INTO redirects (source_title, target_title, source_url, status, fetched_at)
        VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(source_title) DO UPDATE SET
            target_title = excluded.target_title,
            source_url = excluded.source_url,
            status = excluded.status,
            fetched_at = CURRENT_TIMESTAMP
        """,
        (source_title, target_title, source_url, status),
    )
    conn.commit()


def reextract_first_paragraphs(conn: sqlite3.Connection, max_summary_length: int | None = None) -> int:
    """Refresh derived summaries from stored raw HTML without network access."""

    rows = conn.execute(
        "SELECT pageid, title, raw_html FROM pages WHERE status != 'error' AND COALESCE(raw_html, '') != ''"
    ).fetchall()
    for pageid, title, raw_html in rows:
        status, first_paragraph = extract_summary_status(title, raw_html, max_summary_length)
        if status == "empty":
            LOGGER.debug("empty entry %s", title)
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
    seen_categories: set[str] = set()
    category_queue = list(config.categories)
    while category_queue:
        category = category_queue.pop(0)
        canonical_category = client.category_title(category)
        if canonical_category in seen_categories:
            continue
        seen_categories.add(canonical_category)
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
        categories=tuple(args.categories or DEFAULT_CATEGORIES),
        delay=args.delay,
        max_pages=args.max_pages,
        category_batch_size=args.category_batch_size,
        refresh=args.refresh,
        max_summary_length=args.max_summary_length,
        include_redirects=args.include_redirects,
        max_redirects=args.max_redirects,
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
            LOGGER.debug("[%s/%s] skip %s", index, len(pages), page.title)
            continue

        LOGGER.debug("[%s/%s] fetch %s", index, len(pages), page.title)
        fetch_and_store_page(conn, client, page, config)
        time.sleep(jitter(config.delay))


def fetch_and_store_page(
    conn: sqlite3.Connection,
    client: MediaWikiClient,
    page: CrawlTarget,
    config: CrawlConfig | None = None,
) -> None:
    """Fetch one page and record either the successful content or the error."""

    url = client.page_url(page.title)
    source_category = ", ".join(page.source_categories)
    try:
        data = fetch_page(client, page)
        parsed = data.get("parse", {})
        html = parsed.get("text", {}).get("*", "")
        status, first_paragraph = extract_summary_status(
            page.title,
            html,
            config.max_summary_length if config else None,
        )
        upsert_page(conn, page, url, source_category, status, data, html, first_paragraph or None)
        if status == "empty":
            LOGGER.debug("empty entry %s", page.title)
    except Exception as exc:  # noqa: BLE001 - keep crawling and record failures.
        upsert_page(conn, page, url, source_category, "error", error=repr(exc))
        LOGGER.error("error fetching %s: %r", page.title, exc)


def fetch_and_store_redirects(
    conn: sqlite3.Connection,
    client: MediaWikiClient,
    pages: list[CrawlTarget],
    config: CrawlConfig,
) -> None:
    """Fetch wiki redirects and keep only aliases that target selected pages."""

    if not config.include_redirects:
        return
    target_titles = {page.title.casefold(): page.title for page in pages}
    if not target_titles:
        LOGGER.info("redirects: skipped because no crawl targets were found")
        return
    if not config.max_redirects:
        conn.execute("DELETE FROM redirects")
        conn.commit()

    ok_count = ignored_count = error_count = 0
    for redirect in client.redirects(config.category_batch_size, config.max_redirects, config.delay):
        status = redirect_status(redirect, target_titles)
        target_title = target_titles.get((redirect.target_title or "").casefold(), redirect.target_title)
        upsert_redirect(conn, redirect.source_title, target_title, client.page_url(redirect.source_title), status)
        if status == "ok":
            ok_count += 1
        elif status == "ignored":
            ignored_count += 1
        else:
            error_count += 1
    LOGGER.info("redirects: %s ok, %s ignored, %s error", ok_count, ignored_count, error_count)


def redirect_status(redirect: RedirectRef, target_titles: dict[str, str]) -> str:
    """Return the storage status for one resolved redirect."""

    if redirect.status != "ok" or not redirect.target_title:
        return "error"
    if redirect.target_title.casefold() not in target_titles:
        return "ignored"
    return "ok"


def print_crawl_summary(conn: sqlite3.Connection, output: Path) -> None:
    """Print final counts for successful and failed page fetches."""

    ok_count = conn.execute("SELECT COUNT(*) FROM pages WHERE status = 'ok'").fetchone()[0]
    empty_count = conn.execute("SELECT COUNT(*) FROM pages WHERE status = 'empty'").fetchone()[0]
    error_count = conn.execute("SELECT COUNT(*) FROM pages WHERE status = 'error'").fetchone()[0]
    redirect_count = conn.execute("SELECT COUNT(*) FROM redirects WHERE status = 'ok'").fetchone()[0]
    LOGGER.info(
        "done: %s ok, %s empty, %s error, %s redirects; wrote %s",
        ok_count,
        empty_count,
        error_count,
        redirect_count,
        output,
    )


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
    parser.add_argument("-c", "--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--fandom", help="Fandom wiki slug, like dungeon-crawler-carl.")
    parser.add_argument(
        "--category",
        dest="categories",
        action="append",
        default=None,
        help="Category name without the Category: prefix. May be repeated. Defaults to the normal DCC build categories.",
    )
    parser.add_argument("-o", "--output", type=Path)
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
    redirect_group = parser.add_mutually_exclusive_group()
    redirect_group.add_argument(
        "--include-redirects",
        dest="include_redirects",
        action="store_true",
        default=True,
        help="Fetch wiki redirect titles and store usable ones as lookup aliases. Enabled by default.",
    )
    redirect_group.add_argument(
        "--no-redirects",
        dest="include_redirects",
        action="store_false",
        help="Skip fetching wiki redirect aliases.",
    )
    parser.add_argument("--max-redirects", type=int, default=0, help="Stop after this many redirects; 0 means no cap.")
    parser.add_argument(
        "--reextract-only",
        action="store_true",
        help="Update first_paragraph from stored raw_html without making network requests.",
    )
    add_output_arguments(parser, paths_only=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the crawler command-line workflow."""

    args = parse_args(argv)
    output = output_from_args(args)
    configure_logging(output)
    project_config = load_project_config(args.config)
    args.fandom = args.fandom or project_config.fandom
    args.categories = args.categories or list(project_config.categories)
    args.output = args.output or project_config.database_path
    args.max_summary_length = project_config.max_summary_length

    conn = init_db(args.output)
    try:
        if args.reextract_only:
            count = reextract_first_paragraphs(conn, project_config.max_summary_length)
            LOGGER.info("re-extracted first paragraphs for %s stored pages in %s", count, args.output)
            output.path(args.output)
            return 0

        if not args.ignore_robots:
            assert_robots_allowed(fandom_api_url(args.fandom), args.user_agent)

        api_url = fandom_api_url(args.fandom)

        save_meta(
            conn,
            {
                "api_url": api_url,
                "fandom": args.fandom,
                "categories": ",".join(args.categories),
                "user_agent": args.user_agent,
                "fetched_by": "fetch_entries.py",
            },
        )

        client = MediaWikiClient(args.fandom, request_config_from_args(args))
        crawl_config = crawl_config_from_args(args)
        pages = load_category_members(client, crawl_config)
        LOGGER.info("found %s pages across %s", len(pages), ", ".join(args.categories))
        crawl_pages(conn, client, pages, crawl_config)
        fetch_and_store_redirects(conn, client, pages, crawl_config)
        print_crawl_summary(conn, args.output)
        output.path(args.output)
    finally:
        conn.close()
        output.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
