#!/usr/bin/env python3
"""Build Kindle dictionary source files from fetched page data."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from dcdict.entries import load_entries
from dcdict.kindle import *  # noqa: F403 - preserve the old module's import surface.
from dcdict.kindle import (
    DEFAULT_AUTHOR,
    DEFAULT_TITLE,
    build_dictionary_sources,
    compile_with_kindlegen,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments for the dictionary builder."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=Path("data/characters.sqlite"))
    parser.add_argument("--output-dir", type=Path, default=Path("build"))
    parser.add_argument("--title", default=DEFAULT_TITLE)
    parser.add_argument("--author", default=DEFAULT_AUTHOR)
    parser.add_argument("--min-definition-length", type=int, default=8)
    parser.add_argument("--compile", action="store_true", help="Run kindlegen if it is installed.")
    parser.add_argument(
        "--no-sidebar-aliases",
        action="store_true",
        help="Disable lookup aliases derived from wiki sidebar alias fields.",
    )
    parser.add_argument(
        "--link-entries",
        action="store_true",
        help="Add internal links between dictionary entries. These work when opening the dictionary directly, but may not work in Kindle lookup popups.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the dictionary build command-line workflow."""

    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    entries = load_entries(args.input, args.min_definition_length)
    if not entries:
        raise SystemExit(f"no usable entries found in {args.input}")

    result = build_dictionary_sources(
        entries,
        args.output_dir,
        args.title,
        args.author,
        link_entries=args.link_entries,
        include_sidebar_aliases=not args.no_sidebar_aliases,
    )

    print(f"wrote {result.xhtml_path}")
    print(f"wrote {result.opf_path}")
    print(f"entries: {result.entry_count}")
    print(f"aliases: {result.alias_count}")
    print(f"omitted aliases: {result.omitted_alias_count}")

    if args.compile:
        compilation = compile_with_kindlegen(result.opf_path)
        if compilation:
            print(compilation.compiler_log, end="")
            print(f"compiled {compilation.output_path}")
        else:
            print("kindlegen was not found; source files are ready, but no .mobi was produced")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
