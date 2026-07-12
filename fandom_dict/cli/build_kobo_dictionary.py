#!/usr/bin/env python3
"""Build a Kobo dicthtml dictionary from fetched page data."""

from __future__ import annotations

import argparse
from pathlib import Path

from fandom_dict.cli.common import configured_lookup_report, load_config_for_command, load_entries_for_command
from fandom_dict.cli.output import add_output_arguments, output_from_args
from fandom_dict.config import DEFAULT_CONFIG_PATH
from fandom_dict.entries import lookup_report_debug_lines
from fandom_dict.formats.kobo import DICTGEN_OUTPUT_NAME, KoboValidationError, build_kobo, inspect_kobo


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments for the Kobo builder."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-c", "--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("-i", "--input", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--output-name")
    parser.add_argument("--source-name")
    parser.add_argument("--min-definition-length", type=int, default=8)
    parser.add_argument(
        "--no-sidebar-aliases",
        action="store_true",
        help="Disable lookup aliases derived from wiki sidebar alias fields.",
    )
    add_output_arguments(parser, paths_only=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Build and smoke-test a Kobo dictionary."""

    args = parse_args(argv)
    output = output_from_args(args)
    config = load_config_for_command(args.config, output)
    if config is None:
        output.close()
        return 1
    input_path = args.input or config.database_path
    output_dir = args.output_dir or config.kobo_dir
    output_name = args.output_name or config.kobo_output_name
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
    try:
        result = build_kobo(
            entries,
            output_dir,
            output_name=output_name,
            include_sidebar_aliases=not args.no_sidebar_aliases,
            source_name=source_name,
            title_suffix_aliases=config.title_aliases.suffixes,
            title_prefix_aliases=config.title_aliases.prefixes,
            strip_parenthetical_disambiguation=config.title_aliases.strip_parenthetical,
            title_component_ignore_words=config.title_aliases.component_ignore_words,
            sidebar_alias_labels=config.sidebar_alias_labels,
            lookup_report=lookup_report,
        )
        inspection = inspect_kobo(
            result.dictzip_path,
            required_headwords=config.smoke_headwords,
        )
    except KoboValidationError as exc:
        output.error(f"Kobo build failed: {exc}")
        output.close()
        return 1
    output.path(result.dictfile_path)
    output.path(result.dictzip_path)
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
