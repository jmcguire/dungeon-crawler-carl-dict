import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from dcdict.extraction import (
    ai_description_paragraph_from_html,
    expand_small_description,
    extract_summary_status,
    first_paragraph_from_html,
    is_generic_small_description,
    is_small_description,
    is_stub_like_description,
    is_truncated_description,
    summary_blocks_from_html,
    summary_from_html,
    summary_from_infobox,
)
from dcdict.fetch_entries import (
    CrawlConfig,
    DEFAULT_CATEGORIES,
    init_db,
    load_category_members,
    parse_args,
    reextract_first_paragraphs,
    upsert_page,
)
from dcdict.mediawiki import (
    PageRef,
    fandom_api_url,
    wiki_category_title,
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

    def test_first_paragraph_skips_stub_notice_and_uses_real_description(self) -> None:
        html = """
        <div class="mw-parser-output">
          <div style="overflow:hidden; margin:auto;">This article or section is a <b>stub</b>. You can help by expanding it.</div>
          <p>Veeka is a hunter.</p>
        </div>
        """

        self.assertEqual(first_paragraph_from_html(html), "Veeka is a hunter.")

    def test_first_paragraph_skips_pre_maintenance_message(self) -> None:
        html = """
        <div class="mw-parser-output">
          <pre>System Message: For the Princess Donut Fan Club, please see Princess Posse Fan Club</pre>
          <aside class="portable-infobox"></aside>
          <p><b>The Princess Posse</b> is Team #3 in the Ninth Floor Faction Wars.</p>
        </div>
        """

        self.assertEqual(
            first_paragraph_from_html(html),
            "<b>The Princess Posse</b> is Team #3 in the Ninth Floor Faction Wars.",
        )

    def test_summary_skips_gallery_caption_pages(self) -> None:
        html = """
        <div class="mw-parser-output">
          <p>Collection of Fan Art for the <a href="/wiki/System_AI">System AI</a></p>
          <h2><span class="mw-headline" id="Gallery">Gallery</span></h2>
          <ul class="gallery mw-gallery-traditional">
            <li class="gallerybox"><div class="gallerytext">Art by u/Mashermello, Reddit</div></li>
          </ul>
        </div>
        """

        self.assertEqual(summary_from_html("Fan Art for System AI", html), "")

    def test_is_stub_like_description_detects_broken_intro(self) -> None:
        self.assertTrue(is_stub_like_description("Dwight", "<b>Dwight</b> is"))
        self.assertTrue(is_stub_like_description("Chiyome", "<b>Chiyome</b> was."))
        self.assertFalse(is_stub_like_description("Carl", "<b>Carl</b> is a crawler."))

    def test_ai_description_paragraph_extracts_first_real_paragraph(self) -> None:
        html = """
        <div class="mw-parser-output">
          <h2><span class="mw-headline" id="AI_Description">AI Description</span></h2>
          <blockquote>
            <p class="mw-empty-elt"></p>
            <p><b>Dwight. Sparkling Unicorn.</b><br />A real paragraph.</p>
          </blockquote>
          <h2><span class="mw-headline" id="Appearance">Appearance</span></h2>
        </div>
        """

        self.assertEqual(
            ai_description_paragraph_from_html(html),
            "<b>Dwight. Sparkling Unicorn.</b> A real paragraph.",
        )

    def test_ai_description_paragraph_skips_statline_only_paragraph(self) -> None:
        html = """
        <div class="mw-parser-output">
          <h2><span class="mw-headline" id="AI_Description">AI Description</span></h2>
          <blockquote>
            <p><b>Chiyome. Razor Fox. Level 80 Mistress of Nunchaku.</b></p>
            <p>One of three from team The Wild Hunt.</p>
          </blockquote>
        </div>
        """

        self.assertEqual(
            ai_description_paragraph_from_html(html),
            "One of three from team The Wild Hunt.",
        )

    def test_ai_description_paragraph_skips_spell_statlines(self) -> None:
        html = """
        <div class="mw-parser-output">
          <h2><span class="mw-headline" id="AI_Description">AI Description</span></h2>
          <blockquote>
            <p><b>Ping</b></p>
            <p><b>Cost</b>: 5 Mana</p>
            <p>Also known as, "Here piggy, piggy," Ping is a hunting tool.</p>
          </blockquote>
        </div>
        """

        self.assertEqual(
            ai_description_paragraph_from_html(html),
            'Also known as, "Here piggy, piggy," Ping is a hunting tool.',
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

    def test_is_small_description_uses_under_100_character_threshold(self) -> None:
        self.assertTrue(is_small_description("A group of crawlers."))
        self.assertFalse(
            is_small_description(
                "This is a deliberately longer sentence that clearly crosses the small-description threshold and keeps going long enough to remove any doubt."
            )
        )

    def test_is_truncated_description_detects_trailing_conjunction(self) -> None:
        self.assertTrue(is_truncated_description("They worship the goddess Apito, and"))
        self.assertFalse(is_truncated_description("They worship the goddess Apito."))

    def test_is_generic_small_description_detects_tiny_spell_intro(self) -> None:
        self.assertTrue(is_generic_small_description("Ping Spell", "<b>Ping Spell</b> is a spell"))
        self.assertFalse(is_generic_small_description("Ping Spell", "<b>Ping Spell</b> is a useful targeting spell."))

    def test_summary_blocks_include_next_paragraph_before_table(self) -> None:
        html = """
        <div class="mw-parser-output">
          <p>A group of crawlers.</p>
          <h2><span class="mw-headline" id="Description">Description</span></h2>
          <p>They are named after Polish special forces despite none of them being from Poland.</p>
          <table class="nav"><tr><td>Navigation</td></tr></table>
        </div>
        """

        self.assertEqual(
            summary_blocks_from_html(html),
            [
                "A group of crawlers.",
                "They are named after Polish special forces despite none of them being from Poland.",
            ],
        )

    def test_summary_expands_small_description_with_next_block(self) -> None:
        html = """
        <div class="mw-parser-output">
          <p><b>Bear Witness Spell</b> is a spell.</p>
          <h2><span class="mw-headline" id="Description">Description</span></h2>
          <p>Spell can be negated by a high enough Mind Balance Skill.</p>
          <table class="nav"><tr><td>Navigation</td></tr></table>
        </div>
        """

        self.assertEqual(
            summary_from_html("Bear Witness Spell", html),
            "<b>Bear Witness Spell</b> is a spell. Spell can be negated by a high enough Mind Balance Skill.",
        )

    def test_summary_expands_truncated_description_with_description_block(self) -> None:
        html = """
        <div class="mw-parser-output">
          <p><b>The 201st Security Group</b> is a cult of City Elves. They worship Apito, and</p>
          <h2><span class="mw-headline" id="Description">Description</span></h2>
          <p>They believe they must protect Skyfowl from flightless creatures.</p>
        </div>
        """

        self.assertEqual(
            summary_from_html("201st Security Group Militia", html),
            "<b>The 201st Security Group</b> is a cult of City Elves. They worship Apito, and they believe they must protect Skyfowl from flightless creatures.",
        )

    def test_summary_replaces_truncated_intro_with_ai_description(self) -> None:
        html = """
        <div class="mw-parser-output">
          <p>The <b>Flex in the City Achievement</b> is an achievement awarded to</p>
          <h2><span class="mw-headline" id="AI_Description">AI Description</span></h2>
          <blockquote>
            <p><b>New Achievement! Flex in the City!</b></p>
            <p>You killed a city boss with the participation of five or less crawlers. That is some serious badassery right there.</p>
            <p>Reward: You already got a boss box.</p>
          </blockquote>
        </div>
        """

        self.assertEqual(
            summary_from_html("Flex in the City Achievement", html),
            "You killed a city boss with the participation of five or less crawlers. That is some serious badassery right there.",
        )

    def test_summary_does_not_expand_from_story_section(self) -> None:
        html = """
        <div class="mw-parser-output">
          <p>The <b>Sapper's Box</b> is a Loot Box that gives various trap supplies.</p>
          <h2><span class="mw-headline" id="Story">Story</span></h2>
          <p><b>Gold Mechanic's Box</b><br />For an achievement.<br /><i>Loot:</i> Carl gets supplies.</p>
        </div>
        """

        self.assertEqual(
            summary_from_html("Sapper's Box", html),
            "The <b>Sapper's Box</b> is a Loot Box that gives various trap supplies.",
        )

    def test_summary_leaves_normal_length_description_unchanged(self) -> None:
        html = """
        <div class="mw-parser-output">
          <p><b>Adventurer Boxes</b> are common Loot Boxes containing standard adventuring gear. Bronze and Silver Adventurer Boxes are liberally distributed on the first two floors, and typically contain potions and bandages.</p>
          <p>Beginning on the Third or Fourth Floor, they also include coins.</p>
        </div>
        """

        self.assertEqual(
            summary_from_html("Adventurer Box", html),
            "<b>Adventurer Boxes</b> are common Loot Boxes containing standard adventuring gear. Bronze and Silver Adventurer Boxes are liberally distributed on the first two floors, and typically contain potions and bandages.",
        )

    def test_expand_small_description_requires_another_block(self) -> None:
        self.assertEqual(expand_small_description("A group of crawlers.", ["A group of crawlers."]), "A group of crawlers.")

    def test_summary_uses_ai_description_when_stub_like_intro_exists(self) -> None:
        html = """
        <div class="mw-parser-output">
          <aside class="portable-infobox"></aside>
          <p><b>Dwight</b> is</p>
          <h2><span class="mw-headline" id="AI_Description">AI Description</span></h2>
          <blockquote>
            <p><b>Dwight. Sparkling Unicorn.</b><br />He is one of Team Sparkles.[1]</p>
          </blockquote>
          <h2><span class="mw-headline" id="References">References</span></h2>
        </div>
        """

        self.assertEqual(
            summary_from_html("Dwight", html),
            "<b>Dwight. Sparkling Unicorn.</b> He is one of Team Sparkles.",
        )

    def test_summary_uses_ai_description_when_intro_is_generic_and_tiny(self) -> None:
        html = """
        <div class="mw-parser-output">
          <p><b>Ping Spell</b> is a spell</p>
          <h2><span class="mw-headline" id="AI_Description">AI Description</span></h2>
          <blockquote>
            <p><b>Ping</b></p>
            <p><b>Cost</b>: 5 Mana</p>
            <p>Also known as, "Here piggy, piggy," Ping is a hunting tool.</p>
            <p>A later paragraph should stay out of the dictionary summary.</p>
          </blockquote>
        </div>
        """

        self.assertEqual(
            summary_from_html("Ping Spell", html),
            'Also known as, "Here piggy, piggy," Ping is a hunting tool.',
        )

    def test_summary_leaves_normal_description_unchanged_even_with_ai_section(self) -> None:
        html = """
        <div class="mw-parser-output">
          <p><b>Carl</b> is a crawler.</p>
          <h2><span class="mw-headline" id="AI_Description">AI Description</span></h2>
          <blockquote>
            <p><b>Carl. Human.</b> Backup text.</p>
          </blockquote>
        </div>
        """

        self.assertEqual(
            summary_from_html("Carl", html),
            "<b>Carl</b> is a crawler.",
        )

    def test_summary_strips_numeric_wiki_reference_markers(self) -> None:
        html = """
        <div class="mw-parser-output">
          <p>Cascadia was founded in 2012.[1] Some text [2] more text. Not [abc] this.</p>
        </div>
        """

        self.assertEqual(
            summary_from_html("Cascadia", html),
            "Cascadia was founded in 2012. Some text more text. Not [abc] this.",
        )

    def test_summary_cleans_source_artifacts(self) -> None:
        html = """
        <div class="mw-parser-output">
          <p><b>Leon</b> isa Dirigible Gnome NPC from the Fifth Floor. For more information: Magic &amp; Spells</p>
        </div>
        """

        self.assertEqual(
            summary_from_html("Leon", html),
            "<b>Leon</b> is a Dirigible Gnome NPC from the Fifth Floor.",
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

    def test_summary_skips_stub_and_candidate_for_deletion_without_fallback(self) -> None:
        html = """
        <div class="mw-parser-output">
          <div>This article or section is a <b>stub</b>. You can help by expanding it.</div>
          <div>This article or section is a candidate for deletion.</div>
        </div>
        """

        self.assertEqual(summary_from_html("Ronaldo Qu", html), "")
        self.assertEqual(extract_summary_status("Ronaldo Qu", html), ("empty", ""))

    def test_summary_falls_back_to_infobox_when_stub_is_only_body_text(self) -> None:
        html = """
        <div class="mw-parser-output">
          <div>This article or section is a <b>stub</b>. You can help by expanding it.</div>
          <aside class="portable-infobox">
            <div class="pi-item pi-data" data-source="origin">
              <h3>ORIGIN</h3><div class="pi-data-value pi-font">Japan</div>
            </div>
            <div class="pi-item pi-data" data-source="race">
              <h3>RACE</h3><div class="pi-data-value pi-font">Human</div>
            </div>
            <div class="pi-item pi-data" data-source="first_appearance">
              <h3>FIRST SCENE</h3><div class="pi-data-value pi-font">Chapter 14, Book 3</div>
            </div>
          </aside>
        </div>
        """

        self.assertEqual(
            summary_from_html("Koki", html),
            "Koki: origin: Japan; race/species: Human; first scene: Chapter 14, Book 3.",
        )
        self.assertEqual(
            extract_summary_status("Koki", html),
            ("ok", "Koki: origin: Japan; race/species: Human; first scene: Chapter 14, Book 3."),
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
                "Characters",
                "ok",
                raw_html="<p><b>Carl</b> is a <i>crawler</i>.</p>",
                first_paragraph="old text",
            )

            self.assertEqual(reextract_first_paragraphs(conn), 1)
            row = conn.execute("SELECT first_paragraph FROM pages WHERE pageid = 1").fetchone()
            self.assertEqual(row[0], "<b>Carl</b> is a <i>crawler</i>.")

    def test_reextract_marks_stub_only_entries_as_empty(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "characters.sqlite"
            conn = init_db(db_path)
            page = PageRef(pageid=2, title="Ronaldo Qu", ns=0)
            upsert_page(
                conn,
                page,
                "https://example.fandom.com/wiki/Ronaldo_Qu",
                "Characters",
                "ok",
                raw_html=(
                    "<div>This article or section is a <b>stub</b>. You can help by expanding it.</div>"
                    "<div>This article or section is a candidate for deletion.</div>"
                ),
                first_paragraph="old text",
            )

            self.assertEqual(reextract_first_paragraphs(conn), 1)
            row = conn.execute("SELECT status, first_paragraph FROM pages WHERE pageid = 2").fetchone()
            self.assertEqual(row, ("empty", ""))

    def test_init_db_adds_source_category_to_older_databases(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "characters.sqlite"
            conn = sqlite3.connect(db_path)
            conn.execute(
                """
                CREATE TABLE pages (
                    pageid INTEGER PRIMARY KEY,
                    title TEXT NOT NULL UNIQUE,
                    ns INTEGER NOT NULL,
                    url TEXT NOT NULL,
                    raw_json TEXT,
                    raw_html TEXT,
                    first_paragraph TEXT,
                    status TEXT NOT NULL,
                    error TEXT,
                    fetched_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.commit()
            conn.close()

            upgraded = init_db(db_path)
            columns = [row[1] for row in upgraded.execute("PRAGMA table_info(pages)").fetchall()]

        self.assertIn("source_category", columns)

    def test_load_category_members_deduplicates_pages_and_tracks_categories(self) -> None:
        class StubClient:
            def __init__(self) -> None:
                self.calls = []

            def category_title(self, category):
                return wiki_category_title(category)

            def category_members(self, category, batch_size, max_pages, delay):
                self.calls.append((category, batch_size, max_pages, delay))
                if category == "Characters":
                    return [
                        PageRef(pageid=1, title="Carl", ns=0),
                        PageRef(pageid=2, title="Donut", ns=0),
                    ]
                return [
                    PageRef(pageid=2, title="Donut", ns=0),
                    PageRef(pageid=3, title="Mordecai", ns=0),
                ]

        client = StubClient()
        config = CrawlConfig(categories=("Characters", "Groups"), delay=0.0, max_pages=0, category_batch_size=50, refresh=False)

        targets = load_category_members(client, config)

        self.assertEqual([target.pageid for target in targets], [1, 2, 3])
        self.assertEqual(targets[1].source_categories, ("Characters", "Groups"))
        self.assertEqual(targets[2].source_categories, ("Groups",))

    def test_load_category_members_skips_failed_category_and_continues(self) -> None:
        class StubClient:
            def category_title(self, category):
                return wiki_category_title(category)

            def category_members(self, category, batch_size, max_pages, delay):
                if category == "Characters":
                    raise RuntimeError("boom")
                return [PageRef(pageid=2, title="Donut", ns=0)]

        client = StubClient()
        config = CrawlConfig(categories=("Characters", "Groups"), delay=0.0, max_pages=0, category_batch_size=50, refresh=False)

        targets = load_category_members(client, config)

        self.assertEqual([target.pageid for target in targets], [2])
        self.assertEqual(targets[0].source_categories, ("Groups",))

    def test_load_category_members_skips_duplicate_categories(self) -> None:
        class StubClient:
            def __init__(self) -> None:
                self.calls = []

            def category_title(self, category):
                return wiki_category_title(category)

            def category_members(self, category, batch_size, max_pages, delay):
                self.calls.append(category)
                return [PageRef(pageid=1, title="Carl", ns=0)]

        client = StubClient()
        config = CrawlConfig(
            categories=("Characters", "Characters", "Category:Characters"),
            delay=0.0,
            max_pages=0,
            category_batch_size=50,
            refresh=False,
        )

        targets = load_category_members(client, config)

        self.assertEqual([target.pageid for target in targets], [1])
        self.assertEqual(client.calls, ["Characters"])

    def test_load_category_members_uses_canonical_titles_for_seen_guard(self) -> None:
        class StubClient:
            def __init__(self) -> None:
                self.calls = []

            def category_title(self, category):
                return wiki_category_title(category)

            def category_members(self, category, batch_size, max_pages, delay):
                self.calls.append(category)
                if category == "Groups":
                    return [PageRef(pageid=2, title="Donut", ns=0)]
                return [PageRef(pageid=1, title="Carl", ns=0)]

        client = StubClient()
        config = CrawlConfig(
            categories=("Category:Characters", "Characters", "Groups"),
            delay=0.0,
            max_pages=0,
            category_batch_size=50,
            refresh=False,
        )

        targets = load_category_members(client, config)

        self.assertEqual([target.pageid for target in targets], [1, 2])
        self.assertEqual(client.calls, ["Category:Characters", "Groups"])

    def test_fandom_url_helpers_use_slug_and_canonical_category_title(self) -> None:
        self.assertEqual(fandom_api_url("dungeon-crawler-carl"), "https://dungeon-crawler-carl.fandom.com/api.php")
        self.assertEqual(wiki_category_title("Characters"), "Category:Characters")
        self.assertEqual(wiki_category_title("Category:Spells"), "Category:Spells")

    def test_parse_args_defaults_to_normal_dcc_categories(self) -> None:
        args = parse_args([])

        self.assertIsNone(args.categories)
        self.assertEqual(
            DEFAULT_CATEGORIES,
            ("Characters", "Groups", "Spells", "Achievements", "Races", "Items"),
        )

    def test_main_uses_normal_dcc_categories_when_none_are_passed(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "characters.sqlite"
            captured = {}

            class StubClient:
                def __init__(self, fandom, request_config) -> None:
                    captured["fandom"] = fandom
                    captured["request_config"] = request_config

            with mock.patch("dcdict.fetch_entries.MediaWikiClient", StubClient), mock.patch(
                "dcdict.fetch_entries.load_category_members", return_value=[]
            ) as load_members, mock.patch("dcdict.fetch_entries.crawl_pages"), mock.patch(
                "dcdict.fetch_entries.print_crawl_summary"
            ), mock.patch("dcdict.fetch_entries.assert_robots_allowed"):
                from dcdict.fetch_entries import main

                self.assertEqual(main(["--output", str(db_path)]), 0)

            crawl_config = load_members.call_args.args[1]
            self.assertEqual(crawl_config.categories, DEFAULT_CATEGORIES)

    def test_fetch_characters_wrapper_reexports_main(self) -> None:
        from dcdict import fetch_characters
        from dcdict import fetch_entries

        self.assertIs(fetch_characters.main, fetch_entries.main)

    def test_wiki_page_url_uses_api_origin_and_encoded_title(self) -> None:
        self.assertEqual(
            wiki_page_url("example-fandom", "Popov Brothers (Maxim & Dmitri)"),
            "https://example-fandom.fandom.com/wiki/Popov_Brothers_%28Maxim_%26_Dmitri%29",
        )


if __name__ == "__main__":
    unittest.main()
