#!/usr/bin/env python3
"""Build Kindle dictionary source files from fetched page data."""

from __future__ import annotations

import argparse
from pathlib import Path

from fandom_dict.cli.output import add_output_arguments, output_from_args
from fandom_dict.config import DEFAULT_CONFIG_PATH, load_project_config
from fandom_dict.entries import load_entries
from fandom_dict.formats.kindle import *  # noqa: F403 - preserve the old module's import surface.
from fandom_dict.formats.kindle import (
    DEFAULT_AUTHOR,
    DEFAULT_TITLE,
    build_dictionary_sources,
    compile_with_kindlegen,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments for the dictionary builder."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--title")
    parser.add_argument("--author")
    parser.add_argument("--source-name")
    parser.add_argument("--min-definition-length", type=int, default=8)
    parser.add_argument("--compile", action="store_true", help="Run kindlegen if it is installed.")
    parser.add_argument(
        "--release-version",
        help="Dictionary release version for the Kindle OPF identifier, such as 0.5.0. Defaults to dev.",
    )
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
    add_output_arguments(parser)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the dictionary build command-line workflow."""

    args = parse_args(argv)
    output = output_from_args(args)
    config = load_project_config(args.config)
    input_path = args.input or config.database_path
    output_dir = args.output_dir or config.kindle_dir
    title = args.title or config.title
    author = args.author or config.author
    source_name = args.source_name or config.source_name
    entries = load_entries(
        input_path,
        args.min_definition_length,
        sidebar_fields=config.sidebar_fields,
        strip_parenthetical_disambiguation=config.title_aliases.strip_parenthetical,
        max_summary_length=config.max_summary_length,
    )
    if not entries:
        raise SystemExit(f"no usable entries found in {input_path}")
    release_version = normalize_release_version(args.release_version)

    result = build_dictionary_sources(
        entries,
        output_dir,
        title,
        author,
        link_entries=args.link_entries,
        include_sidebar_aliases=not args.no_sidebar_aliases,
        release_version=release_version,
        source_name=source_name,
        title_suffix_aliases=config.title_aliases.suffixes,
        title_prefix_aliases=config.title_aliases.prefixes,
        strip_parenthetical_disambiguation=config.title_aliases.strip_parenthetical,
        title_component_ignore_words=config.title_aliases.component_ignore_words,
        sidebar_alias_labels=config.sidebar_alias_labels,
    )

    output.path(result.xhtml_path)
    output.path(result.opf_path)
    output.info(f"entries: {result.entry_count}")
    output.info(f"aliases: {result.alias_count}")
    output.info(f"multi-target lookups: {result.multi_lookup_count}")
    output.info(f"omitted aliases: {result.omitted_alias_count}")

    if args.compile:
        compilation = compile_with_kindlegen(result.opf_path)
        if compilation:
            output.detail(compilation.compiler_log.rstrip())
            output.path(compilation.output_path)
        else:
            output.warning("kindlegen was not found; source files are ready, but no .mobi was produced")
    output.close()

    return 0


def normalize_release_version(value: str | None) -> str:
    """Return the OPF identifier version component for local Kindle builds."""

    if value is None:
        return "dev"
    from fandom_dict.cli.release import ReleaseError, parse_version

    try:
        return parse_version(value).tag
    except ReleaseError as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    raise SystemExit(main())
