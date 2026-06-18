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

from dcdict.kindle import (
    Entry,
    build_alias_report,
    build_aliases,
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
from dcdict.build_kindle_dictionary import main as build_kindle_main
from dcdict.build_kindle_dictionary import normalize_release_version


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
        self.assertEqual(entries[1].definition, "<b>Princess Donut</b> is royalty.")
        self.assertEqual(entries[1].spoiler_notice, "This article contains unmarked spoilers for Book 2.")
        self.assertEqual(
            entries[1].details,
            (("Aliases", "GC, BWR, NW Princess Donut"), ("Origin", "Earth: Seattle, WA")),
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

            with self.assertLogs("dcdict.entries", level="INFO") as logs:
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

    def test_build_aliases_adds_suffix_stripped_lookup_aliases(self) -> None:
        entries = [
            Entry("1914 Box", "https://example/wiki/1914_Box", "A box."),
            Entry("Fireball Spell", "https://example/wiki/Fireball_Spell", "A spell."),
            Entry("Goblin Box", "https://example/wiki/Goblin_Box", "A box."),
        ]

        aliases = build_aliases(entries)

        self.assertIn("1914", aliases["1914 Box"])
        self.assertIn("Fireball", aliases["Fireball Spell"])
        self.assertIn("Goblin", aliases["Goblin Box"])

    def test_build_aliases_skips_suffix_stripped_alias_when_title_already_exists(self) -> None:
        entries = [
            Entry("Fireball", "https://example/wiki/Fireball", "A thing."),
            Entry("Fireball Spell", "https://example/wiki/Fireball_Spell", "A spell."),
        ]

        aliases = build_aliases(entries)

        self.assertNotIn("Fireball", aliases["Fireball Spell"])

    def test_build_aliases_skips_case_insensitive_canonical_and_generated_collisions(self) -> None:
        entries = [
            Entry("Fireball", "https://example/wiki/Fireball", "A thing."),
            Entry("FIREBALL Spell", "https://example/wiki/Fireball_Spell", "A spell."),
            Entry("Fire Box", "https://example/wiki/Fire_Box", "A box."),
            Entry("Fire Spell", "https://example/wiki/Fire_Spell", "A spell."),
        ]

        aliases = build_aliases(entries)

        self.assertNotIn("FIREBALL", aliases["FIREBALL Spell"])
        self.assertNotIn("Fire", aliases["Fire Box"])
        self.assertNotIn("Fire", aliases["Fire Spell"])

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

        report = build_alias_report(entries)

        self.assertIn("Katia", report.aliases["Katia Grim"])
        self.assertIn("Grim", report.aliases["Katia Grim"])
        self.assertNotIn("Carl", report.aliases["Carl Smith"])
        self.assertIn("Smith", report.aliases["Carl Smith"])
        self.assertIn("canonical-collision", {omission.reason for omission in report.omissions})

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
            self.assertIn('<b><idx:orth value="Fireball Spell">Fireball Spell</idx:orth></b>', text)
            self.assertIn('<b><idx:orth value="Fireball">Fireball Spell</idx:orth></b>', text)
            self.assertNotIn("idx:iform", text)
            self.assertNotIn("Fireball</idx:orth></b>", text)
            self.assertEqual(text.count('<idx:entry name="default"'), 2)
            canonical_end = text.index("</idx:entry>")
            alias_start = text.index('<idx:entry name="default"', canonical_end)
            self.assertIn("<hr />", text[canonical_end:alias_start])

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
            self.assertIn('<b><idx:orth value="Sacs">Saccathian</idx:orth></b>', text)
            self.assertIn('<b><idx:orth value="Borant">Borant Corporation</idx:orth></b>', text)
            self.assertIn('<b><idx:orth value="Gravy Boat">Ferdinand</idx:orth></b>', text)
            self.assertIn('<b><idx:orth value="Valtay">Valtay Corporation</idx:orth></b>', text)
            self.assertIn('<b><idx:orth value="The Valtay Corporation">Valtay Corporation</idx:orth></b>', text)
            self.assertIn('<b><idx:orth value="Katia">Katia Grim</idx:orth></b>', text)
            self.assertIn('<b><idx:orth value="Brain Boilers">Brain Boiler</idx:orth></b>', text)

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
            text = (root / "build" / "dictionary.opf").read_text(encoding="utf-8")
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
                    Path("dictionary.mobi").write_bytes(b"MOBI")
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
            with mock.patch("dcdict.kindle.find_kindlegen", return_value=None):
                self.assertIsNone(compile_with_kindlegen(opf_path))


if __name__ == "__main__":
    unittest.main()
