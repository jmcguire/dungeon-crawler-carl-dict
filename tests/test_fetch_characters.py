import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from dcdict.fetch_characters import (
    PageRef,
    first_paragraph_from_html,
    init_db,
    reextract_first_paragraphs,
    summary_from_html,
    summary_from_infobox,
    upsert_page,
    wiki_page_url,
)


class FetchCharacterExtractionTests(unittest.TestCase):
    def test_first_paragraph_skips_fandom_chrome_and_quote(self) -> None:
        html = """
        <div class="mw-parser-output">
          <big><div class="dcc-highlight"><b>This article contains spoilers.</b></div></big>
          <p><b>System Message. Posting Book 9 spoilers will result in "acceleration".</b></p>
          <aside class="portable-infobox"><img src="cover.webp"><div>Noise</div></aside>
          <blockquote class="pull-quote"><p>A dramatic quote.</p></blockquote>
          <p><b>Agatha</b> appears to be a homeless human woman pushing a cart.</p>
        </div>
        """

        self.assertEqual(
            first_paragraph_from_html(html),
            "<b>Agatha</b> appears to be a homeless human woman pushing a cart.",
        )

    def test_summary_uses_loose_text_and_preserves_inline_emphasis(self) -> None:
        html = """
        <div class="mw-parser-output">
          <aside class="portable-infobox"></aside>
          Not much is known about <strong>Chirag Ali</strong>.
          They appear once on the <em>Leaderboard</em>.
          <table class="nav"><tr><td>Navigation</td></tr></table>
        </div>
        """

        self.assertEqual(
            summary_from_html("Chirag Ali", html),
            "Not much is known about <b>Chirag Ali</b>. They appear once on the <i>Leaderboard</i>.",
        )

    def test_summary_falls_back_to_infobox_fields(self) -> None:
        html = """
        <aside class="portable-infobox">
          <div class="pi-item pi-data" data-source="species">
            <h3>RACE</h3><div class="pi-data-value pi-font">Sai</div>
          </div>
          <div class="pi-item pi-data" data-source="occupation">
            <h3>OCCUPATION</h3><div class="pi-data-value pi-font">Firefighter</div>
          </div>
          <div class="pi-item pi-data" data-source="first_appearance">
            <h3>FIRST SCENE</h3><div class="pi-data-value pi-font">Chapter 32, Book 6</div>
          </div>
        </aside>
        """

        self.assertEqual(
            summary_from_infobox("Walter", html),
            "Walter: race/species: Sai; occupation: Firefighter; first scene: Chapter 32, Book 6.",
        )

    def test_reextract_updates_existing_rows_without_network(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "characters.sqlite"
            conn = init_db(db_path)
            page = PageRef(pageid=1, title="Carl", ns=0)
            upsert_page(
                conn,
                page,
                "https://example.fandom.com/wiki/Carl",
                "ok",
                raw_html="<p><b>Carl</b> is a <i>crawler</i>.</p>",
                first_paragraph="old text",
            )

            self.assertEqual(reextract_first_paragraphs(conn), 1)
            row = conn.execute("SELECT first_paragraph FROM pages WHERE pageid = 1").fetchone()
            self.assertEqual(row[0], "<b>Carl</b> is a <i>crawler</i>.")

    def test_wiki_page_url_uses_api_origin_and_encoded_title(self) -> None:
        self.assertEqual(
            wiki_page_url("https://example.fandom.com/api.php", "Popov Brothers (Maxim & Dmitri)"),
            "https://example.fandom.com/wiki/Popov_Brothers_%28Maxim_%26_Dmitri%29",
        )


if __name__ == "__main__":
    unittest.main()
