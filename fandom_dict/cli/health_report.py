#!/usr/bin/env python3
"""Report dictionary health without crawling or building output files."""

from __future__ import annotations

import argparse
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from fandom_dict.cli.audit_entries import AuditFinding, audit_entries
from fandom_dict.cli.common import configured_lookup_report, load_config_for_command, load_entries_for_command
from fandom_dict.cli.output import add_output_arguments, output_from_args
from fandom_dict.config import DEFAULT_CONFIG_PATH, ProjectConfig
from fandom_dict.entries import (
    AliasQualityFinding,
    Entry,
    LookupReport,
    entries_outside_source_categories,
    lookup_quality_findings,
    lookup_report_debug_lines,
)
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
    wiki_cleanup_candidates: tuple["WikiCleanupCandidate", ...]
    alias_quality_findings: tuple["AliasQualityFinding", ...]
    out_of_config_scope: tuple[Entry, ...]


@dataclass(frozen=True)
class WikiCleanupCandidate:
    """One entry that may be easier to improve on the source wiki."""

    title: str
    reasons: tuple[str, ...]
    detail: str
    url: str
    severity: int

    def format_summary(self) -> str:
        """Return the small-verbosity one-line summary without the page URL."""

        reason_text = ", ".join(self.reasons)
        return f"{bold_headword(self.title)}: {reason_text}: {self.detail}"

    def format_detail(self) -> str:
        """Return the full-verbosity one-line detail with the page URL."""

        return f"{self.format_summary()} ({self.url})"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments for the health report."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-c", "--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("-i", "--input", type=Path)
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

    lookup_report = configured_lookup_report(
        entries,
        config,
        include_sidebar_aliases=include_sidebar_aliases,
    )
    terms = tuple(dict.fromkeys((*config.smoke_headwords, *expected_terms)))
    return HealthReport(
        entries=tuple(entries),
        lookup_report=lookup_report,
        formats=format_health(entries, lookup_report),
        term_statuses=term_statuses(terms, entries, lookup_report),
        findings=tuple(audit_entries(entries)),
        wiki_cleanup_candidates=tuple(wiki_cleanup_candidates(entries)),
        alias_quality_findings=tuple(lookup_quality_findings(lookup_report)),
        out_of_config_scope=tuple(entries_outside_source_categories(entries, config.categories)),
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


def wiki_cleanup_candidates(entries: list[Entry]) -> list[WikiCleanupCandidate]:
    """Return entries that probably deserve source-wiki cleanup."""

    candidates = []
    for entry in entries:
        text = text_for_cleanup(entry)
        reasons = cleanup_reasons(entry, text)
        if not reasons:
            continue
        severity = cleanup_severity(reasons)
        candidates.append(
            WikiCleanupCandidate(
                title=entry.title,
                reasons=tuple(reasons),
                detail=snippet(text),
                url=entry.url,
                severity=severity,
            )
        )
    return sorted(candidates, key=lambda item: (item.severity, item.title.casefold()))


def text_for_cleanup(entry: Entry) -> str:
    """Return plain definition text for source-wiki cleanup checks."""

    from fandom_dict.entries import text_from_inline_html

    return text_from_inline_html(entry.definition)


def cleanup_reasons(entry: Entry, text: str) -> list[str]:
    """Return source-wiki cleanup reasons for one entry."""

    reasons: list[str] = []
    normalized = " ".join(text.split())
    if not normalized:
        return reasons
    if has_wiki_markup_artifact(normalized):
        reasons.append("malformed-wiki-markup")
    if is_title_only(entry.title, normalized):
        reasons.append("title-only-definition")
    if is_generated_section_label(normalized):
        reasons.append("generated-section-label")
    generic_type = is_generic_type_definition(entry.title, normalized)
    if generic_type:
        reasons.append("generic-type-definition")
    if is_truncated_cleanup_text(normalized) and not generic_type:
        reasons.append("truncated-looking-definition")
    if len(normalized) < 80 and not entry.details:
        reasons.append("short-definition-without-sidebar")
    if is_quote_intro_ending(normalized):
        reasons.append("quote-or-description-leadin-ending")
    return reasons


def has_wiki_markup_artifact(text: str) -> bool:
    """Return true when wiki markup leaked into the generated definition."""

    return bool(re.search(r"'''|‘’’|’’’|‘‘‘", text))


def is_title_only(title: str, text: str) -> bool:
    """Return true when the definition is just the headword."""

    cleaned_text = re.sub(r"[.!?]+$", "", text).strip().casefold()
    return cleaned_text == title.strip().casefold()


def is_generated_section_label(text: str) -> bool:
    """Return true for generated AI-section labels that are not prose."""

    return bool(re.search(r"\bSection of\s+[^:]{1,160}:\s*$", text))


def is_truncated_cleanup_text(text: str) -> bool:
    """Return true when a short definition looks cut off."""

    if re.search(r"\b(and|or|but|because|with|of|to)\s*$", text, re.I):
        return True
    if len(text) < 120 and not re.search(r"[.!?:;\"”']\s*$", text):
        return True
    return False


def is_generic_type_definition(title: str, text: str) -> bool:
    """Return true for one-line definitions that only name a broad type."""

    plain_title = re.escape(title.strip())
    return bool(
        re.fullmatch(
            rf"(?:the\s+)?{plain_title}\s+(?:is|are)\s+(?:a|an)\s+"
            r"(?:item|race|spell|loot box|box|mob)\.?",
            text.strip(),
            re.I,
        )
    )


def is_quote_intro_ending(text: str) -> bool:
    """Return true for definitions that end by introducing missing quoted text."""

    if not text.rstrip().endswith(":"):
        return False
    return bool(
        re.search(
            r"\b(?:describes?|description|message|voice|from|part of|as part of|of [^:]{1,80})\s*:\s*$",
            text,
            re.I,
        )
    )


def cleanup_severity(reasons: list[str]) -> int:
    """Return a sort rank for source-wiki cleanup candidates."""

    if any(
        reason in reasons
        for reason in (
            "malformed-wiki-markup",
            "title-only-definition",
            "truncated-looking-definition",
            "generated-section-label",
        )
    ):
        return 0
    if "generic-type-definition" in reasons:
        return 1
    if "short-definition-without-sidebar" in reasons:
        return 2
    return 3


def snippet(text: str, max_length: int = 220) -> str:
    """Return a compact one-line snippet for health report output."""

    normalized = " ".join(text.split())
    if len(normalized) <= max_length:
        return normalized
    return f"{normalized[: max_length - 1].rstrip()}…"


def bold_headword(title: str) -> str:
    """Return a lightweight bold wrapper for health-report headwords."""

    return f"**{title}**"


def render_health_report(report: HealthReport, input_path: Path, max_findings: int) -> tuple[list[str], list[str], list[str]]:
    """Return small, warning, and full-detail health report lines."""

    info: list[str] = [
        f"input: {input_path}",
        f"canonical entries: {len(report.entries)}",
        f"entries outside configured category scope: {len(report.out_of_config_scope)}",
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
            f"  alias quality findings: {len(report.alias_quality_findings)}",
            "expected terms:",
        )
    )
    warnings: list[str] = []
    for entry in report.out_of_config_scope[: max(0, max_findings)]:
        warnings.append(
            f"out-of-config-scope: {entry.title}: {', '.join(entry.source_categories)}"
        )
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

    info.append(f"wiki cleanup candidates: {len(report.wiki_cleanup_candidates)}")
    visible_cleanup = report.wiki_cleanup_candidates[: max(0, max_findings)]
    if visible_cleanup:
        info.append("wiki cleanup candidate details:")
        for candidate in visible_cleanup:
            info.append(f"  {candidate.format_summary()}")
    hidden_cleanup_count = max(0, len(report.wiki_cleanup_candidates) - len(visible_cleanup))
    if hidden_cleanup_count:
        info.append(f"  ... {hidden_cleanup_count} more; use --verbosity full to show all cleanup candidates")
    if report.wiki_cleanup_candidates:
        info.extend(
            (
                "wiki cleanup guidance:",
                "  improve the first non-spoilery paragraph; fix broken lead markup; add useful sidebar fields or redirects when known",
            )
        )

    visible_alias_findings = report.alias_quality_findings[: max(0, max_findings)]
    if visible_alias_findings:
        info.append("alias quality details:")
        info.extend(f"  {finding.format()}" for finding in visible_alias_findings)
    hidden_alias_findings = max(0, len(report.alias_quality_findings) - len(visible_alias_findings))
    if hidden_alias_findings:
        info.append(f"  ... {hidden_alias_findings} more; use --verbosity full to show all alias findings")

    detail = lookup_report_debug_lines(report.lookup_report)
    detail.extend(
        f"out-of-config-scope: {entry.title}: {', '.join(entry.source_categories)}"
        for entry in report.out_of_config_scope[max(0, max_findings) :]
    )
    if hidden_count:
        detail.extend(f"audit finding: {finding.format()}" for finding in report.findings[len(visible_findings) :])
    detail.extend(
        f"wiki cleanup candidate: {candidate.format_detail()}"
        for candidate in report.wiki_cleanup_candidates
    )
    if hidden_alias_findings:
        detail.extend(
            f"alias quality finding: {finding.format()}"
            for finding in report.alias_quality_findings[len(visible_alias_findings) :]
        )
    return info, warnings, detail


def main(argv: list[str] | None = None) -> int:
    """Run the offline dictionary health report."""

    args = parse_args(argv)
    output = output_from_args(args)
    config = load_config_for_command(args.config, output)
    if config is None:
        output.close()
        return 1
    input_path = args.input or config.database_path
    try:
        entries = load_entries_for_command(input_path, config, args.min_definition_length, output)
        if entries is None:
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
