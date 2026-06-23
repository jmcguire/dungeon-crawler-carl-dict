import unittest
import gzip
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from dcdict.build_kobo_dictionary import main, parse_args
from dcdict.entries import Entry
from dcdict.kobo import (
    DICTGEN_OUTPUT_NAME,
    KoboValidationError,
    build_kobo,
    entries_to_dictfile,
    find_dictgen,
    detect_dictgen_version,
    inspect_kobo,
    kobo_prefix,
    synthetic_kobo_zip,
)


class KoboTests(unittest.TestCase):
    def sample_entries(self) -> list[Entry]:
        return [
            Entry(
                "1914 Box",
                "https://example/1914",
                "A <b>loot box</b> awarded to Carl.",
                "This article contains spoilers for Book 4.",
                (("Source", "Achievement reward"),),
            ),
            Entry("Carl", "https://example/Carl", "Carl travels with <i>Donut</i>."),
            Entry("Donut", "https://example/Donut", "Donut is a crawler with Carl."),
            Entry("Fire Fingers Spell", "https://example/Fire", "A spell used by Mordecai."),
            Entry("Mordecai", "https://example/Mordecai", "Mordecai is an experienced guide."),
            Entry("Red Beret", "https://example/Red", "Red Beret is an item."),
        ]

    def test_kobo_prefix_examples(self) -> None:
        cases = {
            "test": "te",
            "a": "aa",
            "Èe": "èe",
            "multiple words": "mu",
            "àççèñts": "àç",
            "à": "àa",
            "ç": "ça",
            "": "11",
            " ": "11",
            " x": "xa",
            " 123": "11",
            "x 23": "xa",
            "д ": "д",
            "дaд": "дa",
            "未未": "未未",
            "未": "未a",
        }
        for word, prefix in cases.items():
            with self.subTest(word=word):
                self.assertEqual(kobo_prefix(word), prefix)

    def test_dictfile_preserves_formatting_and_suffix_alias_variants(self) -> None:
        dictfile, alias_count, multi_lookup_count, omitted_alias_count = entries_to_dictfile(self.sample_entries())
        self.assertEqual(alias_count, 2)
        self.assertEqual(multi_lookup_count, 0)
        self.assertGreaterEqual(omitted_alias_count, 0)
        self.assertIn("@ 1914 Box\n& 1914\n::\n<html>", dictfile)
        self.assertIn("@ Fire Fingers Spell\n& Fire Fingers\n::\n<html>", dictfile)
        self.assertIn("@ Red Beret\n::\n<html>", dictfile)
        self.assertNotIn("& Red\n", dictfile)
        self.assertIn("<b>loot box</b>", dictfile)
        self.assertIn("<i>Donut</i>", dictfile)
        self.assertIn("Spoiler note", dictfile)
        self.assertIn("Achievement reward", dictfile)

    def test_synthetic_kobo_zip_inspects_representative_lookups(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / DICTGEN_OUTPUT_NAME
            synthetic_kobo_zip(path, self.sample_entries())
            inspection = inspect_kobo(
                path,
                required_headwords=("Carl", "Donut", "Mordecai", "1914", "Fire Fingers"),
            )
            self.assertEqual(inspection.canonical_word("1914"), "1914 Box")
            self.assertEqual(inspection.canonical_word("Fire Fingers"), "Fire Fingers Spell")
            self.assertIsNone(inspection.lookup("Red"))
            self.assertIn("<b>loot box</b>", inspection.lookup("1914") or "")
            self.assertIn("<i>Donut</i>", inspection.lookup("Carl") or "")
            self.assertEqual(inspection.alias_count, 2)

    def test_multi_target_lookup_uses_combined_canonical_result(self) -> None:
        entries = [
            Entry("Earth", "https://example/Earth", "Earth is a planet."),
            Entry("Earth Box", "https://example/Earth_Box", "Earth Box is a reward."),
        ]
        dictfile, alias_count, multi_lookup_count, omitted_alias_count = entries_to_dictfile(entries)

        self.assertEqual(alias_count, 0)
        self.assertEqual(multi_lookup_count, 1)
        self.assertEqual(omitted_alias_count, 0)
        self.assertIn("@ Earth\n::\n<html>", dictfile)
        self.assertIn("<b>Earth</b>", dictfile)
        self.assertIn("<b>Earth Box</b>", dictfile)
        self.assertNotIn("& Earth\n", dictfile)

        with TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / DICTGEN_OUTPUT_NAME
            synthetic_kobo_zip(path, entries)
            inspection = inspect_kobo(path, required_headwords=("Earth", "Earth Box"))

        earth_lookup = inspection.lookup("Earth") or ""
        self.assertIn("Earth is a planet.", earth_lookup)
        self.assertIn("Earth Box is a reward.", earth_lookup)
        self.assertEqual(inspection.canonical_word("Earth"), "Earth")
        self.assertEqual(inspection.canonical_word("Earth Box"), "Earth Box")

    def test_title_rule_multi_target_lookup_uses_combined_result(self) -> None:
        entries = [
            Entry("Heal Pet Potion", "https://example/Heal_Pet_Potion", "A potion that helps pets."),
            Entry("Heal Pet Spell", "https://example/Heal_Pet_Spell", "A spell that helps pets."),
        ]
        dictfile, alias_count, multi_lookup_count, omitted_alias_count = entries_to_dictfile(entries)

        self.assertEqual(alias_count, 0)
        self.assertEqual(multi_lookup_count, 1)
        self.assertEqual(omitted_alias_count, 0)
        self.assertIn("@ Heal Pet\n::\n<html>", dictfile)
        self.assertNotIn("& Heal Pet\n", dictfile)

        with TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / DICTGEN_OUTPUT_NAME
            synthetic_kobo_zip(path, entries)
            inspection = inspect_kobo(path, required_headwords=("Heal Pet", "Heal Pet Potion", "Heal Pet Spell"))

        heal_pet_lookup = inspection.lookup("Heal Pet") or ""
        self.assertIn("A potion that helps pets.", heal_pet_lookup)
        self.assertIn("A spell that helps pets.", heal_pet_lookup)

    def test_automatic_aliases_become_variants(self) -> None:
        entries = [
            Entry("Saccathian", "https://example/Saccathian", "<b>Saccathian</b> (or <b>Sacs</b>) are common."),
            Entry(
                "Borant Corporation",
                "https://example/Borant",
                "The <b>Borant Corporation</b> (aka <b>Borant</b>) is a company.",
            ),
            Entry("Ferdinand", "https://example/Ferdinand", '<b>Ferdinand</b> (actually named "Gravy Boat") is a cat.'),
            Entry(
                "Valtay Corporation",
                "https://example/Valtay",
                "The <b>Valtay Corporation</b> is a massive company.",
                details=(("Aliases", "The Valtay"),),
            ),
            Entry("Katia Grim", "https://example/Katia", "A crawler.", details=(("Race", "Human"),)),
            Entry("Brain Boiler", "https://example/Brain_Boiler", "<b>Brain Boilers</b> are a mob."),
        ]
        with TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / DICTGEN_OUTPUT_NAME
            synthetic_kobo_zip(path, entries)
            inspection = inspect_kobo(
                path,
                required_headwords=(
                    "Sacs",
                    "Borant",
                    "Gravy Boat",
                    "Valtay",
                    "The Valtay Corporation",
                    "Katia",
                    "Grim",
                    "Brain Boilers",
                ),
            )

        self.assertEqual(inspection.canonical_word("Sacs"), "Saccathian")
        self.assertEqual(inspection.canonical_word("Borant"), "Borant Corporation")
        self.assertEqual(inspection.canonical_word("Gravy Boat"), "Ferdinand")
        self.assertEqual(inspection.canonical_word("Valtay"), "Valtay Corporation")
        self.assertEqual(inspection.canonical_word("The Valtay Corporation"), "Valtay Corporation")
        self.assertEqual(inspection.canonical_word("Katia"), "Katia Grim")
        self.assertEqual(inspection.canonical_word("Grim"), "Katia Grim")
        self.assertEqual(inspection.canonical_word("Brain Boilers"), "Brain Boiler")

    def test_title_rule_aliases_become_variants_or_multi_lookup(self) -> None:
        entries = [
            Entry("Crybaby Achievement", "https://example/Crybaby_Achievement", "An achievement."),
            Entry("Mana Potion", "https://example/Mana_Potion", "A potion."),
            Entry("Potion of Bloodlust", "https://example/Potion_of_Bloodlust", "Another potion."),
            Entry("Heal Scroll", "https://example/Heal_Scroll", "A scroll."),
            Entry("Scroll of Water Breathing", "https://example/Scroll_of_Water_Breathing", "A scroll."),
            Entry("Ring of Water Breathing", "https://example/Ring_of_Water_Breathing", "A ring."),
            Entry("Wand of Nighty Night", "https://example/Wand_of_Nighty_Night", "A wand."),
        ]
        with TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / DICTGEN_OUTPUT_NAME
            synthetic_kobo_zip(path, entries)
            inspection = inspect_kobo(
                path,
                required_headwords=(
                    "Crybaby",
                    "Mana",
                    "Bloodlust",
                    "Heal",
                    "Water Breathing",
                    "Nighty Night",
                ),
            )

        self.assertEqual(inspection.canonical_word("Crybaby"), "Crybaby Achievement")
        self.assertEqual(inspection.canonical_word("Mana"), "Mana Potion")
        self.assertEqual(inspection.canonical_word("Bloodlust"), "Potion of Bloodlust")
        self.assertEqual(inspection.canonical_word("Heal"), "Heal Scroll")
        self.assertEqual(inspection.canonical_word("Nighty Night"), "Wand of Nighty Night")
        water_breathing_lookup = inspection.lookup("Water Breathing") or ""
        self.assertIn("A scroll.", water_breathing_lookup)
        self.assertIn("A ring.", water_breathing_lookup)

    def test_inspector_accepts_gzipped_dicthtml_members(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / DICTGEN_OUTPUT_NAME
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("words", b"test")
                archive.writestr(
                    "te.html",
                    gzip.compress(b'<html><w><a name="test" /><p>Definition</p></w></html>'),
                )
            inspection = inspect_kobo(path, required_headwords=("test",))
            self.assertEqual(inspection.canonical_word("test"), "test")

    def test_inspector_rejects_bad_zip_layout_and_markup(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            nested = root / "nested.zip"
            with zipfile.ZipFile(nested, "w") as archive:
                archive.writestr("folder/te.html", "<html></html>")
                archive.writestr("words", b"test")
            with self.assertRaisesRegex(KoboValidationError, "top-level"):
                inspect_kobo(nested)

            bad_markup = root / "bad-markup.zip"
            with zipfile.ZipFile(bad_markup, "w") as archive:
                archive.writestr("words", b"test")
                archive.writestr("te.html", '<html><w><a name="test" /><script>x</script></w></html>')
            with self.assertRaisesRegex(KoboValidationError, "unsupported"):
                inspect_kobo(bad_markup)

            bad_prefix = root / "bad-prefix.zip"
            with zipfile.ZipFile(bad_prefix, "w") as archive:
                archive.writestr("words", b"test")
                archive.writestr("zz.html", '<html><w><a name="test" /><p>Definition</p></w></html>')
            with self.assertRaisesRegex(KoboValidationError, "wrong prefix"):
                inspect_kobo(bad_prefix)

    def test_cli_defaults(self) -> None:
        args = parse_args([])
        self.assertIsNone(args.output_dir)
        self.assertIsNone(args.output_name)

    def test_cli_reports_missing_dictgen_cleanly(self) -> None:
        with mock.patch("dcdict.build_kobo_dictionary.load_entries", return_value=self.sample_entries()), mock.patch(
            "dcdict.kobo.find_dictgen", return_value=None
        ):
            self.assertEqual(main(["--input", "ignored.sqlite"]), 1)

    def test_detect_dictgen_version_prefers_version_line(self) -> None:
        completed = mock.Mock(stdout="Usage: dictgen [options]\n\nVersion: dictgen dev\n", returncode=0)
        with mock.patch("dcdict.kobo.subprocess.run", return_value=completed):
            self.assertEqual(detect_dictgen_version("/usr/local/bin/dictgen"), "dictgen dev")

    @unittest.skipUnless(find_dictgen(), "dictgen is not installed")
    def test_real_dictgen_output_passes_kobo_smoke_tests(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            result = build_kobo(self.sample_entries(), Path(tmp_dir))
            inspection = inspect_kobo(
                result.dictzip_path,
                required_headwords=("Carl", "Donut", "Mordecai", "1914", "Fire Fingers"),
            )
            self.assertEqual(result.entry_count, 6)
            self.assertEqual(result.alias_count, 2)
            self.assertEqual(inspection.canonical_word("1914"), "1914 Box")


if __name__ == "__main__":
    unittest.main()
