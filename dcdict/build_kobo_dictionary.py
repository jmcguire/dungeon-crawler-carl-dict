#!/usr/bin/env python3
"""Build a Kobo dicthtml dictionary from fetched page data."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from dcdict.entries import load_entries
from dcdict.kobo import DICTGEN_OUTPUT_NAME, KoboValidationError, build_kobo, inspect_kobo


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments for the Kobo builder."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=Path("data/characters.sqlite"))
    parser.add_argument("--output-dir", type=Path, default=Path("build/kobo"))
    parser.add_argument("--output-name", default=DICTGEN_OUTPUT_NAME)
    parser.add_argument("--min-definition-length", type=int, default=8)
    parser.add_argument(
        "--no-sidebar-aliases",
        action="store_true",
        help="Disable lookup aliases derived from wiki sidebar alias fields.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Build and smoke-test a Kobo dictionary."""

    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    entries = load_entries(args.input, args.min_definition_length)
    if not entries:
        raise SystemExit(f"no usable entries found in {args.input}")
    try:
        result = build_kobo(
            entries,
            args.output_dir,
            output_name=args.output_name,
            include_sidebar_aliases=not args.no_sidebar_aliases,
        )
        inspection = inspect_kobo(
            result.dictzip_path,
            required_headwords=("Carl", "Donut", "Mordecai", "1914", "Fire Fingers"),
        )
    except KoboValidationError as exc:
        print(f"Kobo build failed: {exc}", file=sys.stderr)
        return 1
    print(f"wrote {result.dictfile_path}")
    print(f"wrote {result.dictzip_path}")
    print(f"entries: {result.entry_count}")
    print(f"aliases: {result.alias_count}")
    print(f"multi-target lookups: {result.multi_lookup_count}")
    print(f"omitted aliases: {result.omitted_alias_count}")
    print(f"smoke checks: {len(inspection.checks)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
