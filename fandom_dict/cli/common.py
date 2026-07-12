"""Shared, small CLI helpers for loading entries and lookup policy."""

from __future__ import annotations

import sqlite3
import json
from pathlib import Path

from fandom_dict.config import ProjectConfig, load_project_config
from fandom_dict.entries import Entry, LookupReport, build_lookup_report, load_entries


def load_config_for_command(path: Path, output) -> ProjectConfig | None:
    """Load project configuration with a concise CLI error."""

    try:
        return load_project_config(path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        output.error(f"could not load project config {path}: {exc}")
        return None


def load_entries_for_command(
    input_path: Path,
    config: ProjectConfig,
    min_definition_length: int,
    output,
) -> list[Entry] | None:
    """Load configured entries, reporting expected input errors without a traceback."""

    try:
        entries = load_entries(
            input_path,
            min_definition_length,
            sidebar_fields=config.sidebar_fields,
            strip_parenthetical_disambiguation=config.title_aliases.strip_parenthetical,
            max_summary_length=config.max_summary_length,
        )
    except (OSError, sqlite3.Error) as exc:
        output.error(f"could not load crawler database {input_path}: {exc}")
        return None
    if not entries:
        output.error(f"no usable entries found in {input_path}")
        return None
    return entries


def configured_lookup_report(
    entries: list[Entry],
    config: ProjectConfig,
    *,
    include_sidebar_aliases: bool,
) -> LookupReport:
    """Build the shared lookup report from one project's configured policy."""

    return build_lookup_report(
        entries,
        include_sidebar_aliases=include_sidebar_aliases,
        title_suffix_aliases=config.title_aliases.suffixes,
        title_prefix_aliases=config.title_aliases.prefixes,
        strip_parenthetical_disambiguation=config.title_aliases.strip_parenthetical,
        title_component_ignore_words=config.title_aliases.component_ignore_words,
        sidebar_alias_labels=config.sidebar_alias_labels,
    )
