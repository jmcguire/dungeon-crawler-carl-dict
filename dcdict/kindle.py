"""Render Kindle dictionary source files and compile them to MOBI."""

from __future__ import annotations

import html
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from dcdict.entries import (
    ALLOWED_INLINE_TAGS,
    AliasReport,
    BIOGRAPHICAL_FIELD_LABELS,
    LINKABLE_INLINE_TAGS,
    LookupForm,
    LookupReport,
    SIDEBAR_FIELD_LABELS,
    BiographicalInfoParser,
    Entry,
    EntryReferenceLinker,
    InlineTextParser,
    LinkedDefinitionParser,
    SafeInlineHtmlParser,
    SidebarInfoParser,
    SpoilerNoticeParser,
    ascii_fold,
    biographical_details_from_html,
    build_aliases,
    build_alias_report,
    build_lookup_report,
    compile_title_pattern,
    filter_low_quality_entries,
    forwarding_target_from_definition,
    has_class,
    is_linkable_title,
    is_low_quality_definition,
    link_definition_references,
    load_entries,
    normalize_inline_html,
    normalize_text,
    resolve_forwarding_entries,
    sanitize_inline_html,
    sidebar_details_from_html,
    spoiler_notice_from_html,
    suffix_stripped_alias,
    text_from_inline_html,
)


DEFAULT_TITLE = "Dungeon Crawler Carl Dictionary"
DEFAULT_AUTHOR = "Generated from Dungeon Crawler Carl Wiki contributors"
LANGUAGE = "en-us"
DEFAULT_RELEASE_VERSION = "dev"


@dataclass(frozen=True)
class BuildResult:
    """Paths and counts produced by Kindle source generation."""

    xhtml_path: Path
    opf_path: Path
    entry_count: int
    alias_count: int = 0
    multi_lookup_count: int = 0
    omitted_alias_count: int = 0


@dataclass(frozen=True)
class CompilationResult:
    """Structured result from a Kindle compiler invocation."""

    output_path: Path
    compiler_log: str
    warnings: tuple[str, ...]
    compiler_version: str | None
    returncode: int


def entry_to_xhtml(
    entry: Entry,
    entry_id: str,
    lookup_aliases: list[str] | None = None,
    title_to_id: dict[str, int] | None = None,
    lookup_value: str | None = None,
) -> str:
    """Render one Kindle dictionary entry with optional hidden lookup forms."""

    title = html.escape(entry.title, quote=True)
    lookup = html.escape(lookup_value or entry.title, quote=True)
    definition = (
        link_definition_references(entry.definition, title_to_id, entry.title)
        if title_to_id
        else sanitize_inline_html(entry.definition)
    )
    url = html.escape(entry.url, quote=True)
    spoiler_note = ""
    if entry.spoiler_notice:
        spoiler_note = (
            f'\n        <p class="spoiler-note"><b>Spoiler note:</b> '
            f"{sanitize_inline_html(entry.spoiler_notice)}</p>"
        )
    detail_items = "\n".join(
        f"          <li><b>{html.escape(label, quote=False)}:</b> {sanitize_inline_html(value)}</li>"
        for label, value in entry.details
    )
    details_block = f"\n{detail_items}" if detail_items else ""
    inflections = ""
    alias_values = [alias for alias in (lookup_aliases or []) if alias.casefold() != entry.title.casefold()]
    if alias_values:
        inflection_items = "\n".join(
            f'            <idx:iform value="{html.escape(alias, quote=True)}" />' for alias in alias_values
        )
        inflections = f"\n          <idx:infl>\n{inflection_items}\n          </idx:infl>"
    return f"""<idx:entry name="default" scriptable="yes" spell="yes" id="entry-{entry_id}">
        <a id="entry-{entry_id}"></a>
        <idx:orth value="{lookup}"><b>{title}</b>{inflections}
        </idx:orth>
        <idx:short>{spoiler_note}
        <ul class="definition">
          <li>{definition}</li>
{details_block}
        </ul>
        <p class="source">Source: <a href="{url}">{title} on Dungeon Crawler Carl Wiki</a></p>
        </idx:short>
      </idx:entry>"""


def alphabet_section_label(title: str) -> str:
    """Return the alphabetic section label for a dictionary entry title."""

    for char in title:
        if char.isalpha():
            return ascii_fold(char).upper()[:1] or char.upper()
        if char.isdigit():
            return "#"
    return "#"


def alphabet_section_to_xhtml(label: str) -> str:
    """Render a simple alphabet section break for Kindle navigation."""

    heading = "0-9" if label == "#" else label
    section_id = "letter-number" if label == "#" else f"letter-{html.escape(label, quote=True)}"
    return f"""      <mbp:pagebreak />
      <h1 class="letter-heading" id="{section_id}">{heading}</h1>"""


def entries_to_xhtml(
    entries: list[Entry],
    lookup_report: LookupReport,
    title_to_id: dict[str, int] | None = None,
) -> str:
    """Render canonical Kindle entries with alphabet page breaks and separators."""

    aliases = lookup_report.aliases
    entries_by_title = {entry.title: entry for entry in entries}
    multi_by_primary_target: dict[str, list[tuple[int, LookupForm]]] = {}
    for lookup_index, lookup in enumerate(lookup_report.multi_target_lookups, 1):
        if lookup.targets:
            multi_by_primary_target.setdefault(lookup.targets[0], []).append((lookup_index, lookup))

    rendered_entries: list[str] = []
    current_label: str | None = None
    for index, entry in enumerate(entries, 1):
        label = alphabet_section_label(entry.title)
        section_heading = ""
        if label != current_label:
            current_label = label
            section_heading = alphabet_section_to_xhtml(label)
        rendered = entry_to_xhtml(entry, str(index), aliases.get(entry.title, []), title_to_id)
        if section_heading:
            rendered = f"{section_heading}\n\n{rendered}"
        rendered_entries.append(rendered)
        for lookup_index, lookup in multi_by_primary_target.get(entry.title, []):
            for target_index, target_title in enumerate(lookup.targets[1:], 1):
                rendered_entries.append(
                    entry_to_xhtml(
                        entries_by_title[target_title],
                        f"{index}-lookup-{lookup_index}-{target_index}",
                        [],
                        title_to_id,
                        lookup.word,
                    )
                )
    return "\n\n      <hr />\n\n".join(rendered_entries)


def write_xhtml(entries: list[Entry], output: Path, title: str) -> None:
    """Write the Kindle dictionary XHTML source file."""

    write_xhtml_with_options(entries, output, title, link_entries=False)


def write_xhtml_with_options(
    entries: list[Entry],
    output: Path,
    title: str,
    link_entries: bool,
    include_sidebar_aliases: bool = True,
) -> LookupReport:
    """Write the Kindle dictionary XHTML source file with build options."""

    lookup_report = build_lookup_report(
        entries,
        include_sidebar_aliases=include_sidebar_aliases,
    )
    title_to_id = {entry.title: index for index, entry in enumerate(entries, 1)} if link_entries else None
    body = entries_to_xhtml(entries, lookup_report, title_to_id)
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
      b idx\\:orth {{ font-size: 1.15em; }}
      ul.definition {{ margin-top: 0.35em; }}
      .letter-heading {{ font-size: 1.1em; }}
      .spoiler-note {{ margin-bottom: 0.35em; }}
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
    return lookup_report


def write_opf(output: Path, title: str, author: str, xhtml_name: str, identifier: str) -> None:
    """Write the OPF package file Kindle tooling compiles."""

    output.write_text(
        f"""<?xml version="1.0" encoding="utf-8"?>
<package unique-identifier="uid">
  <metadata>
    <dc-metadata xmlns:dc="http://purl.org/dc/elements/1.1/"
                 xmlns:opf="http://www.idpf.org/2007/opf">
      <dc:Identifier id="uid">{html.escape(identifier)}</dc:Identifier>
      <dc:Title>{html.escape(title)}</dc:Title>
      <dc:Language>{LANGUAGE}</dc:Language>
      <dc:Creator>{html.escape(author)}</dc:Creator>
      <dc:contributor opf:role="edt" opf:file-as="McGuire, Justin">Justin McGuire</dc:contributor>
      <dc:Subject>Dictionary</dc:Subject>
      <dc:Description>A dictionary for {html.escape(title)}, generated from the fandom wiki page summaries.</dc:Description>
      <dc:Rights>Content is available under CC-BY-SA unless otherwise noted on its linked wiki page.</dc:Rights>
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
  <guide>
    <reference type="index" title="IndexName" href="{html.escape(xhtml_name)}" />
  </guide>
</package>
""",
        encoding="utf-8",
    )


def kindle_identifier(title: str, release_version: str = DEFAULT_RELEASE_VERSION) -> str:
    """Return the deterministic OPF identifier for one Kindle dictionary build."""

    title_component = identifier_component(title)
    version_component = identifier_component(release_version)
    return f"dcdict:{title_component}:{version_component}"


def identifier_component(value: str) -> str:
    """Normalize one identifier segment to stable ASCII words."""

    folded = ascii_fold(normalize_text(value))
    component = re.sub(r"[^A-Za-z0-9.]+", "-", folded).strip("-")
    if not component:
        raise ValueError("Kindle identifier components cannot be empty")
    return component


def validate_xml(path: Path) -> None:
    """Raise if a generated XML/XHTML file is not well-formed."""

    ET.parse(path)


def build_dictionary_sources(
    entries: list[Entry],
    output_dir: Path,
    title: str,
    author: str,
    link_entries: bool = False,
    include_sidebar_aliases: bool = True,
    release_version: str = DEFAULT_RELEASE_VERSION,
) -> BuildResult:
    """Generate and validate Kindle dictionary source files."""

    output_dir.mkdir(parents=True, exist_ok=True)
    xhtml_path = output_dir / "dictionary.xhtml"
    opf_path = output_dir / "dictionary.opf"
    identifier = kindle_identifier(title, release_version)

    lookup_report = write_xhtml_with_options(
        entries,
        xhtml_path,
        title,
        link_entries,
        include_sidebar_aliases=include_sidebar_aliases,
    )
    write_opf(opf_path, title, author, xhtml_path.name, identifier)
    validate_xml(xhtml_path)
    validate_xml(opf_path)

    return BuildResult(
        xhtml_path=xhtml_path,
        opf_path=opf_path,
        entry_count=len(entries),
        alias_count=lookup_report.single_target_alias_count,
        multi_lookup_count=lookup_report.multi_target_lookup_count,
        omitted_alias_count=lookup_report.omitted_alias_count,
    )


def compile_with_kindlegen(
    opf_path: Path,
    *,
    dont_append_source: bool = False,
) -> CompilationResult | None:
    """Compile OPF/XHTML sources into MOBI when kindlegen is available."""

    kindlegen = find_kindlegen()
    if not kindlegen:
        return None
    command = [kindlegen, opf_path.name, "-verbose"]
    if dont_append_source:
        command.append("-dont_append_source")
    mobi_path = opf_path.with_suffix(".mobi")
    mobi_path.unlink(missing_ok=True)
    result = subprocess.run(
        command,
        cwd=opf_path.parent,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    # Legacy kindlegen exits non-zero when it builds with warnings, including
    # expected dictionary warnings. Treat the output file as the success signal.
    if mobi_path.exists():
        log = result.stdout or ""
        warnings = tuple(
            line.strip()
            for line in log.splitlines()
            if line.strip().lower().startswith("warning")
        )
        version_match = re.search(r"kindlegen[^\n]*?\bV([0-9.]+)", log, re.I)
        return CompilationResult(
            output_path=mobi_path,
            compiler_log=log,
            warnings=warnings,
            compiler_version=version_match.group(1) if version_match else None,
            returncode=result.returncode,
        )
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
