#!/usr/bin/env python3
"""Build a StarDict dictionary bundle from fetched page data."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from dcdict.entries import load_entries
from dcdict.kindle import DEFAULT_AUTHOR, DEFAULT_TITLE
from dcdict.stardict import build_stardict, inspect_stardict


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments for the StarDict builder."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=Path("data/characters.sqlite"))
    parser.add_argument("--output-dir", type=Path, default=Path("build/stardict"))
    parser.add_argument("--title", default=DEFAULT_TITLE)
    parser.add_argument("--author", default=DEFAULT_AUTHOR)
    parser.add_argument("--min-definition-length", type=int, default=8)
    parser.add_argument(
        "--no-sidebar-aliases",
        action="store_true",
        help="Disable lookup aliases derived from wiki sidebar alias fields.",
    )
    parser.add_argument(
        "--link-entries",
        action="store_true",
        help="Add tappable KOReader links between known dictionary entries.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Build and smoke-test a StarDict dictionary."""

    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    entries = load_entries(args.input, args.min_definition_length)
    if not entries:
        raise SystemExit(f"no usable entries found in {args.input}")
    result = build_stardict(
        entries,
        args.output_dir,
        args.title,
        args.author,
        link_entries=args.link_entries,
        include_sidebar_aliases=not args.no_sidebar_aliases,
    )
    inspection = inspect_stardict(
        result.ifo_path,
        expected_title=args.title,
        require_links=args.link_entries,
    )
    for path in result.files:
        print(f"wrote {path}")
    print(f"entries: {result.entry_count}")
    print(f"aliases: {result.alias_count}")
    print(f"omitted aliases: {result.omitted_alias_count}")
    print(f"smoke checks: {len(inspection.checks)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
