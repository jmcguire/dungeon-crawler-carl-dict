import contextlib
import io
import json
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from fandom_dict.cli.health_report import (
    build_health_report,
    main,
    parse_args,
    render_health_report,
)
from fandom_dict.cli.output import output_from_args
from fandom_dict.config import project_config_from_mapping
from fandom_dict.entries import Entry


def test_config(database_path: str = "data/test.sqlite") -> dict[str, object]:
    return {
        "fandom": "test",
        "title": "Test Dictionary",
        "author": "Test Author",
        "source_name": "Test Wiki",
        "categories": ["Characters", "Items", "Spells"],
        "database_path": database_path,
        "build_dir": "build/test",
        "sidebar_fields": [{"source": "aliases", "label": "Aliases", "alias": True}],
        "title_aliases": {
            "suffixes": [" Box", " Potion", " Spell"],
            "prefixes": [],
            "strip_parenthetical": True,
            "component_ignore_words": [],
        },
        "max_summary_length": 600,
        "smoke_headwords": ["Carl", "Earth", "Heal Pet", "Missing"],
        "kobo_output_name": "dicthtml-test.zip",
    }


class HealthReportTests(unittest.TestCase):
    def sample_entries(self) -> list[Entry]:
        return [
            Entry("Carl", "https://example/Carl", "Carl."),
            Entry("Earth", "https://example/Earth", "Earth is a planet."),
            Entry("Earth Box", "https://example/Earth_Box", "Earth Box is a reward."),
            Entry("Fireball Spell", "https://example/Fireball_Spell", "Fireball Spell burns things."),
            Entry("Heal Pet Potion", "https://example/Heal_Pet_Potion", "A potion that helps pets."),
            Entry("Heal Pet Spell", "https://example/Heal_Pet_Spell", "A spell that helps pets."),
        ]

    def test_build_health_report_counts_formats_and_expected_terms(self) -> None:
        config = project_config_from_mapping(test_config())
        report = build_health_report(self.sample_entries(), config)

        formats = {item.name: item for item in report.formats}
        self.assertEqual(formats["Kindle"].entry_count, 6)
        self.assertEqual(formats["Kindle"].lookup_record_count, 9)
        self.assertEqual(formats["StarDict"].entry_count, 6)
        self.assertEqual(formats["StarDict"].lookup_record_count, 7)
        self.assertEqual(formats["Kobo"].lookup_record_count, 7)
        self.assertEqual(report.lookup_report.single_target_alias_count, 1)
        self.assertEqual(report.lookup_report.multi_target_lookup_count, 2)

        statuses = {status.term: status for status in report.term_statuses}
        self.assertEqual(statuses["Carl"].kind, "canonical")
        self.assertEqual(statuses["Earth"].kind, "multi-target")
        self.assertEqual(statuses["Earth"].targets, ("Earth", "Earth Box"))
        self.assertEqual(statuses["Heal Pet"].kind, "multi-target")
        self.assertEqual(statuses["Missing"].kind, "missing")
        self.assertGreaterEqual(len(report.findings), 1)

    def test_build_health_report_flags_wiki_cleanup_candidates(self) -> None:
        config = project_config_from_mapping(test_config())
        entries = [
            Entry("Rats", "https://example/Rats", "‘’’Rats are"),
            Entry("Red Beret", "https://example/Red_Beret", "Red Beret is an item"),
            Entry("Krakaren", "https://example/Krakaren", "Krakaren is useful. Section of AI Description of Clone:"),
            Entry("Fumble Achievement", "https://example/Fumble", "The Fumble Achievement is an achievement awarded"),
            Entry("Hell-Kissed Potion", "https://example/Potion", "Hell-Kissed Potion"),
            Entry("Tom", "https://example/Tom", "Tom is a boss.", details=(("Race", "Human"),)),
            Entry(
                "Healthy",
                "https://example/Healthy",
                "Healthy has a clear, useful, above-the-fold summary with enough context for a reader.",
            ),
        ]

        report = build_health_report(entries, config)
        candidates = {candidate.title: candidate for candidate in report.wiki_cleanup_candidates}

        self.assertIn("malformed-wiki-markup", candidates["Rats"].reasons)
        self.assertIn("generic-type-definition", candidates["Red Beret"].reasons)
        self.assertIn("generated-section-label", candidates["Krakaren"].reasons)
        self.assertIn("truncated-looking-definition", candidates["Fumble Achievement"].reasons)
        self.assertIn("title-only-definition", candidates["Hell-Kissed Potion"].reasons)
        self.assertNotIn("Tom", candidates)
        self.assertNotIn("Healthy", candidates)
        self.assertLess(candidates["Rats"].severity, candidates["Red Beret"].severity)

    def test_render_health_report_includes_summary_warnings_and_full_detail(self) -> None:
        config = project_config_from_mapping(test_config())
        report = build_health_report(self.sample_entries(), config)

        info, warnings, detail = render_health_report(report, Path("data/test.sqlite"), max_findings=1)

        self.assertIn("canonical entries: 6", info)
        self.assertIn("  Kindle: entries=6, lookup records=9", info)
        self.assertIn("  StarDict: entries=6, lookup records=7", info)
        self.assertIn("  single-target aliases: 1", info)
        self.assertIn("missing expected term: Missing", warnings)
        self.assertIn('alias: alias="Fireball" main="Fireball Spell" source=title-suffix-spell', detail)
        self.assertIn('multi-target lookup: lookup="Heal Pet" targets="Heal Pet Potion" | "Heal Pet Spell"', detail)

    def test_render_health_report_caps_cleanup_candidates_in_small_output(self) -> None:
        config = project_config_from_mapping(test_config())
        report = build_health_report(
            [
                Entry("Alpha", "https://example/Alpha", "Alpha"),
                Entry("Beta", "https://example/Beta", "Beta"),
                Entry("Gamma", "https://example/Gamma", "Gamma"),
            ],
            config,
        )

        info, _warnings, detail = render_health_report(report, Path("data/test.sqlite"), max_findings=1)

        cleanup_lines = [line for line in info if "**Alpha**" in line or "**Beta**" in line or "**Gamma**" in line]
        self.assertEqual(len(cleanup_lines), 1)
        self.assertIn("wiki cleanup candidates: 3", info)
        self.assertTrue(any("use --verbosity full to show all cleanup candidates" in line for line in info))
        self.assertTrue(all("https://example/" not in line for line in cleanup_lines))
        cleanup_detail = [line for line in detail if line.startswith("wiki cleanup candidate:")]
        self.assertEqual(len(cleanup_detail), 3)
        self.assertTrue(all("https://example/" in line for line in cleanup_detail))
        self.assertTrue(all("wiki-cleanup:" not in line for line in cleanup_lines + cleanup_detail))
        self.assertTrue(all("**" in line for line in cleanup_lines + cleanup_detail))

    def test_cli_reports_health_from_fixture_database(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            db_path = root / "entries.sqlite"
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
                "INSERT INTO pages VALUES (?, ?, ?, ?, '', 'ok')",
                [
                    ("Carl", "https://example/Carl", "Characters", "Carl is a crawler."),
                    ("Earth", "https://example/Earth", "Items", "Earth is a planet."),
                    ("Earth Box", "https://example/Earth_Box", "Items", "Earth Box is a reward."),
                    ("Fireball Spell", "https://example/Fireball_Spell", "Spells", "Fireball Spell burns things."),
                    ("Heal Pet Potion", "https://example/Heal_Pet_Potion", "Items", "A potion that helps pets."),
                    ("Heal Pet Spell", "https://example/Heal_Pet_Spell", "Spells", "A spell that helps pets."),
                ],
            )
            conn.commit()
            conn.close()
            config_path = root / "config.json"
            config_path.write_text(json.dumps(test_config(str(db_path))), encoding="utf-8")
            stdout = io.StringIO()

            with contextlib.redirect_stdout(stdout):
                result = main(["-c", str(config_path), "-v", "--max-findings", "1"])

        output = stdout.getvalue()
        self.assertEqual(result, 0)
        self.assertIn("canonical entries: 6", output)
        self.assertIn("missing expected term: Missing", output)
        self.assertIn('alias: alias="Fireball" main="Fireball Spell"', output)

    def test_health_cli_accepts_verbose_and_rejects_paths_only(self) -> None:
        args = parse_args(["-v"])
        output = output_from_args(args)
        self.assertEqual(output.verbosity, "full")
        self.assertFalse(output.paths_only)

        with self.assertRaises(SystemExit):
            parse_args(["--paths-only"])


if __name__ == "__main__":
    unittest.main()
