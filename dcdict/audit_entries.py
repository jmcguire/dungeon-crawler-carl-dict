#!/usr/bin/env python3
"""Report suspicious generated dictionary entries without fetching the wiki."""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path

from dcdict.kindle import (
    Entry,
    forwarding_target_from_definition,
    load_entries,
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
    parser.add_argument("--input", type=Path, default=Path("data/characters.sqlite"))
    parser.add_argument("--min-definition-length", type=int, default=8)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the offline dictionary entry audit."""

    args = parse_args(argv)
    entries = load_entries(args.input, args.min_definition_length)
    findings = audit_entries(entries)
    for finding in findings:
        print(finding.format())
    print(f"audited {len(entries)} entries; findings: {len(findings)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
