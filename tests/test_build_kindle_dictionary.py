import os
import sqlite3
import stat
import sys
import textwrap
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from fandom_dict.config import SidebarField
from fandom_dict.formats.kindle import (
    Entry,
    build_alias_report,
    build_aliases,
    build_lookup_report,
    build_dictionary_sources,
    compile_with_kindlegen,
    forwarding_target_from_definition,
    kindle_identifier,
    link_definition_references,
    load_entries,
    sanitize_inline_html,
    sidebar_details_from_html,
    spoiler_notice_from_html,
    write_opf,
    write_xhtml,
    write_xhtml_with_options,
)
from fandom_dict.cli.build_kindle_dictionary import main as build_kindle_main
from fandom_dict.cli.build_kindle_dictionary import normalize_release_version


class BuildKindleDictionaryTests(unittest.TestCase):
    def test_load_entries_orders_filters_and_normalizes_text(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "characters.sqlite"
            conn = sqlite3.connect(db_path)
            conn.execute(
                """
                CREATE TABLE pages (
                    title TEXT,
                    url TEXT,
                    source_category TEXT,
                    first_paragraph TEXT,
                    raw_html TEXT,
                    status TEXT
                )
                """
            )
            conn.executemany(
                "INSERT INTO pages VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (
                        "Donut",
                        "https://example/wiki/Donut",
                        "Characters, Groups",
                        " <b>Princess\u00a0Donut</b>   is royalty.[1] ",
                        """
                        <div class="dcc-highlight"><b>This article contains unmarked spoilers for Book 2.</b></div>
                        <aside class="portable-infobox">
                          <h2 class="pi-header" data-source="crawler_info">BIOGRAPHICAL INFO</h2>
                          <div class="pi-data" data-source="aliases">
                            <h3 class="pi-data-label">ALIASES</h3>
                            <div class="pi-data-value">GC, BWR, NW Princess Donut</div>
                          </div>
                          <div class="pi-data" data-source="origin">
                            <h3 class="pi-data-label">ORIGIN</h3>
                            <div class="pi-data-value">Earth: Seattle, WA</div>
                          </div>
                          <div class="pi-data" data-source="class">
                            <h3 class="pi-data-label">CLASS</h3>
                            <div class="pi-data-value">Legendary Diva</div>
                          </div>
                        </aside>
                        """,
                        "ok",
                    ),
                    ("Bad", "https://example/wiki/Bad", "", "Ignored", "", "error"),
                    ("Tiny", "https://example/wiki/Tiny", "Characters", "short", "", "ok"),
                    ("Carl", "https://example/wiki/Carl", "Characters", "Carl is a crawler.", "", "ok"),
                ],
            )
            conn.commit()

            entries = load_entries(db_path, min_definition_length=8)

        self.assertEqual([entry.title for entry in entries], ["Carl", "Donut"])
        self.assertEqual(entries[1].source_categories, ("Characters", "Groups"))
        self.assertEqual(entries[1].definition, "<b>Princess Donut</b> is royalty.")
        self.assertEqual(entries[1].spoiler_notice, "This article contains unmarked spoilers for Book 2.")
        self.assertEqual(
            entries[1].details,
            (("Aliases", "GC, BWR, NW Princess Donut"), ("Origin", "Earth: Seattle, WA")),
        )

    def test_load_entries_tolerates_missing_source_category_column(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "characters.sqlite"
            conn = sqlite3.connect(db_path)
            conn.execute(
                """
                CREATE TABLE pages (
                    title TEXT,
                    url TEXT,
                    first_paragraph TEXT,
                    raw_html TEXT,
                    status TEXT
                )
                """
            )
            conn.execute(
                "INSERT INTO pages VALUES (?, ?, ?, '', 'ok')",
                ("Katia Grim", "https://example/wiki/Katia_Grim", "Katia is a crawler."),
            )
            conn.commit()
            conn.close()

            entries = load_entries(db_path, min_definition_length=8)

        self.assertEqual(entries[0].title, "Katia Grim")
        self.assertEqual(entries[0].source_categories, ())

    def test_load_entries_parses_category_prefixed_source_categories(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "characters.sqlite"
            conn = sqlite3.connect(db_path)
            conn.execute(
                """
                CREATE TABLE pages (
                    title TEXT,
                    url TEXT,
                    source_category TEXT,
                    first_paragraph TEXT,
                    raw_html TEXT,
                    status TEXT
                )
                """
            )
            conn.execute(
                "INSERT INTO pages VALUES (?, ?, ?, ?, '', 'ok')",
                (
                    "Katia Grim",
                    "https://example/wiki/Katia_Grim",
                    "Category:Characters, Groups, Characters",
                    "Katia is a crawler.",
                ),
            )
            conn.commit()
            conn.close()

            entries = load_entries(db_path, min_definition_length=8)

        self.assertEqual(entries[0].source_categories, ("Characters", "Groups"))

    def test_load_entries_strips_collision_free_parenthetical_title(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "characters.sqlite"
            conn = sqlite3.connect(db_path)
            conn.execute(
                """
                CREATE TABLE pages (
                    title TEXT,
                    url TEXT,
                    source_category TEXT,
                    first_paragraph TEXT,
                    raw_html TEXT,
                    status TEXT
                )
                """
            )
            conn.executemany(
                "INSERT INTO pages VALUES (?, ?, ?, ?, ?, ?)",
                [
                    ("Torch (Item)", "https://example/wiki/Torch", "Items", "Torch is an item.", "", "ok"),
                    ("Carl", "https://example/wiki/Carl", "Characters", "Carl is a crawler.", "", "ok"),
                ],
            )
            conn.commit()

            entries = load_entries(db_path, min_definition_length=8)

        self.assertIn("Torch", {entry.title for entry in entries})
        self.assertNotIn("Torch (Item)", {entry.title for entry in entries})

    def test_load_entries_keeps_parenthetical_title_when_stripped_title_collides(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "characters.sqlite"
            conn = sqlite3.connect(db_path)
            conn.execute(
                """
                CREATE TABLE pages (
                    title TEXT,
                    url TEXT,
                    source_category TEXT,
                    first_paragraph TEXT,
                    raw_html TEXT,
                    status TEXT
                )
                """
            )
            conn.executemany(
                "INSERT INTO pages VALUES (?, ?, ?, ?, ?, ?)",
                [
                    ("Baelon Targaryen (son of Aerys)", "https://example/wiki/Baelon_A", "Characters", "One Baelon.", "", "ok"),
                    ("Baelon Targaryen (son of Viserys I)", "https://example/wiki/Baelon_V", "Characters", "Another Baelon.", "", "ok"),
                ],
            )
            conn.commit()

            entries = load_entries(db_path, min_definition_length=8)

        self.assertEqual(
            {entry.title for entry in entries},
            {"Baelon Targaryen (son of Aerys)", "Baelon Targaryen (son of Viserys I)"},
        )

    def test_load_entries_resolves_simple_forwarding_entry(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "characters.sqlite"
            conn = sqlite3.connect(db_path)
            conn.execute(
                """
                CREATE TABLE pages (
                    title TEXT,
                    url TEXT,
                    source_category TEXT,
                    first_paragraph TEXT,
                    raw_html TEXT,
                    status TEXT
                )
                """
            )
            conn.executemany(
                "INSERT INTO pages VALUES (?, ?, ?, ?, ?, ?)",
                [
                    ("ABC", "https://example/wiki/ABC", "Characters", "ABC is real.", "", "ok"),
                    ("Kimaris", "https://example/wiki/Kimaris", "Characters", "See: ABC", "", "ok"),
                ],
            )
            conn.commit()

            entries = load_entries(db_path, min_definition_length=8)

        definitions = {entry.title: entry.definition for entry in entries}
        self.assertEqual(definitions["Kimaris"], "ABC is real.")

    def test_load_entries_resolves_forwarding_chain(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "characters.sqlite"
            conn = sqlite3.connect(db_path)
            conn.execute(
                """
                CREATE TABLE pages (
                    title TEXT,
                    url TEXT,
                    source_category TEXT,
                    first_paragraph TEXT,
                    raw_html TEXT,
                    status TEXT
                )
                """
            )
            conn.executemany(
                "INSERT INTO pages VALUES (?, ?, ?, ?, ?, ?)",
                [
                    ("Final", "https://example/wiki/Final", "Characters", "Final has the answer.", "", "ok"),
                    ("Middle", "https://example/wiki/Middle", "Characters", "See: Final", "", "ok"),
                    ("Start", "https://example/wiki/Start", "Characters", "See: Middle", "", "ok"),
                ],
            )
            conn.commit()

            entries = load_entries(db_path, min_definition_length=8)

        definitions = {entry.title: entry.definition for entry in entries}
        self.assertEqual(definitions["Start"], "Final has the answer.")
        self.assertEqual(definitions["Middle"], "Final has the answer.")

    def test_load_entries_leaves_forwarding_entry_when_target_is_missing(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "characters.sqlite"
            conn = sqlite3.connect(db_path)
            conn.execute(
                """
                CREATE TABLE pages (
                    title TEXT,
                    url TEXT,
                    source_category TEXT,
                    first_paragraph TEXT,
                    raw_html TEXT,
                    status TEXT
                )
                """
            )
            conn.execute(
                "INSERT INTO pages VALUES (?, ?, ?, ?, ?, ?)",
                ("Kimaris", "https://example/wiki/Kimaris", "Characters", "See: Missing Target", "", "ok"),
            )
            conn.commit()

            entries = load_entries(db_path, min_definition_length=8)

        self.assertEqual(entries[0].definition, "See: Missing Target")

    def test_load_entries_leaves_forwarding_cycle_unresolved(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "characters.sqlite"
            conn = sqlite3.connect(db_path)
            conn.execute(
                """
                CREATE TABLE pages (
                    title TEXT,
                    url TEXT,
                    source_category TEXT,
                    first_paragraph TEXT,
                    raw_html TEXT,
                    status TEXT
                )
                """
            )
            conn.executemany(
                "INSERT INTO pages VALUES (?, ?, ?, ?, ?, ?)",
                [
                    ("Recursion", "https://example/wiki/Recursion", "Characters", "See: Loop", "", "ok"),
                    ("Loop", "https://example/wiki/Loop", "Characters", "See: Recursion", "", "ok"),
                ],
            )
            conn.commit()

            entries = load_entries(db_path, min_definition_length=8)

        definitions = {entry.title: entry.definition for entry in entries}
        self.assertEqual(definitions["Recursion"], "See: Loop")
        self.assertEqual(definitions["Loop"], "See: Recursion")

    def test_load_entries_resolves_duplicate_page_forwarding_case_insensitively(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "characters.sqlite"
            conn = sqlite3.connect(db_path)
            conn.execute(
                """
                CREATE TABLE pages (
                    title TEXT,
                    url TEXT,
                    source_category TEXT,
                    first_paragraph TEXT,
                    raw_html TEXT,
                    status TEXT
                )
                """
            )
            conn.executemany(
                "INSERT INTO pages VALUES (?, ?, ?, ?, ?, ?)",
                [
                    ("That's the Spirit Box", "https://example/wiki/Spirit", "Loot_Boxes", "A useful box.", "", "ok"),
                    (
                        "That's the Spirit! Box",
                        "https://example/wiki/Spirit_Duplicate",
                        "Loot_Boxes",
                        "duplicate page - please see That's the Spirit box.",
                        "",
                        "ok",
                    ),
                ],
            )
            conn.commit()

            entries = load_entries(db_path, min_definition_length=8)

        definitions = {entry.title: entry.definition for entry in entries}
        self.assertEqual(definitions["That's the Spirit! Box"], "A useful box.")

    def test_forwarding_target_from_definition_supports_maintenance_forms(self) -> None:
        self.assertEqual(
            forwarding_target_from_definition("duplicate page - please see That's the Spirit box."),
            "That's the Spirit box",
        )
        self.assertEqual(
            forwarding_target_from_definition("System Message: For the Princess Donut Fan Club, please see Princess Posse Fan Club"),
            "Princess Posse Fan Club",
        )

    def test_load_entries_cleans_stale_source_artifacts(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "characters.sqlite"
            conn = sqlite3.connect(db_path)
            conn.execute(
                """
                CREATE TABLE pages (
                    title TEXT,
                    url TEXT,
                    source_category TEXT,
                    first_paragraph TEXT,
                    raw_html TEXT,
                    status TEXT
                )
                """
            )
            conn.executemany(
                "INSERT INTO pages VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (
                        "Annie",
                        "https://example/wiki/Annie",
                        "Characters",
                        "Annie is the child Katia tried to adopt. Art by Rebecca Dorr, ArtStation",
                        "",
                        "ok",
                    ),
                    (
                        "Leon",
                        "https://example/wiki/Leon",
                        "Characters",
                        "Leon isa Dirigible Gnome NPC from the Fifth Floor. For more information: Magic & Spells",
                        "",
                        "ok",
                    ),
                ],
            )
            conn.commit()

            entries = load_entries(db_path, min_definition_length=8)

        definitions = {entry.title: entry.definition for entry in entries}
        self.assertEqual(definitions["Annie"], "Annie is the child Katia tried to adopt.")
        self.assertEqual(definitions["Leon"], "Leon is a Dirigible Gnome NPC from the Fifth Floor.")

    def test_load_entries_skips_low_quality_incomplete_definition_and_logs(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "characters.sqlite"
            conn = sqlite3.connect(db_path)
            conn.execute(
                """
                CREATE TABLE pages (
                    title TEXT,
                    url TEXT,
                    source_category TEXT,
                    first_paragraph TEXT,
                    raw_html TEXT,
                    status TEXT
                )
                """
            )
            conn.executemany(
                "INSERT INTO pages VALUES (?, ?, ?, ?, ?, ?)",
                [
                    ("Carl", "https://example/wiki/Carl", "Characters", "Carl is a crawler.", "", "ok"),
                    (
                        "Commander Stockade",
                        "https://example/wiki/Commander_Stockade",
                        "Characters",
                        "<b>Commander Stockade</b> is",
                        "<aside class=\"portable-infobox\"></aside>",
                        "ok",
                    ),
                ],
            )
            conn.commit()

            with self.assertLogs("fandom_dict.entries", level="INFO") as logs:
                entries = load_entries(db_path, min_definition_length=8)

            self.assertEqual([entry.title for entry in entries], ["Carl"])
            self.assertIn("skipped low-quality dictionary entry Commander Stockade", "\n".join(logs.output))

    def test_load_entries_keeps_incomplete_definition_when_sidebar_details_exist(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "characters.sqlite"
            conn = sqlite3.connect(db_path)
            conn.execute(
                """
                CREATE TABLE pages (
                    title TEXT,
                    url TEXT,
                    source_category TEXT,
                    first_paragraph TEXT,
                    raw_html TEXT,
                    status TEXT
                )
                """
            )
            conn.execute(
                "INSERT INTO pages VALUES (?, ?, ?, ?, ?, ?)",
                (
                    "Koki",
                    "https://example/wiki/Koki",
                    "Characters",
                    "<b>Koki</b> is",
                    """
                    <aside class="portable-infobox">
                      <div class="pi-data" data-source="origin">
                        <h3 class="pi-data-label">ORIGIN</h3>
                        <div class="pi-data-value">Japan</div>
                      </div>
                    </aside>
                    """,
                    "ok",
                ),
            )
            conn.commit()

            entries = load_entries(db_path, min_definition_length=8)

        self.assertEqual([entry.title for entry in entries], ["Koki"])
        self.assertEqual(entries[0].details, (("Origin", "Japan"),))

    def test_spoiler_notice_from_html_extracts_page_warning(self) -> None:
        self.assertEqual(
            spoiler_notice_from_html(
                '<big><div class="dcc-highlight"><b>This article contains unmarked spoilers for Book 6.</b></div></big>'
            ),
            "This article contains unmarked spoilers for Book 6.",
        )
        self.assertIsNone(spoiler_notice_from_html("<p>No warning here.</p>"))

    def test_sidebar_details_from_html_extracts_approved_sidebar_fields(self) -> None:
        raw_html = """
        <aside class="portable-infobox">
          <h2 class="pi-header" data-source="crawler_info">BIOGRAPHICAL INFO</h2>
          <div class="pi-data" data-source="aliases">
            <h3 class="pi-data-label">ALIASES</h3>
            <div class="pi-data-value">Morty, Uncle Morty</div>
          </div>
          <div class="pi-data" data-source="origin">
            <h3 class="pi-data-label">ORIGIN</h3>
            <div class="pi-data-value">Dungeon</div>
          </div>
          <div class="pi-data" data-source="species">
            <h3 class="pi-data-label">RACE</h3>
            <div class="pi-data-value">Skyfowl</div>
          </div>
          <div class="pi-data" data-source="first_appearance">
            <h3 class="pi-data-label">FIRST SCENE</h3>
            <div class="pi-data-value">Book 1, Chapter 18</div>
          </div>
          <div class="pi-data" data-source="occupation">
            <h3 class="pi-data-label">OCCUPATION</h3>
            <div class="pi-data-value">Trainer</div>
          </div>
        </aside>
        """

        self.assertEqual(
            sidebar_details_from_html(raw_html),
            (
                ("Aliases", "Morty, Uncle Morty"),
                ("Origin", "Dungeon"),
                ("Race", "Skyfowl"),
                ("First scene", "Book 1, Chapter 18"),
            ),
        )

    def test_sidebar_details_from_html_extracts_source_for_loot_box(self) -> None:
        raw_html = """
        <aside class="portable-infobox">
          <div class="pi-data" data-source="type">
            <h3 class="pi-data-label">TYPE</h3>
            <div class="pi-data-value">Loot Box</div>
          </div>
          <div class="pi-data" data-source="source">
            <h3 class="pi-data-label">SOURCE</h3>
            <div class="pi-data-value">Achievements</div>
          </div>
        </aside>
        """

        self.assertEqual(
            sidebar_details_from_html(raw_html),
            (("Source", "Achievements"),),
        )

    def test_sidebar_details_from_html_extracts_source_for_spell(self) -> None:
        raw_html = """
        <aside class="portable-infobox">
          <div class="pi-data" data-source="mana">
            <h3 class="pi-data-label">MANA</h3>
            <div class="pi-data-value">40</div>
          </div>
          <div class="pi-data" data-source="source">
            <h3 class="pi-data-label">SOURCE</h3>
            <div class="pi-data-value">Scroll, Loot Box</div>
          </div>
        </aside>
        """

        self.assertEqual(
            sidebar_details_from_html(raw_html),
            (("Source", "Scroll, Loot Box"),),
        )

    def test_sidebar_details_from_html_omits_source_when_missing(self) -> None:
        raw_html = """
        <aside class="portable-infobox">
          <h2 class="pi-header" data-source="crawler_info">BIOGRAPHICAL INFO</h2>
          <div class="pi-data" data-source="origin">
            <h3 class="pi-data-label">ORIGIN</h3>
            <div class="pi-data-value">Dungeon</div>
          </div>
        </aside>
        """

        self.assertEqual(
            sidebar_details_from_html(raw_html),
            (("Origin", "Dungeon"),),
        )

    def test_sidebar_details_from_html_uses_configurable_fields(self) -> None:
        raw_html = """
        <aside class="portable-infobox">
          <div class="pi-data" data-source="allegiance">
            <h3 class="pi-data-label">ALLEGIANCE</h3>
            <div class="pi-data-value">House Stark</div>
          </div>
          <div class="pi-data" data-source="culture">
            <h3 class="pi-data-label">CULTURE</h3>
            <div class="pi-data-value">Northmen</div>
          </div>
        </aside>
        """

        fields = (
            SidebarField("allegiance", "Allegiance"),
            SidebarField("culture", "Culture"),
        )

        self.assertEqual(
            sidebar_details_from_html(raw_html, fields),
            (("Allegiance", "House Stark"), ("Culture", "Northmen")),
        )

    def test_build_aliases_does_not_add_unrelated_alias_forms(self) -> None:
        entries = [
            Entry("Red Beret", "https://example/wiki/Red_Beret", "An item."),
            Entry("Reaper Spider Minion Patch", "https://example/wiki/Reaper", "A patch."),
            Entry("José Sanchez", "https://example/wiki/Jose", "A crawler."),
            Entry("Under_score", "https://example/wiki/Under_score", "A thing."),
        ]

        aliases = build_aliases(entries)

        self.assertEqual(aliases["Red Beret"], ["Red Beret"])
        self.assertEqual(aliases["Reaper Spider Minion Patch"], ["Reaper Spider Minion Patch"])
        self.assertEqual(aliases["José Sanchez"], ["José Sanchez"])
        self.assertEqual(aliases["Under_score"], ["Under_score"])

    def test_build_aliases_adds_title_rule_lookup_aliases(self) -> None:
        entries = [
            Entry("1914 Box", "https://example/wiki/1914_Box", "A box."),
            Entry("Fireball Spell", "https://example/wiki/Fireball_Spell", "A spell."),
            Entry("Goblin Box", "https://example/wiki/Goblin_Box", "A box."),
            Entry("Crybaby Achievement", "https://example/wiki/Crybaby_Achievement", "An achievement."),
            Entry("Mana Potion", "https://example/wiki/Mana_Potion", "A potion."),
            Entry("Potion of Bloodlust", "https://example/wiki/Potion_of_Bloodlust", "A potion."),
            Entry("Heal Scroll", "https://example/wiki/Heal_Scroll", "A scroll."),
            Entry("Scroll of Water Breathing", "https://example/wiki/Scroll_of_Water_Breathing", "A scroll."),
            Entry("Ring of Water Breathing", "https://example/wiki/Ring_of_Water_Breathing", "A ring."),
            Entry("Wand of Nighty Night", "https://example/wiki/Wand_of_Nighty_Night", "A wand."),
        ]

        aliases = build_aliases(entries)

        self.assertIn("1914", aliases["1914 Box"])
        self.assertIn("Fireball", aliases["Fireball Spell"])
        self.assertIn("Goblin", aliases["Goblin Box"])
        self.assertIn("Crybaby", aliases["Crybaby Achievement"])
        self.assertIn("Mana", aliases["Mana Potion"])
        self.assertIn("Bloodlust", aliases["Potion of Bloodlust"])
        self.assertIn("Heal", aliases["Heal Scroll"])
        self.assertIn("Nighty Night", aliases["Wand of Nighty Night"])

    def test_lookup_report_tracks_suffix_alias_when_title_already_exists(self) -> None:
        entries = [
            Entry("Fireball", "https://example/wiki/Fireball", "A thing."),
            Entry("Fireball Spell", "https://example/wiki/Fireball_Spell", "A spell."),
        ]

        aliases = build_aliases(entries)
        report = build_lookup_report(entries)

        self.assertNotIn("Fireball", aliases["Fireball Spell"])
        self.assertEqual(len(report.multi_target_lookups), 1)
        self.assertEqual(report.multi_target_lookups[0].word, "Fireball")
        self.assertEqual(report.multi_target_lookups[0].targets, ("Fireball", "Fireball Spell"))

    def test_lookup_report_tracks_title_rule_alias_collisions_as_multi_lookup(self) -> None:
        entries = [
            Entry("Heal Pet Potion", "https://example/wiki/Heal_Pet_Potion", "A potion."),
            Entry("Heal Pet Spell", "https://example/wiki/Heal_Pet_Spell", "A spell."),
        ]

        aliases = build_aliases(entries)
        report = build_lookup_report(entries)

        self.assertNotIn("Heal Pet", aliases["Heal Pet Potion"])
        self.assertNotIn("Heal Pet", aliases["Heal Pet Spell"])
        self.assertEqual(len(report.multi_target_lookups), 1)
        self.assertEqual(report.multi_target_lookups[0].word, "Heal Pet")
        self.assertEqual(report.multi_target_lookups[0].targets, ("Heal Pet Potion", "Heal Pet Spell"))

    def test_lookup_report_tracks_prefix_title_alias_collisions_as_multi_lookup(self) -> None:
        entries = [
            Entry("Scroll of Water Breathing", "https://example/wiki/Scroll_of_Water_Breathing", "A scroll."),
            Entry("Ring of Water Breathing", "https://example/wiki/Ring_of_Water_Breathing", "A ring."),
        ]

        aliases = build_aliases(entries)
        report = build_lookup_report(entries)

        self.assertNotIn("Water Breathing", aliases["Scroll of Water Breathing"])
        self.assertNotIn("Water Breathing", aliases["Ring of Water Breathing"])
        self.assertEqual(len(report.multi_target_lookups), 1)
        self.assertEqual(report.multi_target_lookups[0].word, "Water Breathing")
        self.assertEqual(
            report.multi_target_lookups[0].targets,
            ("Ring of Water Breathing", "Scroll of Water Breathing"),
        )

    def test_lookup_report_tracks_parenthetical_alias_collisions_as_multi_lookup(self) -> None:
        entries = [
            Entry("Baelon Targaryen (son of Aerys)", "https://example/wiki/Baelon_A", "One Baelon."),
            Entry("Baelon Targaryen (son of Viserys I)", "https://example/wiki/Baelon_V", "Another Baelon."),
        ]

        aliases = build_aliases(entries)
        report = build_lookup_report(entries)

        self.assertNotIn("Baelon Targaryen", aliases["Baelon Targaryen (son of Aerys)"])
        self.assertEqual(len(report.multi_target_lookups), 1)
        self.assertEqual(report.multi_target_lookups[0].word, "Baelon Targaryen")
        self.assertEqual(
            report.multi_target_lookups[0].targets,
            ("Baelon Targaryen (son of Aerys)", "Baelon Targaryen (son of Viserys I)"),
        )

    def test_lookup_report_uses_configured_house_prefix_only_when_enabled(self) -> None:
        entries = [
            Entry("House Stark", "https://example/wiki/House_Stark", "A noble house."),
            Entry("Battle of the Bells", "https://example/wiki/Battle_Bells", "A battle."),
        ]

        default_report = build_lookup_report(entries, title_prefix_aliases=())
        house_report = build_lookup_report(entries, title_prefix_aliases=("House ",))

        self.assertNotIn("Stark", default_report.aliases["House Stark"])
        self.assertIn("Stark", house_report.aliases["House Stark"])
        self.assertNotIn("the Bells", house_report.aliases["Battle of the Bells"])

    def test_lookup_report_uses_configured_sidebar_alias_label(self) -> None:
        entries = [
            Entry(
                "Ferdinand",
                "https://example/wiki/Ferdinand",
                "A cat.",
                details=(("Also known as", "Gravy Boat"),),
            )
        ]

        default_report = build_lookup_report(entries)
        custom_report = build_lookup_report(entries, sidebar_alias_labels=("Also known as",))

        self.assertNotIn("Gravy Boat", default_report.aliases["Ferdinand"])
        self.assertIn("Gravy Boat", custom_report.aliases["Ferdinand"])

    def test_character_first_name_alias_requires_characters_category(self) -> None:
        entries = [
            Entry("Katia Grim", "https://example/wiki/Katia_Grim", "A crawler.", source_categories=("Characters",)),
            Entry("Lucia Mar", "https://example/wiki/Lucia_Mar", "A crawler.", source_categories=("Groups",)),
        ]

        report = build_lookup_report(entries)

        self.assertIn("Katia", report.aliases["Katia Grim"])
        self.assertNotIn("Lucia", report.aliases["Lucia Mar"])

    def test_character_first_name_alias_skips_honorific_first_words(self) -> None:
        entries = [
            Entry("Princess Donut", "https://example/wiki/Princess_Donut", "A crawler.", source_categories=("Characters",)),
            Entry("Ser Addison", "https://example/wiki/Ser_Addison", "A knight.", source_categories=("Characters",)),
        ]

        report = build_lookup_report(entries)

        self.assertNotIn("Princess", report.aliases["Princess Donut"])
        self.assertNotIn("Ser", report.aliases["Ser Addison"])

    def test_character_first_name_alias_collisions_become_multi_target_lookup(self) -> None:
        entries = [
            Entry("Aegon Frey", "https://example/wiki/Aegon_Frey", "One Aegon.", source_categories=("Characters",)),
            Entry(
                "Aegon Targaryen",
                "https://example/wiki/Aegon_Targaryen",
                "Another Aegon.",
                source_categories=("Characters",),
            ),
        ]

        report = build_lookup_report(entries)

        self.assertNotIn("Aegon", report.aliases["Aegon Frey"])
        self.assertEqual(len(report.multi_target_lookups), 1)
        self.assertEqual(report.multi_target_lookups[0].word, "Aegon")
        self.assertEqual(report.multi_target_lookups[0].targets, ("Aegon Frey", "Aegon Targaryen"))

    def test_character_first_name_alias_canonical_collision_includes_canonical_first(self) -> None:
        entries = [
            Entry("Katia", "https://example/wiki/Katia", "The canonical Katia."),
            Entry("Katia Grim", "https://example/wiki/Katia_Grim", "Another Katia.", source_categories=("Characters",)),
        ]

        report = build_lookup_report(entries)

        self.assertNotIn("Katia", report.aliases["Katia Grim"])
        self.assertEqual(len(report.multi_target_lookups), 1)
        self.assertEqual(report.multi_target_lookups[0].word, "Katia")
        self.assertEqual(report.multi_target_lookups[0].targets, ("Katia", "Katia Grim"))

    def test_build_aliases_skips_case_insensitive_canonical_and_generated_collisions(self) -> None:
        entries = [
            Entry("Fireball", "https://example/wiki/Fireball", "A thing."),
            Entry("FIREBALL Spell", "https://example/wiki/Fireball_Spell", "A spell."),
            Entry("Fire Box", "https://example/wiki/Fire_Box", "A box."),
            Entry("Fire Spell", "https://example/wiki/Fire_Spell", "A spell."),
            Entry("Red Beret", "https://example/wiki/Red_Beret", "An item."),
            Entry("Reaper Spider Minion Patch", "https://example/wiki/Reaper_Patch", "A patch."),
        ]

        aliases = build_aliases(entries)

        self.assertNotIn("FIREBALL", aliases["FIREBALL Spell"])
        self.assertNotIn("Fire", aliases["Fire Box"])
        self.assertNotIn("Fire", aliases["Fire Spell"])
        self.assertEqual(aliases["Red Beret"], ["Red Beret"])
        self.assertEqual(aliases["Reaper Spider Minion Patch"], ["Reaper Spider Minion Patch"])

    def test_build_alias_report_adds_parenthetical_description_aliases(self) -> None:
        entries = [
            Entry(
                "Saccathian",
                "https://example/wiki/Saccathian",
                "<b>Saccathian</b> (or <b>Sacs</b>) are a common race.",
            ),
            Entry(
                "Borant Corporation",
                "https://example/wiki/Borant",
                "A Syndicate company, the <b>Borant Corporation</b> (aka <b>Borant</b>) is here.",
            ),
            Entry(
                "Ferdinand",
                "https://example/wiki/Ferdinand",
                '<b>Ferdinand</b> (actually named "Gravy Boat") is a cat.',
            ),
            Entry(
                "Katia Grim",
                "https://example/wiki/Katia",
                '<b>Katia Grimmsdóttir</b> (shortened to "Grim" in her crawler ID) is a crawler.',
                details=(("Race", "Human | Doppelgänger"),),
            ),
        ]

        report = build_alias_report(entries)

        self.assertIn("Sacs", report.aliases["Saccathian"])
        self.assertIn("Borant", report.aliases["Borant Corporation"])
        self.assertIn("Gravy Boat", report.aliases["Ferdinand"])
        self.assertIn("Grim", report.aliases["Katia Grim"])

    def test_build_alias_report_adds_bold_intro_variant_alias(self) -> None:
        entries = [
            Entry("Brain Boiler", "https://example/wiki/Brain_Boiler", "<b>Brain Boilers</b> are a mob."),
            Entry(
                "Suppurating Eye Spell",
                "https://example/wiki/Suppurating_Eye_Spell",
                "<b>Suppurating Eye Spell</b> is a spell.",
            ),
        ]

        report = build_alias_report(entries)

        self.assertIn("Brain Boilers", report.aliases["Brain Boiler"])
        self.assertIn("Suppurating Eye", report.aliases["Suppurating Eye Spell"])

    def test_build_alias_report_adds_leading_article_variants_from_discovered_aliases(self) -> None:
        entries = [
            Entry(
                "Valtay Corporation",
                "https://example/wiki/Valtay",
                "The <b>Valtay Corporation</b> is a massive company.",
                details=(("Aliases", "The Valtay, The Brain Worms"),),
            ),
        ]

        report = build_alias_report(entries)

        self.assertIn("The Valtay Corporation", report.aliases["Valtay Corporation"])
        self.assertIn("The Valtay", report.aliases["Valtay Corporation"])
        self.assertIn("Valtay", report.aliases["Valtay Corporation"])
        self.assertIn("Brain Worms", report.aliases["Valtay Corporation"])

    def test_build_alias_report_filters_sidebar_aliases(self) -> None:
        entries = [
            Entry(
                "Ferdinand",
                "https://example/wiki/Ferdinand",
                "A cat.",
                details=(("Aliases", 'Gravy Boat, Ferdinand, Circe Took (sponsor), The "kill, kill" lady[1]'),),
            )
        ]

        report = build_alias_report(entries)

        self.assertIn("Gravy Boat", report.aliases["Ferdinand"])
        reasons = {omission.reason for omission in report.omissions}
        self.assertIn("self-alias", reasons)
        self.assertIn("parenthetical-note", reasons)
        self.assertIn("quoted-noise", reasons)

    def test_build_alias_report_adds_conservative_human_name_aliases(self) -> None:
        entries = [
            Entry("Katia Grim", "https://example/wiki/Katia", "A crawler.", details=(("Race", "Human"),)),
            Entry("Carl", "https://example/wiki/Carl", "A crawler."),
            Entry("Carl Smith", "https://example/wiki/Carl_Smith", "A crawler.", details=(("Race", "Human"),)),
        ]

        report = build_lookup_report(entries)

        self.assertIn("Katia", report.aliases["Katia Grim"])
        self.assertIn("Grim", report.aliases["Katia Grim"])
        self.assertNotIn("Carl", report.aliases["Carl Smith"])
        self.assertIn("Smith", report.aliases["Carl Smith"])
        self.assertEqual(len(report.multi_target_lookups), 1)
        self.assertEqual(report.multi_target_lookups[0].word, "Carl")
        self.assertEqual(report.multi_target_lookups[0].targets, ("Carl", "Carl Smith"))
        self.assertNotIn("canonical-collision", {omission.reason for omission in report.omissions})

    def test_build_alias_report_omits_ambiguous_aliases(self) -> None:
        entries = [
            Entry("First Target", "https://example/wiki/First", "A thing.", details=(("Aliases", "Shared"),)),
            Entry("Second Target", "https://example/wiki/Second", "A thing.", details=(("Aliases", "Shared"),)),
        ]

        report = build_alias_report(entries)

        self.assertNotIn("Shared", report.aliases["First Target"])
        self.assertNotIn("Shared", report.aliases["Second Target"])
        self.assertIn("alias-collision", {omission.reason for omission in report.omissions})

    def test_write_xhtml_keeps_displayed_title_unchanged_when_suffix_alias_exists(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            output = Path(tmp_dir) / "dictionary.xhtml"
            entries = [
                Entry("Fireball Spell", "https://example/wiki/Fireball_Spell", "A spell."),
            ]

            write_xhtml(entries, output, "Test Dictionary")

            text = output.read_text(encoding="utf-8")
            self.assertIn('<idx:orth value="Fireball Spell"><b>Fireball Spell</b>', text)
            self.assertIn('<idx:iform value="Fireball" />', text)
            self.assertNotIn('value="Fireball"><b>Fireball Spell</b>', text)
            self.assertEqual(text.count('<idx:entry name="default"'), 1)
            self.assertEqual(text.count("<hr />"), 0)

    def test_write_xhtml_emits_character_first_name_alias_as_inflection(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            output = Path(tmp_dir) / "dictionary.xhtml"
            entries = [
                Entry("Katia Grim", "https://example/wiki/Katia_Grim", "A crawler.", source_categories=("Characters",)),
            ]

            write_xhtml(entries, output, "Test Dictionary")

            text = output.read_text(encoding="utf-8")
            self.assertIn('<idx:orth value="Katia Grim"><b>Katia Grim</b>', text)
            self.assertIn('<idx:iform value="Katia" />', text)
            self.assertEqual(text.count('<idx:entry name="default"'), 1)
            ET.parse(output)

    def test_write_xhtml_emits_duplicate_entry_for_multi_target_lookup(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            output = Path(tmp_dir) / "dictionary.xhtml"
            entries = [
                Entry("Fireball", "https://example/wiki/Fireball", "A standalone thing."),
                Entry("Fireball Spell", "https://example/wiki/Fireball_Spell", "A spell."),
            ]

            write_xhtml(entries, output, "Test Dictionary")

            text = output.read_text(encoding="utf-8")
            self.assertEqual(text.count('<idx:orth value="Fireball">'), 2)
            self.assertIn('<idx:orth value="Fireball"><b>Fireball</b>', text)
            self.assertIn('<idx:orth value="Fireball"><b>Fireball Spell</b>', text)
            self.assertNotIn('<idx:iform value="Fireball" />', text)
            self.assertIn('id="entry-1-lookup-1-1"', text)
            self.assertIn("<hr />", text)
            ET.parse(output)

    def test_write_xhtml_emits_duplicate_entry_for_title_rule_multi_lookup(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            output = Path(tmp_dir) / "dictionary.xhtml"
            entries = [
                Entry("Heal Pet Potion", "https://example/wiki/Heal_Pet_Potion", "A potion."),
                Entry("Heal Pet Spell", "https://example/wiki/Heal_Pet_Spell", "A spell."),
            ]

            write_xhtml(entries, output, "Test Dictionary")

            text = output.read_text(encoding="utf-8")
            self.assertEqual(text.count('<idx:orth value="Heal Pet">'), 2)
            self.assertIn('<idx:orth value="Heal Pet"><b>Heal Pet Potion</b>', text)
            self.assertIn('<idx:orth value="Heal Pet"><b>Heal Pet Spell</b>', text)
            self.assertNotIn('<idx:iform value="Heal Pet" />', text)
            ET.parse(output)

    def test_write_xhtml_emits_duplicate_entry_for_character_first_name_multi_lookup(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            output = Path(tmp_dir) / "dictionary.xhtml"
            entries = [
                Entry("Aegon Frey", "https://example/wiki/Aegon_Frey", "One Aegon.", source_categories=("Characters",)),
                Entry(
                    "Aegon Targaryen",
                    "https://example/wiki/Aegon_Targaryen",
                    "Another Aegon.",
                    source_categories=("Characters",),
                ),
            ]

            write_xhtml(entries, output, "Test Dictionary")

            text = output.read_text(encoding="utf-8")
            self.assertEqual(text.count('<idx:orth value="Aegon">'), 2)
            self.assertIn('<idx:orth value="Aegon"><b>Aegon Frey</b>', text)
            self.assertIn('<idx:orth value="Aegon"><b>Aegon Targaryen</b>', text)
            self.assertNotIn('<idx:iform value="Aegon" />', text)
            ET.parse(output)

    def test_write_xhtml_emits_automatic_alias_headwords(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            output = Path(tmp_dir) / "dictionary.xhtml"
            entries = [
                Entry(
                    "Saccathian",
                    "https://example/wiki/Saccathian",
                    "<b>Saccathian</b> (or <b>Sacs</b>) are a common race.",
                ),
                Entry(
                    "Borant Corporation",
                    "https://example/wiki/Borant",
                    "A Syndicate company, the <b>Borant Corporation</b> (aka <b>Borant</b>) is here.",
                ),
                Entry(
                    "Ferdinand",
                    "https://example/wiki/Ferdinand",
                    '<b>Ferdinand</b> (actually named "Gravy Boat") is a cat.',
                ),
                Entry(
                    "Valtay Corporation",
                    "https://example/wiki/Valtay",
                    "The <b>Valtay Corporation</b> is a massive company.",
                    details=(("Aliases", "The Valtay"),),
                ),
                Entry("Katia Grim", "https://example/wiki/Katia", "A crawler.", details=(("Race", "Human"),)),
                Entry("Brain Boiler", "https://example/wiki/Brain_Boiler", "<b>Brain Boilers</b> are a mob."),
            ]

            write_xhtml(entries, output, "Test Dictionary")

            text = output.read_text(encoding="utf-8")
            self.assertIn('<idx:iform value="Sacs" />', text)
            self.assertIn('<idx:iform value="Borant" />', text)
            self.assertIn('<idx:iform value="Gravy Boat" />', text)
            self.assertIn('<idx:iform value="Valtay" />', text)
            self.assertIn('<idx:iform value="The Valtay Corporation" />', text)
            self.assertIn('<idx:iform value="Katia" />', text)
            self.assertIn('<idx:iform value="Brain Boilers" />', text)
            self.assertNotIn('value="Sacs"><b>Saccathian</b>', text)

    def test_sanitize_inline_html_preserves_only_safe_emphasis(self) -> None:
        self.assertEqual(
            sanitize_inline_html('<strong>Carl</strong> & <em>Donut</em> <script>bad()</script>'),
            "<b>Carl</b> &amp; <i>Donut</i> bad()",
        )

    def test_link_definition_references_links_known_entry_names(self) -> None:
        title_to_id = {"Carl": 1, "Donut": 2, "Mordecai": 3}

        linked = link_definition_references(
            "Carl talks to <b>Donut</b> and Mordecai. Carl stays calm.",
            title_to_id,
            current_title="Mordecai",
        )

        self.assertEqual(
            linked,
            '<a href="#entry-1">Carl</a> talks to <b><a href="#entry-2">Donut</a></b> '
            "and Mordecai. Carl stays calm.",
        )

    def test_link_definition_references_skips_short_single_word_titles(self) -> None:
        title_to_id = {"Tin": 1, "Li Na": 2}

        linked = link_definition_references(
            "Tin appears near Li Na.",
            title_to_id,
            current_title="Carl",
        )

        self.assertEqual(linked, 'Tin appears near <a href="#entry-2">Li Na</a>.')

    def test_link_definition_references_escapes_unlinked_text(self) -> None:
        title_to_id = {"Blood": 1}

        linked = link_definition_references(
            "Fire & Blood mentions Blood.",
            title_to_id,
            current_title="Carl",
        )

        self.assertEqual(linked, 'Fire &amp; <a href="#entry-1">Blood</a> mentions Blood.')

    def test_write_xhtml_preserves_emphasis_escapes_text_and_produces_valid_xml(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            output = Path(tmp_dir) / "dictionary.xhtml"
            entries = [
                Entry(
                    "Carl & Donut",
                    "https://example/wiki/Carl?x=1&y=2",
                    '<b>Carl</b> says "hi" & <i>keeps crawling</i>.',
                )
            ]

            write_xhtml(entries, output, "Test Dictionary")

            text = output.read_text(encoding="utf-8")
            self.assertIn("Carl &amp; Donut", text)
            self.assertIn("<li><b>Carl</b> says \"hi\" &amp; <i>keeps crawling</i>.</li>", text)
            self.assertIn("<idx:entry", text)
            self.assertIn("<idx:short>", text)
            ET.parse(output)

    def test_write_xhtml_adds_entry_separators_and_alphabet_pagebreaks(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            output = Path(tmp_dir) / "dictionary.xhtml"
            entries = [
                Entry("1914 Box", "https://example/wiki/1914_Box", "A box."),
                Entry("Agatha", "https://example/wiki/Agatha", "A crawler."),
                Entry("Bomo", "https://example/wiki/Bomo", "A character."),
            ]

            write_xhtml(entries, output, "Test Dictionary")

            text = output.read_text(encoding="utf-8")
            self.assertIn("<mbp:pagebreak />", text)
            self.assertIn('<h1 class="letter-heading" id="letter-number">0-9</h1>', text)
            self.assertIn('<h1 class="letter-heading" id="letter-A">A</h1>', text)
            self.assertIn('<h1 class="letter-heading" id="letter-B">B</h1>', text)
            self.assertIn('<idx:iform value="1914" />', text)
            self.assertEqual(
                text.count("<hr />"),
                text.count('<idx:entry name="default"') - 1,
            )
            ET.parse(output)

    def test_write_xhtml_adds_spoiler_note_before_bulleted_definition(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            output = Path(tmp_dir) / "dictionary.xhtml"
            entries = [
                Entry(
                    "Agatha",
                    "https://example/wiki/Agatha",
                    "<b>Agatha</b> pushes a cart.",
                    spoiler_notice="This article contains unmarked spoilers for Book 6.",
                    details=(("Origin", "Earth: Wenatchee, WA"), ("Race", "Human")),
                )
            ]

            write_xhtml(entries, output, "Test Dictionary")

            text = output.read_text(encoding="utf-8")
            self.assertIn(
                '<p class="spoiler-note"><b>Spoiler note:</b> This article contains unmarked spoilers for Book 6.</p>',
                text,
            )
            self.assertIn('<ul class="definition">', text)
            self.assertIn("<li><b>Agatha</b> pushes a cart.</li>", text)
            self.assertIn("<li><b>Origin:</b> Earth: Wenatchee, WA</li>", text)
            self.assertIn("<li><b>Race:</b> Human</li>", text)
            ET.parse(output)

    def test_write_xhtml_does_not_add_internal_cross_links_by_default(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            output = Path(tmp_dir) / "dictionary.xhtml"
            entries = [
                Entry("Carl", "https://example/wiki/Carl", "Carl knows Donut."),
                Entry("Donut", "https://example/wiki/Donut", "Donut knows Carl."),
            ]

            write_xhtml(entries, output, "Test Dictionary")

            text = output.read_text(encoding="utf-8")
            self.assertIn("<li>Carl knows Donut.</li>", text)
            self.assertNotIn('href="#entry-2">Donut</a>', text)
            ET.parse(output)

    def test_write_xhtml_can_add_internal_cross_links(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            output = Path(tmp_dir) / "dictionary.xhtml"
            entries = [
                Entry("Carl", "https://example/wiki/Carl", "Carl knows Donut."),
                Entry("Donut", "https://example/wiki/Donut", "Donut knows Carl."),
            ]

            write_xhtml_with_options(entries, output, "Test Dictionary", link_entries=True)

            text = output.read_text(encoding="utf-8")
            self.assertIn('<li>Carl knows <a href="#entry-2">Donut</a>.</li>', text)
            self.assertIn('<li>Donut knows <a href="#entry-1">Carl</a>.</li>', text)
            ET.parse(output)

    def test_write_opf_contains_dictionary_metadata(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            output = Path(tmp_dir) / "dictionary.opf"

            write_opf(output, "Title", "Author", "dictionary.xhtml", "urn:test")

            text = output.read_text(encoding="utf-8")
            self.assertIn("<DictionaryInLanguage>en-us</DictionaryInLanguage>", text)
            self.assertIn('<dc:Identifier id="uid">urn:test</dc:Identifier>', text)
            self.assertIn("<DefaultLookupIndex>default</DefaultLookupIndex>", text)
            self.assertIn("generated from the fandom wiki page summaries", text)
            self.assertIn('xmlns:opf="http://www.idpf.org/2007/opf"', text)
            ET.parse(output)

    def test_kindle_identifier_uses_title_and_release_version(self) -> None:
        self.assertEqual(
            kindle_identifier("Dungeon Crawler Carl Dictionary", "v0.5.0"),
            "dcdict:Dungeon-Crawler-Carl-Dictionary:v0.5.0",
        )
        self.assertEqual(
            kindle_identifier("Dungeon Crawler Carl Dictionary"),
            "dcdict:Dungeon-Crawler-Carl-Dictionary:dev",
        )
        self.assertEqual(
            kindle_identifier("Dungeon: Crawler / Carl Dictionary!", "v1.0.0-beta.1+build.4"),
            "dcdict:Dungeon-Crawler-Carl-Dictionary:v1.0.0-beta.1-build.4",
        )

    def test_normalize_release_version_uses_release_semver_rules(self) -> None:
        self.assertEqual(normalize_release_version(None), "dev")
        self.assertEqual(normalize_release_version("0.5.0"), "v0.5.0")
        self.assertEqual(normalize_release_version("v1.0.0-rc.1+build.4"), "v1.0.0-rc.1+build.4")
        with self.assertRaises(SystemExit):
            normalize_release_version("release-1.0")

    def test_build_dictionary_sources_writes_and_validates_outputs(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            result = build_dictionary_sources(
                [Entry("Carl", "https://example/wiki/Carl", "Carl is a crawler.")],
                Path(tmp_dir),
                "Test Dictionary",
                "Test Author",
            )

            self.assertEqual(result.entry_count, 1)
            self.assertTrue(result.xhtml_path.exists())
            self.assertTrue(result.opf_path.exists())
            self.assertIn(
                "<dc:Identifier id=\"uid\">dcdict:Test-Dictionary:dev</dc:Identifier>",
                result.opf_path.read_text(encoding="utf-8"),
            )
            ET.parse(result.xhtml_path)
            ET.parse(result.opf_path)

    def test_build_dictionary_sources_uses_release_version_in_opf_identifier(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            result = build_dictionary_sources(
                [Entry("Carl", "https://example/wiki/Carl", "Carl is a crawler.")],
                Path(tmp_dir),
                "Test Dictionary",
                "Test Author",
                release_version="v0.5.0",
            )

            self.assertIn(
                "<dc:Identifier id=\"uid\">dcdict:Test-Dictionary:v0.5.0</dc:Identifier>",
                result.opf_path.read_text(encoding="utf-8"),
            )

    def test_cli_release_version_writes_release_identifier(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            db_path = root / "entries.sqlite"
            conn = sqlite3.connect(db_path)
            conn.execute(
                """
                CREATE TABLE pages (
                    title TEXT, url TEXT, first_paragraph TEXT,
                    raw_html TEXT, status TEXT
                )
                """
            )
            conn.execute(
                "INSERT INTO pages VALUES (?, ?, ?, '', 'ok')",
                ("Carl", "https://example/wiki/Carl", "Carl is a crawler."),
            )
            conn.commit()
            conn.close()

            with mock.patch("builtins.print"):
                code = build_kindle_main(
                    [
                        "--input",
                        str(db_path),
                        "--output-dir",
                        str(root / "build"),
                        "--title",
                        "Test Dictionary",
                        "--release-version",
                        "0.5.0",
                    ]
                )

            self.assertEqual(code, 0)
            text = (root / "build" / "Test-Dictionary.opf").read_text(encoding="utf-8")
            self.assertIn(
                "<dc:Identifier id=\"uid\">dcdict:Test-Dictionary:v0.5.0</dc:Identifier>",
                text,
            )

    def test_compile_with_kindlegen_accepts_warning_exit_when_mobi_exists(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            opf_path = tmp_path / "dictionary.opf"
            opf_path.write_text("<package />", encoding="utf-8")
            fake_kindlegen = tmp_path / "kindlegen"
            fake_kindlegen.write_text(
                textwrap.dedent(
                    f"""\
                    #!{sys.executable}
                    import sys
                    from pathlib import Path
                    assert "-dont_append_source" in sys.argv
                    print("Amazon kindlegen(MAC OSX) V2.9 build 0000")
                    print("Warning(test): expected warning")
                    Path(sys.argv[1]).with_suffix(".mobi").write_bytes(b"MOBI")
                    raise SystemExit(1)
                    """
                ),
                encoding="utf-8",
            )
            fake_kindlegen.chmod(fake_kindlegen.stat().st_mode | stat.S_IXUSR)

            with mock.patch.dict(os.environ, {"PATH": str(tmp_path)}):
                compilation = compile_with_kindlegen(opf_path, dont_append_source=True)

        self.assertIsNotNone(compilation)
        assert compilation is not None
        self.assertEqual(compilation.output_path, opf_path.with_suffix(".mobi"))
        self.assertEqual(compilation.returncode, 1)
        self.assertEqual(compilation.compiler_version, "2.9")
        self.assertEqual(compilation.warnings, ("Warning(test): expected warning",))

    def test_compile_with_kindlegen_returns_none_when_not_available(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            opf_path = Path(tmp_dir) / "dictionary.opf"
            opf_path.write_text("<package />", encoding="utf-8")
            with mock.patch("fandom_dict.formats.kindle.find_kindlegen", return_value=None):
                self.assertIsNone(compile_with_kindlegen(opf_path))


if __name__ == "__main__":
    unittest.main()
