#!/usr/bin/env python3
"""Build a StarDict dictionary bundle from fetched page data."""

from __future__ import annotations

import argparse
from pathlib import Path

from fandom_dict.cli.common import configured_lookup_report, load_config_for_command, load_entries_for_command
from fandom_dict.cli.output import add_output_arguments, output_from_args
from fandom_dict.config import DEFAULT_CONFIG_PATH
from fandom_dict.entries import lookup_report_debug_lines
from fandom_dict.formats.stardict import build_stardict, inspect_stardict


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments for the StarDict builder."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-c", "--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("-i", "--input", type=Path)
    parser.add_argument("-o", "--output-dir", type=Path)
    parser.add_argument("--title")
    parser.add_argument("--author")
    parser.add_argument("--source-name")
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
    add_output_arguments(parser, paths_only=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Build and smoke-test a StarDict dictionary."""

    args = parse_args(argv)
    output = output_from_args(args)
    config = load_config_for_command(args.config, output)
    if config is None:
        output.close()
        return 1
    input_path = args.input or config.database_path
    output_dir = args.output_dir or config.stardict_dir
    title = args.title or config.title
    author = args.author or config.author
    source_name = args.source_name or config.source_name
    entries = load_entries_for_command(input_path, config, args.min_definition_length, output)
    if entries is None:
        output.close()
        return 1
    lookup_report = configured_lookup_report(
        entries,
        config,
        include_sidebar_aliases=not args.no_sidebar_aliases,
    )
    result = build_stardict(
        entries,
        output_dir,
        title,
        author,
        link_entries=args.link_entries,
        base_name=config.file_base_name,
        include_sidebar_aliases=not args.no_sidebar_aliases,
        source_name=source_name,
        title_suffix_aliases=config.title_aliases.suffixes,
        title_prefix_aliases=config.title_aliases.prefixes,
        strip_parenthetical_disambiguation=config.title_aliases.strip_parenthetical,
        title_component_ignore_words=config.title_aliases.component_ignore_words,
        sidebar_alias_labels=config.sidebar_alias_labels,
        lookup_report=lookup_report,
    )
    inspection = inspect_stardict(
        result.ifo_path,
        expected_title=title,
        required_headwords=config.smoke_headwords,
        require_links=args.link_entries,
    )
    for path in result.files:
        output.path(path)
    output.info(f"entries: {result.entry_count}")
    output.info(f"lookup records: {result.lookup_record_count}")
    output.info(f"aliases: {result.alias_count}")
    output.info(f"multi-target lookups: {result.multi_lookup_count}")
    output.info(f"omitted aliases: {result.omitted_alias_count}")
    for line in lookup_report_debug_lines(lookup_report):
        output.detail(line)
    output.info(f"smoke checks: {len(inspection.checks)}")
    output.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
