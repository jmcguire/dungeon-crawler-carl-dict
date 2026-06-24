import json
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from tempfile import TemporaryDirectory

from fandom_dict.cli.build_kindle_lookup_experiments import (
    ExperimentItem,
    build_experiment_bundle,
    lookup_words_for_items,
)
from fandom_dict.entries import Entry


class KindleLookupExperimentTests(unittest.TestCase):
    def test_lookup_words_preserve_multi_target_collisions(self) -> None:
        items = [
            ExperimentItem(Entry("Heal Spell", "https://example/Heal_Spell", "A spell."), ("Heal Spell", "Heal")),
            ExperimentItem(Entry("Heal Scroll", "https://example/Heal_Scroll", "A scroll."), ("Heal Scroll", "Heal")),
            ExperimentItem(Entry("Earth", "https://example/Earth", "A planet."), ("Earth",)),
            ExperimentItem(Entry("Earth Box", "https://example/Earth_Box", "A box."), ("Earth Box", "Earth")),
        ]

        lookups = {lookup.word: lookup.targets for lookup in lookup_words_for_items(items)}

        self.assertEqual(lookups["Heal"], ("Heal Spell", "Heal Scroll"))
        self.assertEqual(lookups["Earth"], ("Earth", "Earth Box"))

    def test_build_experiment_bundle_writes_variant_shapes_and_manifest(self) -> None:
        items = [
            ExperimentItem(Entry("Carl", "https://example/Carl", "<b>Carl</b> is a crawler."), ("Carl",)),
            ExperimentItem(Entry("Valtay Corporation", "https://example/Valtay", "The <b>Valtay Corporation</b> is a company."), ("Valtay Corporation", "Valtay")),
            ExperimentItem(Entry("Gwendolyn Duet", "https://example/Gwendolyn", "<b>Gwendolyn Duet</b> is a crawler."), ("Gwendolyn Duet", "Gwendolyn", "Duet")),
            ExperimentItem(Entry("Heal Spell", "https://example/Heal_Spell", "<b>Heal</b> restores health."), ("Heal Spell", "Heal")),
            ExperimentItem(Entry("Heal Scroll", "https://example/Heal_Scroll", "<b>Heal Scroll</b> teaches Heal."), ("Heal Scroll", "Heal")),
            ExperimentItem(Entry("Dirigible Gnomes", "https://example/Gnomes", "<b>Dirigible Gnomes</b> float."), ("Dirigible Gnomes", "dirigible gnomes")),
        ]

        with TemporaryDirectory() as tmp_dir:
            output_dir = Path(tmp_dir)
            builds = build_experiment_bundle(items, output_dir, compile_outputs=False)

            self.assertEqual(len(builds), 8)
            manifest = json.loads((output_dir / "MANIFEST.json").read_text(encoding="utf-8"))
            self.assertEqual(len(manifest["variants"]), 8)
            self.assertIn("The Valtay Corporation", (output_dir / "TESTING_CHECKLIST.md").read_text(encoding="utf-8"))
            ET.parse(output_dir / "test-book" / "dcc-lookup-test-book.xhtml")
            ET.parse(output_dir / "test-book" / "dcc-lookup-test-book.opf")

            baseline = (output_dir / "dcc-1-baseline-current" / "DCC-Lookup-Test-1.xhtml").read_text(encoding="utf-8")
            self.assertLess(baseline.index('<a id="entry-1"></a>'), baseline.index('<idx:orth value="Carl"'))
            self.assertIn('<idx:iform value="Valtay" />', baseline)

            no_pre_anchor = (output_dir / "dcc-2-no-pre-anchor" / "DCC-Lookup-Test-2.xhtml").read_text(encoding="utf-8")
            self.assertNotIn('<a id="entry-1"></a>', no_pre_anchor)
            self.assertIn('<idx:entry name="default" scriptable="yes" spell="yes" id="entry-1">', no_pre_anchor)

            post_anchor = (output_dir / "dcc-3-post-orth-anchor" / "DCC-Lookup-Test-3.xhtml").read_text(encoding="utf-8")
            self.assertLess(post_anchor.index('<idx:orth value="Carl"'), post_anchor.index('<a id="entry-1"></a>'))

            direct_alias = (output_dir / "dcc-4-direct-alias-entries" / "DCC-Lookup-Test-4.xhtml").read_text(encoding="utf-8")
            self.assertNotIn("<idx:iform", direct_alias)
            self.assertIn('<idx:orth value="Valtay"><b>Valtay Corporation</b></idx:orth>', direct_alias)

            multiple_orth = (output_dir / "dcc-5-multiple-orth-tags" / "DCC-Lookup-Test-5.xhtml").read_text(encoding="utf-8")
            self.assertIn('<idx:orth value="Valtay" type="silent" />', multiple_orth)

            extra_inflections = (output_dir / "dcc-6-extra-inflections" / "DCC-Lookup-Test-6.xhtml").read_text(encoding="utf-8")
            self.assertIn('<idx:iform value="carl&#x27;s" />', extra_inflections)
            self.assertIn('<idx:iform value="dirigible gnomes" />', extra_inflections)

            combined = (output_dir / "dcc-7-combined-multi-target" / "DCC-Lookup-Test-7.xhtml").read_text(encoding="utf-8")
            self.assertIn('<idx:orth value="Heal"><b>Heal</b></idx:orth>', combined)
            self.assertIn("Multiple definitions for <b>Heal</b>", combined)

            minimal = (output_dir / "dcc-8-minimal-popup-markup" / "DCC-Lookup-Test-8.xhtml").read_text(encoding="utf-8")
            self.assertNotIn('<p class="source">', minimal)
            self.assertIn("<p><b>Carl</b> is a crawler.</p>", minimal)

