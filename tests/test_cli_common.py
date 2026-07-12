import io
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from fandom_dict.cli.common import load_config_for_command, load_entries_for_command
from fandom_dict.cli.output import CommandOutput
from fandom_dict.config import project_config_from_mapping


def config_mapping(database_path: str) -> dict[str, object]:
    return {
        "fandom": "test",
        "title": "Test Dictionary",
        "author": "Test Author",
        "source_name": "Test Wiki",
        "categories": ["Characters"],
        "database_path": database_path,
        "build_dir": "build/test",
        "sidebar_fields": [{"source": "aliases", "label": "Aliases", "alias": True}],
        "title_aliases": {},
        "smoke_headwords": ["Carl"],
        "kobo_output_name": "dicthtml-test.zip",
    }


class CliCommonTests(unittest.TestCase):
    def test_missing_database_is_reported_without_creating_it(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "missing.sqlite"
            stderr = io.StringIO()
            output = CommandOutput(stderr=stderr)
            config = project_config_from_mapping(config_mapping(str(path)))

            entries = load_entries_for_command(path, config, 8, output)
            output.close()

            self.assertIsNone(entries)
            self.assertFalse(path.exists())
            self.assertIn("crawler database does not exist", stderr.getvalue())

    def test_invalid_config_is_reported_cleanly(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "config.json"
            path.write_text("{broken", encoding="utf-8")
            stderr = io.StringIO()
            output = CommandOutput(stderr=stderr)

            config = load_config_for_command(path, output)
            output.close()

            self.assertIsNone(config)
            self.assertIn("could not load project config", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
