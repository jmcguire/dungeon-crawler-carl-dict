import unittest

from dcdict.audit_entries import audit_entries
from dcdict.kindle import Entry


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


if __name__ == "__main__":
    unittest.main()
