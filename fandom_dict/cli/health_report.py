#!/usr/bin/env python3
"""Report dictionary health without crawling or building output files."""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from fandom_dict.cli.audit_entries import AuditFinding, audit_entries
from fandom_dict.cli.output import add_output_arguments, output_from_args
from fandom_dict.config import DEFAULT_CONFIG_PATH, ProjectConfig, load_project_config
from fandom_dict.entries import Entry, LookupReport, build_lookup_report, load_entries, lookup_report_debug_lines
from fandom_dict.formats.kindle import kindle_lookup_record_count


@dataclass(frozen=True)
class FormatHealth:
    """Canonical entry and lookup-record counts for one output format."""

    name: str
    entry_count: int
    lookup_record_count: int


@dataclass(frozen=True)
class TermStatus:
    """Presence status for one expected lookup term."""

    term: str
    kind: str
    targets: tuple[str, ...]

    @property
    def found(self) -> bool:
        """Return true when the term exists as a title, alias, or multi-target lookup."""

        return self.kind != "missing"


@dataclass(frozen=True)
class HealthReport:
    """Computed dictionary health data used by the CLI and tests."""

    entries: tuple[Entry, ...]
    lookup_report: LookupReport
    formats: tuple[FormatHealth, ...]
    term_statuses: tuple[TermStatus, ...]
    findings: tuple[AuditFinding, ...]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments for the health report."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--min-definition-length", type=int, default=8)
    parser.add_argument(
        "--expected-term",
        action="append",
        default=[],
        help="Extra lookup term to check. May be repeated; config smoke headwords are always checked.",
    )
    parser.add_argument(
        "--max-findings",
        type=int,
        default=25,
        help="Maximum audit findings to print at small verbosity. Full verbosity prints all.",
    )
    parser.add_argument(
        "--no-sidebar-aliases",
        action="store_true",
        help="Disable lookup aliases derived from wiki sidebar alias fields.",
    )
    add_output_arguments(parser)
    return parser.parse_args(argv)


def build_health_report(
    entries: list[Entry],
    config: ProjectConfig,
    *,
    expected_terms: tuple[str, ...] = (),
    include_sidebar_aliases: bool = True,
) -> HealthReport:
    """Return counts and findings for one normalized entry set."""

    lookup_report = build_lookup_report(
        entries,
        include_sidebar_aliases=include_sidebar_aliases,
        title_suffix_aliases=config.title_aliases.suffixes,
        title_prefix_aliases=config.title_aliases.prefixes,
        strip_parenthetical_disambiguation=config.title_aliases.strip_parenthetical,
        title_component_ignore_words=config.title_aliases.component_ignore_words,
        sidebar_alias_labels=config.sidebar_alias_labels,
    )
    terms = tuple(dict.fromkeys((*config.smoke_headwords, *expected_terms)))
    return HealthReport(
        entries=tuple(entries),
        lookup_report=lookup_report,
        formats=format_health(entries, lookup_report),
        term_statuses=term_statuses(terms, entries, lookup_report),
        findings=tuple(audit_entries(entries)),
    )


def format_health(entries: list[Entry], lookup_report: LookupReport) -> tuple[FormatHealth, ...]:
    """Return expected canonical and lookup-record counts for each format."""

    entry_count = len(entries)
    extra_combined_records = extra_combined_lookup_record_count(entries, lookup_report)
    combined_lookup_record_count = entry_count + extra_combined_records
    return (
        FormatHealth("Kindle", entry_count, kindle_lookup_record_count(entries, lookup_report)),
        FormatHealth("StarDict", entry_count, combined_lookup_record_count),
        FormatHealth("Kobo", entry_count, combined_lookup_record_count),
    )


def extra_combined_lookup_record_count(entries: list[Entry], lookup_report: LookupReport) -> int:
    """Return non-canonical combined lookup records used by StarDict and Kobo."""

    folded_titles = {entry.title.casefold() for entry in entries}
    return sum(1 for lookup in lookup_report.multi_target_lookups if lookup.word.casefold() not in folded_titles)


def term_statuses(
    terms: tuple[str, ...],
    entries: list[Entry],
    lookup_report: LookupReport,
) -> tuple[TermStatus, ...]:
    """Return presence status for expected lookup terms."""

    canonical_by_folded = {entry.title.casefold(): entry.title for entry in entries}
    alias_targets: dict[str, str] = {}
    alias_display: dict[str, str] = {}
    for target, forms in lookup_report.aliases.items():
        for alias in forms:
            if alias.casefold() != target.casefold():
                alias_targets[alias.casefold()] = target
                alias_display[alias.casefold()] = alias
    multi_targets = {lookup.word.casefold(): lookup for lookup in lookup_report.multi_target_lookups}
    statuses = []
    for term in terms:
        folded = term.casefold()
        if folded in multi_targets:
            lookup = multi_targets[folded]
            statuses.append(TermStatus(lookup.word, "multi-target", lookup.targets))
        elif folded in canonical_by_folded:
            statuses.append(TermStatus(canonical_by_folded[folded], "canonical", (canonical_by_folded[folded],)))
        elif folded in alias_targets:
            statuses.append(TermStatus(alias_display[folded], "alias", (alias_targets[folded],)))
        else:
            statuses.append(TermStatus(term, "missing", ()))
    return tuple(statuses)


def finding_counts(findings: tuple[AuditFinding, ...]) -> Counter[str]:
    """Return audit finding counts by kind."""

    return Counter(finding.kind for finding in findings)


def render_health_report(report: HealthReport, input_path: Path, max_findings: int) -> tuple[list[str], list[str], list[str]]:
    """Return small, warning, and full-detail health report lines."""

    info: list[str] = [
        f"input: {input_path}",
        f"canonical entries: {len(report.entries)}",
        "format counts:",
    ]
    for item in report.formats:
        info.append(f"  {item.name}: entries={item.entry_count}, lookup records={item.lookup_record_count}")
    info.extend(
        (
            "lookup counts:",
            f"  single-target aliases: {report.lookup_report.single_target_alias_count}",
            f"  multi-target lookups: {report.lookup_report.multi_target_lookup_count}",
            f"  omitted aliases: {report.lookup_report.omitted_alias_count}",
            "expected terms:",
        )
    )
    warnings: list[str] = []
    for status in report.term_statuses:
        if status.kind == "missing":
            warnings.append(f"missing expected term: {status.term}")
        else:
            info.append(f"  {status.term}: {status.kind} -> {' | '.join(status.targets)}")

    counts = finding_counts(report.findings)
    info.append(f"audit findings: {len(report.findings)}")
    for kind in sorted(counts):
        info.append(f"  {kind}: {counts[kind]}")
    visible_findings = report.findings[:max(0, max_findings)]
    if visible_findings:
        info.append("audit finding details:")
        for finding in visible_findings:
            info.append(f"  {finding.format()}")
    hidden_count = max(0, len(report.findings) - len(visible_findings))
    if hidden_count:
        info.append(f"  ... {hidden_count} more; use --verbosity full to show all details")

    detail = lookup_report_debug_lines(report.lookup_report)
    if hidden_count:
        detail.extend(f"audit finding: {finding.format()}" for finding in report.findings[len(visible_findings) :])
    return info, warnings, detail


def main(argv: list[str] | None = None) -> int:
    """Run the offline dictionary health report."""

    args = parse_args(argv)
    output = output_from_args(args)
    config = load_project_config(args.config)
    input_path = args.input or config.database_path
    try:
        entries = load_entries(
            input_path,
            args.min_definition_length,
            sidebar_fields=config.sidebar_fields,
            strip_parenthetical_disambiguation=config.title_aliases.strip_parenthetical,
            max_summary_length=config.max_summary_length,
        )
        if not entries:
            output.error(f"no usable entries found in {input_path}")
            return 1
        report = build_health_report(
            entries,
            config,
            expected_terms=tuple(args.expected_term),
            include_sidebar_aliases=not args.no_sidebar_aliases,
        )
        info, warnings, detail = render_health_report(report, input_path, args.max_findings)
        for line in info:
            output.info(line)
        for line in warnings:
            output.warning(line)
        for line in detail:
            output.detail(line)
        return 0
    finally:
        output.close()


if __name__ == "__main__":
    raise SystemExit(main())
