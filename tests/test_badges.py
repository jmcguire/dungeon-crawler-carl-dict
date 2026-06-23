import json
import sqlite3
import textwrap
import trace
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from fandom_dict.cli.badges import (
    BADGE_NAMES,
    CoverageResult,
    badge,
    build_badges,
    coverage_color,
    entry_count_color,
    parse_trace_summary,
    parse_version,
    project_executable_line_count,
    read_badge,
    validate_badges,
    write_badge_files,
)


class BadgeTests(unittest.TestCase):
    def test_coverage_and_output_colors(self) -> None:
        self.assertEqual(coverage_color(95), "brightgreen")
        self.assertEqual(coverage_color(85), "green")
        self.assertEqual(coverage_color(75), "yellowgreen")
        self.assertEqual(coverage_color(65), "yellow")
        self.assertEqual(coverage_color(59), "red")
        self.assertEqual(entry_count_color(1200), "brightgreen")
        self.assertEqual(entry_count_color(700), "green")
        self.assertEqual(entry_count_color(7), "yellowgreen")
        self.assertEqual(entry_count_color(0), "red")

    def test_builds_expected_badge_payloads(self) -> None:
        badges = build_badges(parse_version("1.2.3"), CoverageResult(8, 10), 1133)
        self.assertEqual(badges["release"]["message"], "v1.2.3")
        self.assertEqual(badges["coverage"]["message"], "80% lines")
        self.assertEqual(badges["coverage"]["color"], "green")
        self.assertEqual(badges["python"]["message"], "3.9+")
        self.assertEqual(badges["formats"]["message"], "Kindle + StarDict + Kobo")
        self.assertEqual(badges["licenses"]["message"], "MIT + CC BY-SA 3.0")
        self.assertEqual(badges["output"]["message"], "1,133 entries")

    def test_parse_trace_summary_counts_only_project_modules(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            (root / "fandom_dict").mkdir()
            project_file = root / "fandom_dict" / "thing.py"
            project_file.write_text("x = 1\n", encoding="ascii")
            stdlib_file = root / "not_project.py"
            stdlib_file.write_text("x = 1\n", encoding="ascii")
            output = textwrap.dedent(
                f"""
                lines   cov%   module   (path)
                   10    80%   thing    ({project_file})
                   50   100%   other    ({stdlib_file})
                """
            )
            coverage = parse_trace_summary(output, root)
            self.assertEqual(coverage.covered_lines, 8)
            self.assertEqual(coverage.executable_lines, 10)
            self.assertEqual(coverage.percent, 80)

    def test_parse_trace_summary_accepts_decimal_percentages(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            (root / "fandom_dict").mkdir()
            project_file = root / "fandom_dict" / "thing.py"
            project_file.write_text("x = 1\n", encoding="ascii")
            output = textwrap.dedent(
                f"""
                lines   cov%   module   (path)
                   10   80.0%   thing    ({project_file})
                """
            )
            coverage = parse_trace_summary(output, root)
            self.assertEqual(coverage.covered_lines, 8)
            self.assertEqual(coverage.executable_lines, 10)

    def test_project_executable_line_count_includes_unexecuted_modules(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            package = root / "fandom_dict"
            package.mkdir()
            subpackage = package / "formats"
            subpackage.mkdir()
            (package / "__init__.py").write_text("", encoding="ascii")
            (package / "covered.py").write_text("x = 1\n", encoding="ascii")
            (package / "uncovered.py").write_text("y = 2\n", encoding="ascii")
            (subpackage / "nested.py").write_text("z = 3\n", encoding="ascii")
            covered_only = len(trace._find_executable_linenos(str(package / "covered.py")))
            nested_lines = len(trace._find_executable_linenos(str(subpackage / "nested.py")))
            self.assertGreaterEqual(project_executable_line_count(root), covered_only + nested_lines)

    def test_writes_and_validates_badge_json(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            badge_dir = Path(tmp_dir)
            badges = build_badges(parse_version("1.2.3"), CoverageResult(10, 10), 42)
            write_badge_files(badge_dir, badges)
            self.assertEqual(read_badge(badge_dir / "coverage.json")["message"], "100% lines")
            validate_badges(badge_dir, parse_version("1.2.3"), 42)

            stale = json.loads((badge_dir / "output.json").read_text(encoding="utf-8"))
            stale["message"] = "41 entries"
            (badge_dir / "output.json").write_text(json.dumps(stale), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "stale"):
                validate_badges(badge_dir, parse_version("1.2.3"), 42)

    def test_fixture_database_entry_count_matches_badge_output(self) -> None:
        from fandom_dict.cli.badges import count_entries

        with TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "entries.sqlite"
            conn = sqlite3.connect(db_path)
            conn.execute(
                """
                CREATE TABLE pages (
                    title TEXT, url TEXT, first_paragraph TEXT,
                    raw_html TEXT, status TEXT
                )
                """
            )
            conn.executemany(
                "INSERT INTO pages VALUES (?, ?, ?, '', 'ok')",
                [
                    ("Carl", "https://example/Carl", "Carl is a crawler."),
                    ("Donut", "https://example/Donut", "Donut is a crawler."),
                    ("Stub", "https://example/Stub", "Stub is"),
                ],
            )
            conn.commit()
            conn.close()
            self.assertEqual(count_entries(db_path), 2)

    def test_readme_references_all_tracked_badges(self) -> None:
        readme = Path("README.md").read_text(encoding="utf-8")
        for name in BADGE_NAMES:
            with self.subTest(name=name):
                self.assertIn(f"badges%2F{name}.json", readme)
                self.assertIn(f"badges/{name}.json", readme.replace("%2F", "/"))


if __name__ == "__main__":
    unittest.main()
