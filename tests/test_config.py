import unittest
from pathlib import Path

from fandom_dict.config import load_project_config


class ProjectConfigTests(unittest.TestCase):
    def test_loads_dcc_config(self) -> None:
        config = load_project_config(Path("configs/dungeon-crawler-carl.json"))

        self.assertEqual(config.fandom, "dungeon-crawler-carl")
        self.assertEqual(config.database_path, Path("data/dungeon-crawler-carl.sqlite"))
        self.assertEqual(config.kindle_dir, Path("build/dungeon-crawler-carl/kindle"))
        self.assertIn("Characters", config.categories)
        self.assertIn(" Box", config.title_aliases.suffixes)
        self.assertIn("Corporation", config.title_aliases.component_ignore_words)
        self.assertIn("Carl", config.smoke_headwords)
        self.assertEqual(config.sidebar_alias_labels, ("Aliases",))

    def test_loads_iceandfire_example_config(self) -> None:
        config = load_project_config(Path("examples/iceandfire.json"))

        self.assertEqual(config.fandom, "iceandfire")
        self.assertEqual(config.source_name, "Ice and Fire Wiki")
        self.assertEqual(config.database_path, Path("data/iceandfire.sqlite"))
        self.assertEqual(config.stardict_dir, Path("build/iceandfire/stardict"))
        self.assertEqual(config.title_aliases.prefixes, ("House ",))
        self.assertEqual(config.title_aliases.component_ignore_words, ())
        self.assertIn("Baelon Targaryen", config.smoke_headwords)
        self.assertEqual(config.sidebar_alias_labels, ("Aliases",))


if __name__ == "__main__":
    unittest.main()
