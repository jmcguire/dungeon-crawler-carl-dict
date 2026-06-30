import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from fandom_dict.cli.build_kobo_link_experiments import (
    TEST_ENTRIES,
    VARIANTS,
    build_experiment_bundle,
    href_for_target,
    render_dictfile,
)
from fandom_dict.formats.kobo import find_dictgen, inspect_kobo, kobo_prefix


class KoboLinkExperimentTests(unittest.TestCase):
    def test_href_variants_are_rendered_exactly(self) -> None:
        self.assertEqual(href_for_target("Donut", VARIANTS[0]), "#Donut")
        self.assertEqual(href_for_target("Donut", VARIANTS[1]), f"{kobo_prefix('Donut')}.html#Donut")
        self.assertEqual(href_for_target("Donut", VARIANTS[2]), "dict:///Donut")

        hash_dictfile = render_dictfile(VARIANTS[0])
        relative_dictfile = render_dictfile(VARIANTS[1])
        dict_scheme_dictfile = render_dictfile(VARIANTS[2])

        self.assertIn('<a href="#Donut">Donut</a>', hash_dictfile)
        self.assertIn('<a href="do.html#Donut">Donut</a>', relative_dictfile)
        self.assertIn('<a href="dict:///Donut">Donut</a>', dict_scheme_dictfile)
        self.assertNotIn('<a name="Carl"', hash_dictfile)

    @unittest.skipUnless(find_dictgen(), "dictgen is not installed")
    def test_build_experiment_bundle_compiles_and_preserves_links(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            output_dir = Path(tmp_dir)
            builds = build_experiment_bundle(output_dir)

            self.assertEqual(len(builds), 3)
            manifest = json.loads((output_dir / "MANIFEST.json").read_text(encoding="utf-8"))
            self.assertEqual(len(manifest["variants"]), 3)
            checklist = (output_dir / "TESTING_CHECKLIST.md").read_text(encoding="utf-8")
            self.assertIn("Copy exactly one experiment ZIP at a time", checklist)
            self.assertIn("dict:///", checklist)

            by_slug = {build.slug: build for build in builds}
            hash_inspection = inspect_kobo(
                Path(by_slug["kobo-link-1-hash"].dictzip),
                required_headwords=TEST_ENTRIES,
                allowed_href_prefixes=VARIANTS[0].allowed_href_prefixes,
            )
            relative_inspection = inspect_kobo(
                Path(by_slug["kobo-link-2-shard-relative"].dictzip),
                required_headwords=TEST_ENTRIES,
                allowed_href_prefixes=VARIANTS[1].allowed_href_prefixes,
            )

        self.assertIn("#Donut", {href for entry in hash_inspection.entries for href in entry.links})
        self.assertIn("do.html#Donut", {href for entry in relative_inspection.entries for href in entry.links})
        self.assertGreater(by_slug["kobo-link-3-dict-scheme"].link_count, 0)


if __name__ == "__main__":
    unittest.main()
