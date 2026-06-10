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

from dcdict.build_kindle_dictionary import (
    Entry,
    biographical_details_from_html,
    build_aliases,
    build_dictionary_sources,
    compile_with_kindlegen,
    link_definition_references,
    load_entries,
    sanitize_inline_html,
    spoiler_notice_from_html,
    write_opf,
    write_xhtml,
    write_xhtml_with_options,
)


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

    def test_spoiler_notice_from_html_extracts_page_warning(self) -> None:
        self.assertEqual(
            spoiler_notice_from_html(
                '<big><div class="dcc-highlight"><b>This article contains unmarked spoilers for Book 6.</b></div></big>'
            ),
            "This article contains unmarked spoilers for Book 6.",
        )
        self.assertIsNone(spoiler_notice_from_html("<p>No warning here.</p>"))

    def test_biographical_details_from_html_extracts_approved_sidebar_fields(self) -> None:
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
            biographical_details_from_html(raw_html),
            (
                ("Aliases", "Morty, Uncle Morty"),
                ("Origin", "Dungeon"),
                ("Race", "Skyfowl"),
                ("First scene", "Book 1, Chapter 18"),
            ),
        )

    def test_build_aliases_adds_unambiguous_first_names_and_ascii_forms(self) -> None:
        entries = [
            Entry("Li Jun", "https://example/wiki/Li_Jun", "A crawler."),
            Entry("Li Na", "https://example/wiki/Li_Na", "A crawler."),
            Entry("Mordecai", "https://example/wiki/Mordecai", "A guide."),
            Entry("José Sanchez", "https://example/wiki/Jose", "A crawler."),
        ]

        aliases = build_aliases(entries)

        self.assertIn("Mordecai", aliases["Mordecai"])
        self.assertIn("Jose Sanchez", aliases["José Sanchez"])
        self.assertIn("José", aliases["José Sanchez"])
        self.assertNotIn("Li", aliases["Li Jun"])
        self.assertNotIn("Li", aliases["Li Na"])

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
            self.assertIn("<ul>", text)
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
            self.assertIn("<DefaultLookupIndex>default</DefaultLookupIndex>", text)
            ET.parse(output)

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
            ET.parse(result.xhtml_path)
            ET.parse(result.opf_path)

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
                    from pathlib import Path
                    Path("dictionary.mobi").write_bytes(b"MOBI")
                    raise SystemExit(1)
                    """
                ),
                encoding="utf-8",
            )
            fake_kindlegen.chmod(fake_kindlegen.stat().st_mode | stat.S_IXUSR)

            with mock.patch.dict(os.environ, {"PATH": str(tmp_path)}):
                mobi_path = compile_with_kindlegen(opf_path)

        self.assertEqual(mobi_path, opf_path.with_suffix(".mobi"))

    def test_compile_with_kindlegen_returns_none_when_not_available(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            opf_path = Path(tmp_dir) / "dictionary.opf"
            opf_path.write_text("<package />", encoding="utf-8")
            with mock.patch("dcdict.build_kindle_dictionary.find_kindlegen", return_value=None):
                self.assertIsNone(compile_with_kindlegen(opf_path))


if __name__ == "__main__":
    unittest.main()
