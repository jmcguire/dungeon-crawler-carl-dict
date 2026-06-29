#!/usr/bin/env python3
"""Build small Kindle dictionaries for physical lookup experiments."""

from __future__ import annotations

import argparse
import html
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from xml.etree import ElementTree as ET

from fandom_dict.cli.output import add_output_arguments, output_from_args
from fandom_dict.config import DEFAULT_CONFIG_PATH, load_project_config, slugify_title
from fandom_dict.entries import (
    Entry,
    build_lookup_report,
    load_entries,
    sanitize_inline_html,
)
from fandom_dict.formats.kindle import compile_with_kindlegen, write_cover_xhtml, write_opf


DEFAULT_OUTPUT_DIR = Path("build/kindle-lookup-experiments")
EXPERIMENT_AUTHOR = "Kindle lookup experiment"
EXPERIMENT_SOURCE = "Dungeon Crawler Carl Dictionary lookup experiment"
TARGET_TITLES = (
    "Carl",
    "Donut",
    "Mordecai",
    "Valtay Corporation",
    "Gwendolyn Duet",
    "Desperado Club",
    "Dirigible Gnomes",
    "Heal Spell",
    "Heal Scroll",
    "1914 Box",
    "Fire Fingers Spell",
    "Katia Grim",
    "Earth",
    "Earth Box",
)
TEST_PHRASES = (
    "Carl",
    "Carl's",
    "Donut",
    "Mordecai",
    "The Valtay Corporation",
    "Valtay Corporation",
    "Valtay",
    "Gwendolyn Duet",
    "Gwendolyn",
    "Duet",
    "Desperado Club",
    "The Desperado Club",
    "dirigible gnomes",
    "Dirigible Gnomes",
    "Heal Spell",
    "Heal spell",
    "Heal",
    "Fire Fingers",
    "1914",
    "1914 Box",
    "Katia",
    "Grim",
    "Earth",
    "Earth Box",
)


@dataclass(frozen=True)
class ExperimentItem:
    """One hand-picked entry and its lookup aliases for the experiment."""

    entry: Entry
    aliases: tuple[str, ...]
    synthetic: bool = False


@dataclass(frozen=True)
class LookupWord:
    """A lookup word and every canonical entry it should reach."""

    word: str
    targets: tuple[str, ...]


@dataclass(frozen=True)
class Variant:
    """One rendered dictionary variation."""

    slug: str
    title: str
    description: str
    anchor_style: str
    alias_style: str
    multi_strategy: str = "duplicate"
    rich_markup: bool = True
    extra_inflections: bool = False


@dataclass(frozen=True)
class VariantBuild:
    """Paths and metadata for one experiment dictionary build."""

    slug: str
    title: str
    description: str
    directory: str
    xhtml: str
    opf: str
    mobi: str | None
    entry_count: int
    single_alias_count: int
    multi_lookup_count: int


VARIANTS = (
    Variant(
        "dcc-1-baseline-current",
        "DCC Lookup Test 1",
        "Current production-like shape: pre-headword anchor, aliases as idx:iform, duplicate entries for multi-target lookups.",
        anchor_style="before",
        alias_style="iform",
    ),
    Variant(
        "dcc-2-no-pre-anchor",
        "DCC Lookup Test 2",
        "No separate pre-headword anchor; relies on idx:entry id and keeps aliases as idx:iform.",
        anchor_style="entry-id-only",
        alias_style="iform",
    ),
    Variant(
        "dcc-3-post-orth-anchor",
        "DCC Lookup Test 3",
        "Moves the internal-link anchor after idx:orth while keeping aliases as idx:iform.",
        anchor_style="after-orth",
        alias_style="iform",
    ),
    Variant(
        "dcc-4-direct-alias-entries",
        "DCC Lookup Test 4",
        "Aliases are direct duplicate idx:entry blocks instead of idx:iform inflections.",
        anchor_style="before",
        alias_style="direct-entry",
    ),
    Variant(
        "dcc-5-multiple-orth-tags",
        "DCC Lookup Test 5",
        "Aliases are extra idx:orth tags in the same entry, including type=\"silent\".",
        anchor_style="before",
        alias_style="multiple-orth",
    ),
    Variant(
        "dcc-6-extra-inflections",
        "DCC Lookup Test 6",
        "Current idx:iform model plus lowercase and possessive lookup forms.",
        anchor_style="before",
        alias_style="iform",
        extra_inflections=True,
    ),
    Variant(
        "dcc-7-combined-multi-target",
        "DCC Lookup Test 7",
        "Ambiguous lookups such as Heal and Earth render as one combined definition.",
        anchor_style="before",
        alias_style="iform",
        multi_strategy="combined",
    ),
    Variant(
        "dcc-8-minimal-popup-markup",
        "DCC Lookup Test 8",
        "Minimal popup markup: no pre-headword anchor, no source footer, and simple definition paragraphs.",
        anchor_style="entry-id-only",
        alias_style="iform",
        rich_markup=False,
    ),
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--min-definition-length", type=int, default=8)
    parser.add_argument("--compile", action="store_true", help="Compile each OPF with KindleGen when available.")
    add_output_arguments(parser)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the experiment builder."""

    args = parse_args(argv)
    output = output_from_args(args)
    config = load_project_config(args.config)
    entries = load_entries(
        args.input or config.database_path,
        args.min_definition_length,
        sidebar_fields=config.sidebar_fields,
        strip_parenthetical_disambiguation=config.title_aliases.strip_parenthetical,
        max_summary_length=config.max_summary_length,
    )
    items = experiment_items_from_entries(entries, config)
    builds = build_experiment_bundle(items, args.output_dir, compile_outputs=args.compile)
    output.path(args.output_dir / "MANIFEST.md")
    output.path(args.output_dir / "TESTING_CHECKLIST.md")
    for build in builds:
        output.path(build.directory)
    output.info(f"experiment dictionaries: {len(builds)}")
    output.close()
    return 0


def experiment_items_from_entries(entries: list[Entry], config) -> list[ExperimentItem]:
    """Return the real and synthetic entries used by all experiment dictionaries."""

    lookup_report = build_lookup_report(
        entries,
        title_suffix_aliases=config.title_aliases.suffixes,
        title_prefix_aliases=config.title_aliases.prefixes,
        strip_parenthetical_disambiguation=config.title_aliases.strip_parenthetical,
        title_component_ignore_words=config.title_aliases.component_ignore_words,
        sidebar_alias_labels=config.sidebar_alias_labels,
    )
    by_title = {entry.title.casefold(): entry for entry in entries}
    items: list[ExperimentItem] = []
    for title in TARGET_TITLES:
        entry = by_title.get(title.casefold()) or synthetic_entry(title)
        aliases = lookup_report.aliases.get(entry.title, [entry.title])
        aliases = tuple(dict.fromkeys([entry.title, *aliases, *synthetic_aliases(entry.title)]))
        items.append(ExperimentItem(entry, aliases, synthetic=entry.title.casefold() not in by_title))
    return items


def synthetic_entry(title: str) -> Entry:
    """Return a tiny synthetic entry when the current DCC database lacks a useful test target."""

    if title == "Dirigible Gnomes":
        return Entry(
            "Dirigible Gnomes",
            "https://example.invalid/Dirigible_Gnomes",
            "<b>Dirigible Gnomes</b> are a synthetic lookup test entry for lowercase multi-word selections.",
            details=(("Test note", "Synthetic entry; not currently present in the normalized DCC database."),),
        )
    if title == "Earth":
        return Entry(
            "Earth",
            "https://example.invalid/Earth",
            "<b>Earth</b> is a synthetic lookup test entry used to collide with Earth Box.",
            details=(("Test note", "Synthetic entry; used for multi-target lookup testing."),),
        )
    return Entry(
        title,
        f"https://example.invalid/{title.replace(' ', '_')}",
        f"<b>{html.escape(title)}</b> is a synthetic lookup test entry.",
        details=(("Test note", "Synthetic fallback entry."),),
    )


def synthetic_aliases(title: str) -> tuple[str, ...]:
    """Return extra aliases needed for missing or collision-focused test entries."""

    if title == "Dirigible Gnomes":
        return ("dirigible gnomes", "Dirigible gnomes")
    if title == "Earth Box":
        return ("Earth", "The Earth Box")
    return ()


def build_experiment_bundle(
    items: list[ExperimentItem],
    output_dir: Path,
    *,
    compile_outputs: bool = False,
) -> list[VariantBuild]:
    """Build all experiment dictionaries and their manifest/checklist files."""

    output_dir.mkdir(parents=True, exist_ok=True)
    lookup_words = lookup_words_for_items(items)
    builds = [
        build_variant(variant, items, lookup_words, output_dir / variant.slug, compile_outputs=compile_outputs)
        for variant in VARIANTS
    ]
    write_test_book(output_dir / "test-book", compile_output=compile_outputs)
    write_manifest(output_dir, builds)
    write_checklist(output_dir, builds)
    return builds


def lookup_words_for_items(items: list[ExperimentItem]) -> list[LookupWord]:
    """Group canonical titles and aliases into collision-aware lookup words."""

    canonical_by_folded = {item.entry.title.casefold(): item.entry.title for item in items}
    targets_by_word: dict[str, list[str]] = {}
    display_by_word: dict[str, str] = {}
    order = {item.entry.title: index for index, item in enumerate(items)}

    for item in items:
        add_lookup_target(targets_by_word, display_by_word, item.entry.title, item.entry.title)
        for alias in item.aliases:
            alias = alias.strip()
            if not alias:
                continue
            add_lookup_target(targets_by_word, display_by_word, alias, item.entry.title)

    lookup_words = []
    for folded, targets in targets_by_word.items():
        unique_targets = tuple(sorted(dict.fromkeys(targets), key=lambda target: (target.casefold() != folded, order[target])))
        word = canonical_by_folded.get(folded, display_by_word[folded])
        lookup_words.append(LookupWord(word, unique_targets))
    return sorted(lookup_words, key=lambda lookup: lookup.word.casefold())


def add_lookup_target(
    targets_by_word: dict[str, list[str]],
    display_by_word: dict[str, str],
    word: str,
    target: str,
) -> None:
    """Add one target for one lookup word."""

    folded = word.casefold()
    display_by_word.setdefault(folded, word)
    targets_by_word.setdefault(folded, []).append(target)


def build_variant(
    variant: Variant,
    items: list[ExperimentItem],
    lookup_words: list[LookupWord],
    output_dir: Path,
    *,
    compile_outputs: bool,
) -> VariantBuild:
    """Build one experiment dictionary variant."""

    output_dir.mkdir(parents=True, exist_ok=True)
    title = variant.title
    base_name = slugify_title(title)
    cover_path = output_dir / f"{base_name}-cover.xhtml"
    xhtml_path = output_dir / f"{base_name}.xhtml"
    opf_path = output_dir / f"{base_name}.opf"
    write_cover_xhtml(cover_path, title, EXPERIMENT_AUTHOR)
    xhtml_path.write_text(render_dictionary_xhtml(variant, items, lookup_words), encoding="utf-8")
    write_opf(opf_path, title, EXPERIMENT_AUTHOR, xhtml_path.name, f"dcdict-lookup-test:{variant.slug}", cover_path.name)
    ET.parse(cover_path)
    ET.parse(xhtml_path)
    ET.parse(opf_path)
    compilation = compile_with_kindlegen(opf_path) if compile_outputs else None
    single_alias_count = sum(1 for lookup in lookup_words if len(lookup.targets) == 1 and lookup.word != lookup.targets[0])
    multi_lookup_count = sum(1 for lookup in lookup_words if len(lookup.targets) > 1)
    return VariantBuild(
        slug=variant.slug,
        title=variant.title,
        description=variant.description,
        directory=str(output_dir),
        xhtml=str(xhtml_path),
        opf=str(opf_path),
        mobi=str(compilation.output_path) if compilation else None,
        entry_count=len(items),
        single_alias_count=single_alias_count,
        multi_lookup_count=multi_lookup_count,
    )


def render_dictionary_xhtml(variant: Variant, items: list[ExperimentItem], lookup_words: list[LookupWord]) -> str:
    """Render one complete XHTML dictionary experiment."""

    entries_by_title = {item.entry.title: item.entry for item in items}
    aliases_by_title = single_aliases_by_title(variant, lookup_words)
    sections: list[str] = [f"      <mbp:pagebreak />\n      <h1>{html.escape(variant.title)}</h1>"]
    for index, item in enumerate(items, 1):
        sections.append(render_entry(item.entry, f"{index}", variant, aliases_by_title.get(item.entry.title, ())))
        if variant.alias_style == "direct-entry":
            for alias_index, alias in enumerate(aliases_by_title.get(item.entry.title, ()), 1):
                sections.append(render_entry(item.entry, f"{index}-alias-{alias_index}", variant, (), lookup_value=alias))

    multi_lookups = [lookup for lookup in lookup_words if len(lookup.targets) > 1]
    for multi_index, lookup in enumerate(multi_lookups, 1):
        if variant.multi_strategy == "combined":
            sections.append(render_combined_entry(lookup, entries_by_title, f"multi-{multi_index}", variant))
        else:
            for target_index, target in enumerate(lookup.targets, 1):
                sections.append(
                    render_entry(entries_by_title[target], f"multi-{multi_index}-{target_index}", variant, (), lookup_value=lookup.word)
                )

    body = "\n\n      <hr />\n\n".join(sections)
    return f"""<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml"
      xmlns:idx="http://www.mobipocket.com/idx"
      xmlns:mbp="http://www.mobipocket.com/mbp">
  <head>
    <meta http-equiv="Content-Type" content="text/html; charset=utf-8" />
    <title>{html.escape(variant.title)}</title>
    <style type="text/css">
      body {{ font-family: serif; }}
      ul.definition {{ margin-top: 0.35em; }}
      .source {{ font-size: 0.8em; }}
    </style>
  </head>
  <body>
    <mbp:frameset>
{body}
    </mbp:frameset>
  </body>
</html>
"""


def single_aliases_by_title(variant: Variant, lookup_words: list[LookupWord]) -> dict[str, tuple[str, ...]]:
    """Return single-target aliases usable by the current variant."""

    aliases: dict[str, list[str]] = {}
    for lookup in lookup_words:
        if len(lookup.targets) != 1 or lookup.word.casefold() == lookup.targets[0].casefold():
            continue
        aliases.setdefault(lookup.targets[0], []).append(lookup.word)
    if variant.extra_inflections:
        for lookup in lookup_words:
            for target in lookup.targets:
                aliases.setdefault(target, [])
                for extra in extra_aliases_for_word(lookup.word):
                    if extra != target:
                        aliases[target].append(extra)
    return {title: tuple(dict.fromkeys(values)) for title, values in aliases.items()}


def extra_aliases_for_word(word: str) -> tuple[str, ...]:
    """Return lower-case and possessive lookup variants for physical tests."""

    extras = []
    lowered = word.lower()
    if lowered != word:
        extras.append(lowered)
    if " " not in word and not word.endswith("'s"):
        extras.append(f"{word}'s")
        extras.append(f"{lowered}'s")
    return tuple(extras)


def render_entry(
    entry: Entry,
    entry_id: str,
    variant: Variant,
    aliases: tuple[str, ...],
    *,
    lookup_value: str | None = None,
) -> str:
    """Render one experiment entry."""

    title = html.escape(entry.title, quote=True)
    lookup = html.escape(lookup_value or entry.title, quote=True)
    before_anchor = f'        <a id="entry-{entry_id}"></a>\n' if variant.anchor_style == "before" else ""
    after_anchor = f'\n        <a id="entry-{entry_id}"></a>' if variant.anchor_style == "after-orth" else ""
    orth = render_orth(entry.title, lookup, aliases, variant)
    definition = render_definition(entry, variant)
    return f"""<idx:entry name="default" scriptable="yes" spell="yes" id="entry-{entry_id}">
{before_anchor}        {orth}{after_anchor}
        <idx:short>
{definition}
        </idx:short>
      </idx:entry>"""


def render_orth(title: str, lookup: str, aliases: tuple[str, ...], variant: Variant) -> str:
    """Render canonical and alias lookup markup for one variant."""

    escaped_title = html.escape(title, quote=True)
    clean_aliases = tuple(alias for alias in aliases if alias != title)
    if variant.alias_style == "iform" and clean_aliases:
        iforms = "\n".join(
            f'            <idx:iform value="{html.escape(alias, quote=True)}" />' for alias in clean_aliases
        )
        return (
            f'<idx:orth value="{lookup}"><b>{escaped_title}</b>\n'
            f"          <idx:infl>\n{iforms}\n          </idx:infl>\n"
            f"        </idx:orth>"
        )
    if variant.alias_style == "multiple-orth" and clean_aliases:
        extra_orths = "\n".join(
            f'        <idx:orth value="{html.escape(alias, quote=True)}" type="silent" />' for alias in clean_aliases
        )
        return f'<idx:orth value="{lookup}"><b>{escaped_title}</b></idx:orth>\n{extra_orths}'
    return f'<idx:orth value="{lookup}"><b>{escaped_title}</b></idx:orth>'


def render_definition(entry: Entry, variant: Variant) -> str:
    """Render one entry definition using rich or minimal markup."""

    definition = sanitize_inline_html(entry.definition)
    if not variant.rich_markup:
        return f"        <p>{definition}</p>"
    detail_items = "\n".join(
        f"          <li><b>{html.escape(label, quote=False)}:</b> {sanitize_inline_html(value)}</li>"
        for label, value in entry.details
    )
    details = f"\n{detail_items}" if detail_items else ""
    return f"""        <ul class="definition">
          <li>{definition}</li>
{details}
        </ul>
        <p class="source">Source: <a href="{html.escape(entry.url, quote=True)}">{html.escape(entry.title)} on {EXPERIMENT_SOURCE}</a></p>"""


def render_combined_entry(
    lookup: LookupWord,
    entries_by_title: dict[str, Entry],
    entry_id: str,
    variant: Variant,
) -> str:
    """Render one combined multi-target lookup entry."""

    items = "\n".join(
        f"          <li><b>{html.escape(title)}:</b> {sanitize_inline_html(entries_by_title[title].definition)}</li>"
        for title in lookup.targets
    )
    combined = Entry(
        lookup.word,
        "https://example.invalid/combined_lookup",
        f"Multiple definitions for <b>{html.escape(lookup.word)}</b>.",
    )
    definition = f"""        <p>Multiple definitions for <b>{html.escape(lookup.word)}</b>:</p>
        <ul class="definition">
{items}
        </ul>"""
    return f"""<idx:entry name="default" scriptable="yes" spell="yes" id="entry-{entry_id}">
        <idx:orth value="{html.escape(lookup.word, quote=True)}"><b>{html.escape(lookup.word)}</b></idx:orth>
        <idx:short>
{definition if variant.rich_markup else f"        <p>{sanitize_inline_html(combined.definition)}</p>"}
        </idx:short>
      </idx:entry>"""


def write_manifest(output_dir: Path, builds: list[VariantBuild]) -> None:
    """Write machine-readable and human-readable experiment manifests."""

    data = {"variants": [asdict(build) for build in builds], "test_phrases": list(TEST_PHRASES)}
    (output_dir / "MANIFEST.json").write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    lines = [
        "# Kindle Lookup Experiment Manifest",
        "",
        "Load one dictionary at a time on the Kindle. Each dictionary has a distinct title.",
        "",
    ]
    for build in builds:
        lines.extend(
            [
                f"## {build.title} ({build.slug})",
                "",
                build.description,
                "",
                f"- Directory: `{Path(build.directory).name}`",
                f"- OPF: `{Path(build.opf).name}`",
                f"- MOBI: `{Path(build.mobi).name if build.mobi else 'not compiled'}`",
                f"- Entries: {build.entry_count}",
                f"- Single-target aliases: {build.single_alias_count}",
                f"- Multi-target lookup words: {build.multi_lookup_count}",
                "",
            ]
        )
    (output_dir / "MANIFEST.md").write_text("\n".join(lines), encoding="utf-8")


def write_checklist(output_dir: Path, builds: list[VariantBuild]) -> None:
    """Write a manual Kindle testing checklist."""

    lines = [
        "# Kindle Lookup Experiment Checklist",
        "",
        "Use the same book/page and load one dictionary at a time. Record what the Kindle shows.",
        "",
        "Suggested result codes: `definition`, `wrong definition`, `no definition`, `no Dictionary tab`, `X-Ray only`, `Wikipedia/Search only`.",
        "",
        "## Test Phrases",
        "",
    ]
    lines.extend(f"- `{phrase}`" for phrase in TEST_PHRASES)
    lines.extend(["", "## Results", ""])
    for build in builds:
        lines.extend([f"### {build.title} ({build.slug})", ""])
        lines.extend(f"- `{phrase}`: " for phrase in TEST_PHRASES)
        lines.append("")
    (output_dir / "TESTING_CHECKLIST.md").write_text("\n".join(lines), encoding="utf-8")


def write_test_book(output_dir: Path, *, compile_output: bool) -> None:
    """Write a small book containing every phrase to highlight on Kindle."""

    output_dir.mkdir(parents=True, exist_ok=True)
    title = "DCC Lookup Experiment Test Book"
    xhtml_path = output_dir / "dcc-lookup-test-book.xhtml"
    opf_path = output_dir / "dcc-lookup-test-book.opf"
    phrase_rows = "\n".join(
        f"      <p><b>{html.escape(phrase)}:</b> Try selecting {html.escape(phrase)} in this sentence.</p>"
        for phrase in TEST_PHRASES
    )
    xhtml_path.write_text(
        f"""<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml">
  <head>
    <meta http-equiv="Content-Type" content="text/html; charset=utf-8" />
    <title>{title}</title>
  </head>
  <body>
    <h1>{title}</h1>
{phrase_rows}
  </body>
</html>
""",
        encoding="utf-8",
    )
    opf_path.write_text(
        f"""<?xml version="1.0" encoding="utf-8"?>
<package unique-identifier="uid">
  <metadata>
    <dc-metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
      <dc:Identifier id="uid">dcdict-lookup-test-book</dc:Identifier>
      <dc:Title>{title}</dc:Title>
      <dc:Language>en</dc:Language>
      <dc:Creator>{EXPERIMENT_AUTHOR}</dc:Creator>
    </dc-metadata>
  </metadata>
  <manifest>
    <item id="content" media-type="application/xhtml+xml" href="{xhtml_path.name}" />
  </manifest>
  <spine>
    <itemref idref="content" />
  </spine>
</package>
""",
        encoding="utf-8",
    )
    ET.parse(xhtml_path)
    ET.parse(opf_path)
    if compile_output:
        compile_with_kindlegen(opf_path)


if __name__ == "__main__":
    raise SystemExit(main())
