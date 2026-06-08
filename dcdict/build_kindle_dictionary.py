#!/usr/bin/env python3
"""Build Kindle dictionary source files from fetched page data."""

from __future__ import annotations

import argparse
import html
import shutil
import sqlite3
import subprocess
import sys
import unicodedata
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path


DEFAULT_TITLE = "Dungeon Crawler Carl Character Dictionary"
DEFAULT_AUTHOR = "Generated from Dungeon Crawler Carl Wiki contributors"
LANGUAGE = "en-us"


@dataclass(frozen=True)
class Entry:
    """One dictionary headword and its definition."""

    title: str
    url: str
    definition: str


@dataclass(frozen=True)
class BuildResult:
    """Paths and counts produced by source generation."""

    xhtml_path: Path
    opf_path: Path
    entry_count: int


def normalize_text(text: str) -> str:
    """Normalize Unicode and collapse whitespace for Kindle definitions."""

    text = unicodedata.normalize("NFKC", text.replace("\xa0", " "))
    return " ".join(text.split())


def ascii_fold(text: str) -> str:
    """Return an ASCII-only form for accent-insensitive lookup aliases."""

    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")


def load_entries(db_path: Path, min_definition_length: int) -> list[Entry]:
    """Load usable dictionary entries from the crawler SQLite database."""

    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        """
        SELECT title, url, first_paragraph
        FROM pages
        WHERE status = 'ok' AND COALESCE(first_paragraph, '') != ''
        ORDER BY lower(title)
        """
    ).fetchall()
    entries = []
    for title, url, first_paragraph in rows:
        definition = normalize_text(first_paragraph)
        if len(definition) >= min_definition_length:
            entries.append(Entry(title=normalize_text(title), url=url, definition=definition))
    return entries


def build_aliases(entries: list[Entry]) -> dict[str, list[str]]:
    """Build conservative lookup aliases for each entry."""

    titles = {entry.title for entry in entries}
    first_names: dict[str, int] = {}
    for title in titles:
        first = title.split()[0]
        if len(first) > 2:
            first_names[first] = first_names.get(first, 0) + 1

    aliases: dict[str, list[str]] = {}
    for entry in entries:
        forms = {entry.title, entry.title.replace("_", " ")}
        folded = ascii_fold(entry.title)
        if folded and folded != entry.title:
            forms.add(folded)
        first = entry.title.split()[0]
        if first_names.get(first) == 1:
            forms.add(first)
        aliases[entry.title] = sorted(forms, key=lambda value: (value.lower(), value))
    return aliases


def entry_to_xhtml(entry: Entry, aliases: list[str], entry_id: int) -> str:
    """Render one Kindle dictionary entry with idx lookup metadata."""

    title = html.escape(entry.title, quote=True)
    definition = html.escape(entry.definition, quote=False)
    url = html.escape(entry.url, quote=True)
    infl = "\n".join(
        f'          <idx:iform value="{html.escape(alias, quote=True)}" />'
        for alias in aliases
        if alias != entry.title
    )
    infl_block = f"\n        <idx:infl>\n{infl}\n        </idx:infl>" if infl else ""
    return f"""      <idx:entry name="default" scriptable="yes" spell="yes" id="entry-{entry_id}">
        <a id="entry-{entry_id}"></a>
        <idx:orth value="{title}"><b>{title}</b>{infl_block}
        </idx:orth>
        <p>{definition}</p>
        <p class="source">Source: <a href="{url}">{title} on Dungeon Crawler Carl Wiki</a></p>
      </idx:entry>"""


def write_xhtml(entries: list[Entry], output: Path, title: str) -> None:
    """Write the Kindle dictionary XHTML source file."""

    aliases = build_aliases(entries)
    body = "\n\n".join(entry_to_xhtml(entry, aliases[entry.title], index) for index, entry in enumerate(entries, 1))
    output.write_text(
        f"""<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml"
      xmlns:idx="http://www.mobipocket.com/idx"
      xmlns:mbp="http://www.mobipocket.com/mbp">
  <head>
    <meta http-equiv="Content-Type" content="text/html; charset=utf-8" />
    <title>{html.escape(title)}</title>
    <style type="text/css">
      body {{ font-family: serif; }}
      idx\\:orth b {{ font-size: 1.15em; }}
      .source {{ font-size: 0.8em; }}
    </style>
  </head>
  <body>
    <mbp:frameset>
{body}
    </mbp:frameset>
  </body>
</html>
""",
        encoding="utf-8",
    )


def write_opf(output: Path, title: str, author: str, xhtml_name: str, identifier: str) -> None:
    """Write the OPF package file Kindle tooling compiles."""

    output.write_text(
        f"""<?xml version="1.0" encoding="utf-8"?>
<package unique-identifier="uid">
  <metadata>
    <dc-metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
      <dc:Identifier id="uid">{identifier}</dc:Identifier>
      <dc:Title>{html.escape(title)}</dc:Title>
      <dc:Language>{LANGUAGE}</dc:Language>
      <dc:Creator>{html.escape(author)}</dc:Creator>
      <dc:Publisher>Local build</dc:Publisher>
      <dc:Subject>Dictionary</dc:Subject>
      <dc:Description>Character lookup dictionary generated from fetched wiki page summaries.</dc:Description>
    </dc-metadata>
    <x-metadata>
      <DictionaryInLanguage>{LANGUAGE}</DictionaryInLanguage>
      <DictionaryOutLanguage>{LANGUAGE}</DictionaryOutLanguage>
      <DefaultLookupIndex>default</DefaultLookupIndex>
    </x-metadata>
  </metadata>
  <manifest>
    <item id="dictionary" media-type="application/xhtml+xml" href="{html.escape(xhtml_name)}" />
  </manifest>
  <spine>
    <itemref idref="dictionary" />
  </spine>
</package>
""",
        encoding="utf-8",
    )


def validate_xml(path: Path) -> None:
    """Raise if a generated XML/XHTML file is not well-formed."""

    ET.parse(path)


def build_dictionary_sources(
    entries: list[Entry],
    output_dir: Path,
    title: str,
    author: str,
) -> BuildResult:
    """Generate and validate Kindle dictionary source files."""

    output_dir.mkdir(parents=True, exist_ok=True)
    xhtml_path = output_dir / "dictionary.xhtml"
    opf_path = output_dir / "dictionary.opf"
    identifier = f"urn:uuid:{uuid.uuid4()}"

    write_xhtml(entries, xhtml_path, title)
    write_opf(opf_path, title, author, xhtml_path.name, identifier)
    validate_xml(xhtml_path)
    validate_xml(opf_path)

    return BuildResult(xhtml_path=xhtml_path, opf_path=opf_path, entry_count=len(entries))


def compile_with_kindlegen(opf_path: Path) -> Path | None:
    """Compile OPF/XHTML sources into MOBI when kindlegen is available."""

    kindlegen = find_kindlegen()
    if not kindlegen:
        return None
    result = subprocess.run(
        [kindlegen, opf_path.name, "-verbose"],
        cwd=opf_path.parent,
    )
    mobi_path = opf_path.with_suffix(".mobi")
    # Legacy kindlegen exits non-zero when it builds with warnings, including
    # expected dictionary warnings. Treat the output file as the success signal.
    if mobi_path.exists():
        return mobi_path
    result.check_returncode()
    return None


def find_kindlegen() -> str | None:
    """Find kindlegen on PATH or bundled inside Kindle Previewer on macOS."""

    if path := shutil.which("kindlegen"):
        return path

    mac_previewer_kindlegen = Path(
        "/Applications/Kindle Previewer 3.app/Contents/lib/fc/bin/kindlegen"
    )
    if sys.platform == "darwin" and mac_previewer_kindlegen.exists():
        return str(mac_previewer_kindlegen)

    return None


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments for the dictionary builder."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=Path("data/characters.sqlite"))
    parser.add_argument("--output-dir", type=Path, default=Path("build"))
    parser.add_argument("--title", default=DEFAULT_TITLE)
    parser.add_argument("--author", default=DEFAULT_AUTHOR)
    parser.add_argument("--min-definition-length", type=int, default=8)
    parser.add_argument("--compile", action="store_true", help="Run kindlegen if it is installed.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the dictionary build command-line workflow."""

    args = parse_args(argv)
    entries = load_entries(args.input, args.min_definition_length)
    if not entries:
        raise SystemExit(f"no usable entries found in {args.input}")

    result = build_dictionary_sources(entries, args.output_dir, args.title, args.author)

    print(f"wrote {result.xhtml_path}")
    print(f"wrote {result.opf_path}")
    print(f"entries: {result.entry_count}")

    if args.compile:
        mobi_path = compile_with_kindlegen(result.opf_path)
        if mobi_path:
            print(f"compiled {mobi_path}")
        else:
            print("kindlegen was not found; source files are ready, but no .mobi was produced")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
