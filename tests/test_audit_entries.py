import unittest

from fandom_dict.cli.audit_entries import audit_entries, parse_args
from fandom_dict.cli.output import output_from_args
from fandom_dict.formats.kindle import Entry


class AuditEntriesTests(unittest.TestCase):
    def test_audit_entries_reports_suspicious_patterns(self) -> None:
        findings = audit_entries(
            [
                Entry(
                    "Carl",
                    "https://example/wiki/Carl",
                    "Carl is a crawler with a long enough plain summary to avoid the short-entry audit warning.",
                ),
                Entry("Kimaris", "https://example/wiki/Kimaris", "See: Stuffed Kimaris Figure"),
                Entry("Gallery", "https://example/wiki/Gallery", "A page. Art by Someone"),
                Entry("Broken", "https://example/wiki/Broken", "Broken isa thing of ."),
                Entry("Tiny", "https://example/wiki/Tiny", "Tiny."),
            ]
        )

        by_kind = {(finding.kind, finding.title) for finding in findings}
        self.assertIn(("unresolved-forward", "Kimaris"), by_kind)
        self.assertIn(("gallery-credit", "Gallery"), by_kind)
        self.assertIn(("source-artifact", "Broken"), by_kind)
        self.assertIn(("short", "Tiny"), by_kind)
        self.assertNotIn(("short", "Carl"), by_kind)

    def test_audit_cli_accepts_verbose_and_rejects_paths_only(self) -> None:
        args = parse_args(["-v"])
        output = output_from_args(args)
        self.assertEqual(output.verbosity, "full")
        self.assertFalse(output.paths_only)

        with self.assertRaises(SystemExit):
            parse_args(["--paths-only"])


if __name__ == "__main__":
    unittest.main()
