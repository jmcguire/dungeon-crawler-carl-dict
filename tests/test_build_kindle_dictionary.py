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
    build_aliases,
    compile_with_kindlegen,
    load_entries,
    write_opf,
    write_xhtml,
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
                    first_paragraph TEXT,
                    status TEXT
                )
                """
            )
            conn.executemany(
                "INSERT INTO pages VALUES (?, ?, ?, ?)",
                [
                    ("Donut", "https://example/wiki/Donut", " Princess\u00a0Donut   is royalty. ", "ok"),
                    ("Bad", "https://example/wiki/Bad", "Ignored", "error"),
                    ("Tiny", "https://example/wiki/Tiny", "short", "ok"),
                    ("Carl", "https://example/wiki/Carl", "Carl is a crawler.", "ok"),
                ],
            )
            conn.commit()

            entries = load_entries(db_path, min_definition_length=8)

        self.assertEqual([entry.title for entry in entries], ["Carl", "Donut"])
        self.assertEqual(entries[1].definition, "Princess Donut is royalty.")

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

    def test_write_xhtml_escapes_text_and_produces_valid_xml(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            output = Path(tmp_dir) / "dictionary.xhtml"
            entries = [
                Entry(
                    "Carl & Donut",
                    "https://example/wiki/Carl?x=1&y=2",
                    'Carl says "hi" & keeps crawling.',
                )
            ]

            write_xhtml(entries, output, "Test Dictionary")

            text = output.read_text(encoding="utf-8")
            self.assertIn("Carl &amp; Donut", text)
            self.assertIn("Carl says \"hi\" &amp; keeps crawling.", text)
            self.assertIn("<idx:entry", text)
            ET.parse(output)

    def test_write_opf_contains_dictionary_metadata(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            output = Path(tmp_dir) / "dictionary.opf"

            write_opf(output, "Title", "Author", "dictionary.xhtml", "urn:test")

            text = output.read_text(encoding="utf-8")
            self.assertIn("<DictionaryInLanguage>en-us</DictionaryInLanguage>", text)
            self.assertIn("<DefaultLookupIndex>default</DefaultLookupIndex>", text)
            ET.parse(output)

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

