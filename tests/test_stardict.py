import unittest
from functools import cmp_to_key
from pathlib import Path
from tempfile import TemporaryDirectory

from dcdict.build_stardict_dictionary import parse_args
from dcdict.entries import Entry
from dcdict.stardict import (
    BASE_NAME,
    StarDictValidationError,
    build_stardict,
    inspect_stardict,
    stardict_compare,
)


class StarDictTests(unittest.TestCase):
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
        ]

    def test_stardict_comparator_matches_ascii_case_insensitive_then_bytes(self) -> None:
        words = ["beta", "Alpha", "alpha", "Éclair", "Zulu"]
        self.assertEqual(
            sorted(words, key=cmp_to_key(stardict_compare)),
            ["Alpha", "alpha", "beta", "Zulu", "Éclair"],
        )

    def test_build_and_inspect_html_dictionary_with_aliases_and_links(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            result = build_stardict(
                self.sample_entries(),
                Path(tmp_dir),
                "Test Dictionary",
                "Test Author",
                link_entries=True,
            )
            inspection = inspect_stardict(
                result.ifo_path,
                expected_title="Test Dictionary",
                required_headwords=("Carl", "Donut", "Mordecai", "1914", "Fire Fingers"),
                require_links=True,
                check_sdcv=False,
            )

            self.assertEqual(result.entry_count, 5)
            self.assertEqual(result.alias_count, 2)
            self.assertEqual(inspection.canonical_word("1914"), "1914 Box")
            self.assertEqual(inspection.canonical_word("Fire Fingers"), "Fire Fingers Spell")
            self.assertIn("<b>loot box</b>", inspection.lookup("1914") or "")
            self.assertIn("<i><a", inspection.lookup("Carl") or "")
            self.assertIn('href="bword://Donut"', inspection.lookup("Carl") or "")
            self.assertIn("Spoiler note", inspection.lookup("1914") or "")
            self.assertIn("Achievement reward", inspection.lookup("1914") or "")
            self.assertNotIn("idx:", result.dict_path.read_text(encoding="utf-8"))

    def test_synonyms_point_to_canonical_sorted_index_entries(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            result = build_stardict(
                self.sample_entries(), Path(tmp_dir), "Test Dictionary", "Test Author"
            )
            inspection = inspect_stardict(result.ifo_path, check_sdcv=False)
            canonical = {syn.word: inspection.entries[syn.original_index].word for syn in inspection.synonyms}
            self.assertEqual(canonical, {"1914": "1914 Box", "Fire Fingers": "Fire Fingers Spell"})

    def test_multi_target_lookup_uses_combined_canonical_result(self) -> None:
        entries = [
            Entry("Earth", "https://example/Earth", "Earth is a planet."),
            Entry("Earth Box", "https://example/Earth_Box", "Earth Box is a reward."),
        ]
        with TemporaryDirectory() as tmp_dir:
            result = build_stardict(entries, Path(tmp_dir), "Test Dictionary", "Test Author")
            inspection = inspect_stardict(
                result.ifo_path,
                required_headwords=("Earth", "Earth Box"),
                check_sdcv=False,
            )

        self.assertEqual(result.alias_count, 0)
        self.assertEqual(result.multi_lookup_count, 1)
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
        with TemporaryDirectory() as tmp_dir:
            result = build_stardict(entries, Path(tmp_dir), "Test Dictionary", "Test Author")
            inspection = inspect_stardict(
                result.ifo_path,
                required_headwords=("Heal Pet Potion", "Heal Pet Spell", "Heal Pet"),
                check_sdcv=False,
            )

        self.assertEqual(result.alias_count, 0)
        self.assertEqual(result.multi_lookup_count, 1)
        heal_pet_lookup = inspection.lookup("Heal Pet") or ""
        self.assertIn("A potion that helps pets.", heal_pet_lookup)
        self.assertIn("A spell that helps pets.", heal_pet_lookup)

    def test_automatic_aliases_resolve_to_canonical_entries(self) -> None:
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
            result = build_stardict(entries, Path(tmp_dir), "Test Dictionary", "Test Author")
            inspection = inspect_stardict(result.ifo_path, check_sdcv=False)

        self.assertEqual(inspection.canonical_word("Sacs"), "Saccathian")
        self.assertEqual(inspection.canonical_word("Borant"), "Borant Corporation")
        self.assertEqual(inspection.canonical_word("Gravy Boat"), "Ferdinand")
        self.assertEqual(inspection.canonical_word("Valtay"), "Valtay Corporation")
        self.assertEqual(inspection.canonical_word("The Valtay Corporation"), "Valtay Corporation")
        self.assertEqual(inspection.canonical_word("Katia"), "Katia Grim")
        self.assertEqual(inspection.canonical_word("Grim"), "Katia Grim")
        self.assertEqual(inspection.canonical_word("Brain Boilers"), "Brain Boiler")

    def test_title_rule_aliases_resolve_to_canonical_entries(self) -> None:
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
            result = build_stardict(entries, Path(tmp_dir), "Test Dictionary", "Test Author")
            inspection = inspect_stardict(result.ifo_path, check_sdcv=False)

        self.assertEqual(result.alias_count, 5)
        self.assertEqual(result.multi_lookup_count, 1)
        self.assertEqual(inspection.canonical_word("Crybaby"), "Crybaby Achievement")
        self.assertEqual(inspection.canonical_word("Mana"), "Mana Potion")
        self.assertEqual(inspection.canonical_word("Bloodlust"), "Potion of Bloodlust")
        self.assertEqual(inspection.canonical_word("Heal"), "Heal Scroll")
        self.assertEqual(inspection.canonical_word("Nighty Night"), "Wand of Nighty Night")
        water_breathing_lookup = inspection.lookup("Water Breathing") or ""
        self.assertIn("A scroll.", water_breathing_lookup)
        self.assertIn("A ring.", water_breathing_lookup)

    def test_inspector_rejects_bad_index_metadata_and_offsets(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            result = build_stardict(
                self.sample_entries(), Path(tmp_dir), "Test Dictionary", "Test Author"
            )
            text = result.ifo_path.read_text(encoding="utf-8")
            result.ifo_path.write_text(text.replace("idxfilesize=", "idxfilesize=9"), encoding="utf-8")
            with self.assertRaisesRegex(StarDictValidationError, "idxfilesize"):
                inspect_stardict(result.ifo_path, check_sdcv=False)

    def test_builder_rejects_oversized_headwords(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            entries = [Entry("A" * 256, "https://example", "A useful definition.")]
            with self.assertRaisesRegex(StarDictValidationError, "headword"):
                build_stardict(entries, Path(tmp_dir), "Test", "Author")

    def test_cli_defaults_and_link_flag(self) -> None:
        args = parse_args(["--link-entries"])
        self.assertEqual(args.output_dir, Path("build/stardict"))
        self.assertTrue(args.link_entries)
        self.assertEqual(BASE_NAME, "Dungeon-Crawler-Carl-Dictionary")


if __name__ == "__main__":
    unittest.main()
