#!/usr/bin/env python3
"""Report suspicious generated dictionary entries without fetching the wiki."""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path

from fandom_dict.cli.common import load_config_for_command, load_entries_for_command
from fandom_dict.cli.output import add_output_arguments, output_from_args
from fandom_dict.config import DEFAULT_CONFIG_PATH
from fandom_dict.entries import (
    Entry,
    forwarding_target_from_definition,
    text_from_inline_html,
)


@dataclass(frozen=True)
class AuditFinding:
    """One possible dictionary quality issue found in generated entries."""

    kind: str
    title: str
    detail: str

    def format(self) -> str:
        """Return a stable one-line report format."""

        return f"{self.kind}: {self.title}: {self.detail}"


def audit_entries(entries: list[Entry]) -> list[AuditFinding]:
    """Find entries that deserve a human look before release."""

    titles = {entry.title.casefold() for entry in entries}
    findings: list[AuditFinding] = []

    for entry in entries:
        text = text_from_inline_html(entry.definition)
        forwarding_target = forwarding_target_from_definition(entry.definition)
        if forwarding_target and forwarding_target.casefold() not in titles:
            findings.append(AuditFinding("unresolved-forward", entry.title, forwarding_target))
        if re.search(r"\b(?:official\s+art|art)\s+by\b", text, re.I):
            findings.append(AuditFinding("gallery-credit", entry.title, text))
        if re.search(r"duplicate page|for more information|please see", text, re.I):
            findings.append(AuditFinding("maintenance-text", entry.title, text))
        if re.search(r"\bisa\b|\bof\s+\.|\s+,|\s+\.", text):
            findings.append(AuditFinding("source-artifact", entry.title, text))
        if re.search(r"\b(and|or|but|because|with|of|to)\s*$", text, re.I):
            findings.append(AuditFinding("truncated", entry.title, text))
        if len(text) < 80 and not entry.details and not forwarding_target:
            findings.append(AuditFinding("short", entry.title, text))

    return findings


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments for the audit command."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-c", "--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("-i", "--input", type=Path)
    parser.add_argument("--min-definition-length", type=int, default=8)
    add_output_arguments(parser)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the offline dictionary entry audit."""

    args = parse_args(argv)
    output = output_from_args(args)
    config = load_config_for_command(args.config, output)
    if config is None:
        output.close()
        return 1
    input_path = args.input or config.database_path
    entries = load_entries_for_command(input_path, config, args.min_definition_length, output)
    if entries is None:
        output.close()
        return 1
    findings = audit_entries(entries)
    for finding in findings:
        output.info(finding.format())
    output.info(f"audited {len(entries)} entries; findings: {len(findings)}")
    output.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
