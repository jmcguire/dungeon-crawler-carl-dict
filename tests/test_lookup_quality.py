import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from fandom_dict.entries import Entry, build_lookup_report
from fandom_dict.formats.kindle import kindle_lookup_record_count
from fandom_dict.formats.kobo import entries_to_dictfile
from fandom_dict.formats.stardict import build_stardict


class LookupQualityTests(unittest.TestCase):
    def test_reader_facing_component_aliases_and_collisions(self) -> None:
        entries = reader_lookup_fixture()
        report = build_lookup_report(entries, **reader_lookup_options())

        self.assertIn("Valtay", report.aliases["Valtay Corporation"])
        self.assertIn("Desperado", report.aliases["Desperado Club"])
        self.assertEqual(report.multi_target_lookups[0].word, "Earth")
        self.assertEqual(report.multi_target_lookups[0].targets, ("Earth", "Earth Box"))
        self.assertEqual(report.single_target_alias_count, 2)
        self.assertEqual(report.multi_target_lookup_count, 1)

    def test_output_formats_share_lookup_counts_for_same_fixture(self) -> None:
        entries = reader_lookup_fixture()
        options = reader_lookup_options()
        report = build_lookup_report(entries, **options)

        with TemporaryDirectory() as tmp_dir:
            stardict = build_stardict(entries, Path(tmp_dir) / "stardict", "Test Dictionary", "Test Author", **options)
        _dictfile, kobo_aliases, kobo_multi, kobo_omitted, kobo_records = entries_to_dictfile(entries, **options)

        self.assertEqual(stardict.alias_count, report.single_target_alias_count)
        self.assertEqual(kobo_aliases, report.single_target_alias_count)
        self.assertEqual(stardict.multi_lookup_count, report.multi_target_lookup_count)
        self.assertEqual(kobo_multi, report.multi_target_lookup_count)
        self.assertEqual(stardict.omitted_alias_count, report.omitted_alias_count)
        self.assertEqual(kobo_omitted, report.omitted_alias_count)
        self.assertEqual(kindle_lookup_record_count(entries, report), 5)
        self.assertEqual(stardict.lookup_record_count, 4)
        self.assertEqual(kobo_records, 4)


def reader_lookup_fixture() -> list[Entry]:
    return [
        Entry("Valtay Corporation", "https://example/Valtay", "A corporation."),
        Entry("Desperado Club", "https://example/Desperado_Club", "A club."),
        Entry("Earth", "https://example/Earth", "A planet."),
        Entry("Earth Box", "https://example/Earth_Box", "A reward box."),
    ]


def reader_lookup_options() -> dict[str, object]:
    return {
        "title_suffix_aliases": (" Box",),
        "title_prefix_aliases": (),
        "title_component_ignore_words": ("Corporation", "Club", "Box"),
    }


if __name__ == "__main__":
    unittest.main()
