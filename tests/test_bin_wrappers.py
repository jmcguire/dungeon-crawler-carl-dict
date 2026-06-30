import os
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


class BinWrapperTests(unittest.TestCase):
    def test_bin_wrappers_are_executable_and_dispatch_to_cli_modules(self) -> None:
        expected = {
            "audit_entries": "fandom_dict.cli.audit_entries",
            "badges": "fandom_dict.cli.badges",
            "build_kindle_dictionary": "fandom_dict.cli.build_kindle_dictionary",
            "build_kindle_lookup_experiments": "fandom_dict.cli.build_kindle_lookup_experiments",
            "build_kobo_dictionary": "fandom_dict.cli.build_kobo_dictionary",
            "build_stardict_dictionary": "fandom_dict.cli.build_stardict_dictionary",
            "fetch_entries": "fandom_dict.cli.fetch_entries",
            "health_report": "fandom_dict.cli.health_report",
            "release": "fandom_dict.cli.release",
        }
        for name, module in expected.items():
            with self.subTest(name=name):
                path = REPO_ROOT / "bin" / name
                self.assertTrue(path.is_file())
                self.assertTrue(os.access(path, os.X_OK))
                text = path.read_text(encoding="utf-8")
                self.assertEqual(text, f'#!/usr/bin/env sh\nexec python3 -m {module} "$@"\n')
