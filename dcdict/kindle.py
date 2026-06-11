"""Load entries and render Kindle dictionary source files."""

from __future__ import annotations

import html
import logging
import re
import shutil
import sqlite3
import subprocess
import sys
import unicodedata
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path

from dcdict.text import clean_wiki_text_artifacts


DEFAULT_TITLE = "Dungeon Crawler Carl Dictionary"
DEFAULT_AUTHOR = "Generated from Dungeon Crawler Carl Wiki contributors"
LANGUAGE = "en-us"
ALLOWED_INLINE_TAGS = {"b": "b", "strong": "b", "i": "i", "em": "i"}
LINKABLE_INLINE_TAGS = {"a": "a", **ALLOWED_INLINE_TAGS}
LOGGER = logging.getLogger(__name__)
SIDEBAR_FIELD_LABELS = {
    "aliases": "Aliases",
    "origin": "Origin",
    "species": "Race",
    "race": "Race",
    "first_appearance": "First scene",
    "source": "Source",
}
BIOGRAPHICAL_FIELD_LABELS = SIDEBAR_FIELD_LABELS


@dataclass(frozen=True)
class Entry:
    """One dictionary headword and its definition."""

    title: str
    url: str
    definition: str
    spoiler_notice: str | None = None
    details: tuple[tuple[str, str], ...] = ()


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


def normalize_inline_html(fragment: str) -> str:
    """Collapse whitespace in safe inline XHTML."""

    return " ".join(fragment.replace("\xa0", " ").split())


class SafeInlineHtmlParser(HTMLParser):
    """Keep only Kindle-safe emphasis tags and escape everything else."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.chunks: list[str] = []
        self._tag_stack: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag not in ALLOWED_INLINE_TAGS:
            return
        kindle_tag = ALLOWED_INLINE_TAGS[tag]
        self.chunks.append(f"<{kindle_tag}>")
        self._tag_stack.append(kindle_tag)

    def handle_endtag(self, tag: str) -> None:
        if tag not in ALLOWED_INLINE_TAGS:
            return
        kindle_tag = ALLOWED_INLINE_TAGS[tag]
        if kindle_tag not in self._tag_stack:
            return
        while self._tag_stack:
            open_tag = self._tag_stack.pop()
            self.chunks.append(f"</{open_tag}>")
            if open_tag == kindle_tag:
                return

    def handle_data(self, data: str) -> None:
        self.chunks.append(html.escape(data, quote=False))

    def close(self) -> None:
        while self._tag_stack:
            self.chunks.append(f"</{self._tag_stack.pop()}>")
        super().close()


class InlineTextParser(HTMLParser):
    """Extract plain text from sanitized inline XHTML."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.chunks: list[str] = []

    def handle_data(self, data: str) -> None:
        self.chunks.append(data)


class LinkedDefinitionParser(HTMLParser):
    """Add internal links to known entry names in safe inline XHTML."""

    def __init__(self, linker: "EntryReferenceLinker") -> None:
        super().__init__(convert_charrefs=True)
        self.linker = linker
        self.chunks: list[str] = []
        self._tag_stack: list[str] = []
        self._inside_link = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag not in LINKABLE_INLINE_TAGS:
            return
        if tag == "a":
            href = next((value for key, value in attrs if key == "href"), None)
            if href and href.startswith("#entry-"):
                self.chunks.append(f'<a href="{html.escape(href, quote=True)}">')
                self._tag_stack.append("a")
                self._inside_link = True
            return
        kindle_tag = LINKABLE_INLINE_TAGS[tag]
        self.chunks.append(f"<{kindle_tag}>")
        self._tag_stack.append(kindle_tag)

    def handle_endtag(self, tag: str) -> None:
        if tag not in LINKABLE_INLINE_TAGS:
            return
        kindle_tag = LINKABLE_INLINE_TAGS[tag]
        if kindle_tag not in self._tag_stack:
            return
        while self._tag_stack:
            open_tag = self._tag_stack.pop()
            self.chunks.append(f"</{open_tag}>")
            if open_tag == "a":
                self._inside_link = False
            if open_tag == kindle_tag:
                return

    def handle_data(self, data: str) -> None:
        if self._inside_link:
            self.chunks.append(html.escape(data, quote=False))
        else:
            self.chunks.append(self.linker.link_text(data))

    def close(self) -> None:
        while self._tag_stack:
            self.chunks.append(f"</{self._tag_stack.pop()}>")
        super().close()


class EntryReferenceLinker:
    """Link known entry names to their anchors inside definition text."""

    def __init__(self, title_to_id: dict[str, int], current_title: str) -> None:
        self.title_to_id = {
            title: entry_id
            for title, entry_id in title_to_id.items()
            if title != current_title and is_linkable_title(title)
        }
        self._linked_titles: set[str] = set()
        self._pattern = compile_title_pattern(self.title_to_id)

    def link_text(self, text: str) -> str:
        """Link the first occurrence of each known target title in a text node."""

        if not self._pattern:
            return html.escape(text, quote=False)

        def replace(match: re.Match[str]) -> str:
            title = match.group(0)
            if title in self._linked_titles:
                return html.escape(title, quote=False)
            self._linked_titles.add(title)
            entry_id = self.title_to_id[title]
            escaped_title = html.escape(title, quote=False)
            return f'<a href="#entry-{entry_id}">{escaped_title}</a>'

        return self._pattern.sub(replace, text)


def sanitize_inline_html(fragment: str) -> str:
    """Return a safe inline XHTML fragment containing only bold/italic tags."""

    parser = SafeInlineHtmlParser()
    parser.feed(fragment)
    parser.close()
    return normalize_inline_html("".join(parser.chunks))


def link_definition_references(
    fragment: str,
    title_to_id: dict[str, int],
    current_title: str,
) -> str:
    """Link entry-title references in a sanitized definition fragment."""

    linker = EntryReferenceLinker(title_to_id, current_title)
    parser = LinkedDefinitionParser(linker)
    parser.feed(sanitize_inline_html(fragment))
    parser.close()
    return normalize_inline_html("".join(parser.chunks))


def is_linkable_title(title: str) -> bool:
    """Return true for titles unlikely to create noisy accidental links."""

    return len(title) >= 4 or any(char in title for char in " -'")


def compile_title_pattern(title_to_id: dict[str, int]) -> re.Pattern[str] | None:
    """Compile a longest-first matcher for known entry titles."""

    if not title_to_id:
        return None
    alternatives = sorted((re.escape(title) for title in title_to_id), key=len, reverse=True)
    return re.compile(r"(?<![\w])(" + "|".join(alternatives) + r")(?![\w])")


def text_from_inline_html(fragment: str) -> str:
    """Return plain text from a sanitized inline XHTML fragment."""

    parser = InlineTextParser()
    parser.feed(fragment)
    parser.close()
    return normalize_text("".join(parser.chunks))


def forwarding_target_from_definition(definition: str) -> str | None:
    """Return the target title for forwarding-only definitions."""

    plain_text = text_from_inline_html(definition)
    patterns = (
        r"See:\s+(.+)",
        r"duplicate page\s*-\s*please see\s+(.+)",
        r"(?:System Message:\s*)?For .+?,\s*please see\s+(.+)",
        r"please see\s+(.+)",
    )
    for pattern in patterns:
        match = re.fullmatch(pattern, plain_text, flags=re.I)
        if match:
            return normalize_text(match.group(1)).rstrip(".")
    return None


class SpoilerNoticeParser(HTMLParser):
    """Extract page-level spoiler notices from Fandom highlight banners."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._capture_depth = 0
        self._chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = {key: value or "" for key, value in attrs}
        classes = attrs_dict.get("class", "")
        if "dcc-highlight" in classes:
            self._capture_depth += 1
        elif self._capture_depth:
            self._capture_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if self._capture_depth:
            self._capture_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._capture_depth:
            self._chunks.append(data)

    @property
    def notice(self) -> str | None:
        text = normalize_text("".join(self._chunks))
        return text if "spoiler" in text.lower() else None


def spoiler_notice_from_html(raw_html: str | None) -> str | None:
    """Extract the source page's spoiler warning, if present."""

    if not raw_html:
        return None
    parser = SpoilerNoticeParser()
    parser.feed(raw_html)
    parser.close()
    return parser.notice


class SidebarInfoParser(HTMLParser):
    """Extract selected approved fields from Fandom sidebars."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.in_infobox = False
        self.current_source: str | None = None
        self._label_depth = 0
        self._value_depth = 0
        self._label_chunks: list[str] = []
        self._value_chunks: list[str] = []
        self.fields: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = {key: value or "" for key, value in attrs}
        classes = attrs_dict.get("class", "")
        source = attrs_dict.get("data-source", "")
        if tag == "aside" and has_class(classes, "portable-infobox"):
            self.in_infobox = True
            return
        if not self.in_infobox:
            return
        if tag == "div" and has_class(classes, "pi-data"):
            if source not in SIDEBAR_FIELD_LABELS:
                return
            self.current_source = source or None
            return
        if self.current_source and tag == "h3" and has_class(classes, "pi-data-label"):
            self._label_depth = 1
            self._label_chunks = []
            return
        if self.current_source and tag == "div" and has_class(classes, "pi-data-value"):
            self._value_depth = 1
            self._value_chunks = []
            return
        if self._label_depth:
            self._label_depth += 1
        if self._value_depth:
            self._value_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag == "aside" and self.in_infobox:
            self.in_infobox = False
            return
        if self._label_depth:
            self._label_depth -= 1
            return
        if self._value_depth:
            self._value_depth -= 1
            if self._value_depth == 0:
                self._save_current_field()
            return

    def handle_data(self, data: str) -> None:
        if self._label_depth:
            self._label_chunks.append(data)
        if self._value_depth:
            self._value_chunks.append(data)

    def _save_current_field(self) -> None:
        if self.current_source in SIDEBAR_FIELD_LABELS:
            value = normalize_text("".join(self._value_chunks))
            if value:
                self.fields.setdefault(self.current_source, value)
        self.current_source = None
        self._label_chunks = []
        self._value_chunks = []


BiographicalInfoParser = SidebarInfoParser


def has_class(classes: str, class_name: str) -> bool:
    """Return true when an HTML class attribute contains a full class token."""

    return class_name in classes.split()


def sidebar_details_from_html(raw_html: str | None) -> tuple[tuple[str, str], ...]:
    """Extract approved non-spoilery sidebar fields."""

    if not raw_html:
        return ()
    parser = SidebarInfoParser()
    parser.feed(raw_html)
    parser.close()
    details = []
    for source in ("aliases", "origin", "species", "race", "first_appearance", "source"):
        if source in parser.fields:
            details.append((SIDEBAR_FIELD_LABELS[source], parser.fields[source]))
    return tuple(details)


def biographical_details_from_html(raw_html: str | None) -> tuple[tuple[str, str], ...]:
    """Compatibility wrapper for the old sidebar details function name."""

    return sidebar_details_from_html(raw_html)


def ascii_fold(text: str) -> str:
    """Return an ASCII-only form for accent-insensitive lookup aliases."""

    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")


def load_entries(db_path: Path, min_definition_length: int) -> list[Entry]:
    """Load usable dictionary entries from the crawler SQLite database."""

    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        """
        SELECT title, url, first_paragraph, raw_html
        FROM pages
        WHERE status = 'ok' AND COALESCE(first_paragraph, '') != ''
        ORDER BY lower(title)
        """
    ).fetchall()
    entries = []
    for title, url, first_paragraph, raw_html in rows:
        definition = sanitize_inline_html(clean_wiki_text_artifacts(first_paragraph))
        details = sidebar_details_from_html(raw_html)
        if len(text_from_inline_html(definition)) >= min_definition_length or forwarding_target_from_definition(definition) or details:
            entries.append(
                Entry(
                    title=normalize_text(title),
                    url=url,
                    definition=definition,
                    spoiler_notice=spoiler_notice_from_html(raw_html),
                    details=details,
                )
            )
    return filter_low_quality_entries(resolve_forwarding_entries(entries))


def is_low_quality_definition(entry: Entry) -> bool:
    """Return true for unusable leftover fragments such as ``Title is``."""

    if entry.details or forwarding_target_from_definition(entry.definition):
        return False
    plain_title = normalize_text(entry.title).lower()
    plain_definition = normalize_text(text_from_inline_html(entry.definition)).lower().rstrip(".")
    return plain_definition in {
        f"{plain_title} is",
        f"{plain_title} was",
        f"{plain_title} are",
    }


def filter_low_quality_entries(entries: list[Entry]) -> list[Entry]:
    """Drop entries that still have no useful dictionary definition."""

    usable_entries = []
    for entry in entries:
        if is_low_quality_definition(entry):
            LOGGER.info("skipped low-quality dictionary entry %s", entry.title)
            continue
        usable_entries.append(entry)
    return usable_entries


def resolve_forwarding_entries(entries: list[Entry]) -> list[Entry]:
    """Copy target definitions into forwarding-only entries when possible."""

    entries_by_title = {entry.title: entry for entry in entries}
    entries_by_casefold_title = {entry.title.casefold(): entry for entry in entries}
    cache: dict[str, Entry | None] = {}

    def lookup_target(title: str) -> Entry | None:
        candidates = [title]
        if title.endswith("."):
            candidates.append(title[:-1])
        for candidate in candidates:
            if candidate in entries_by_title:
                return entries_by_title[candidate]
            if candidate.casefold() in entries_by_casefold_title:
                return entries_by_casefold_title[candidate.casefold()]
        return None

    def resolve_entry(entry: Entry, resolving: set[str]) -> Entry | None:
        if entry.title in cache:
            return cache[entry.title]
        if entry.title in resolving:
            cache[entry.title] = None
            return None

        target_title = forwarding_target_from_definition(entry.definition)
        if not target_title:
            cache[entry.title] = entry
            return entry

        target_entry = lookup_target(target_title)
        if not target_entry:
            cache[entry.title] = entry
            return entry

        resolving.add(entry.title)
        resolved_target = resolve_entry(target_entry, resolving)
        resolving.remove(entry.title)

        if resolved_target is None or forwarding_target_from_definition(resolved_target.definition):
            cache[entry.title] = entry
            return entry

        resolved_entry = Entry(
            title=entry.title,
            url=entry.url,
            definition=resolved_target.definition,
            spoiler_notice=resolved_target.spoiler_notice,
            details=resolved_target.details,
        )
        cache[entry.title] = resolved_entry
        return resolved_entry

    return [resolve_entry(entry, set()) or entry for entry in entries]


def suffix_stripped_alias(title: str, titles: set[str]) -> str | None:
    """Return a lookup alias with a generic suffix removed, when safe."""

    for suffix in (" Spell", " Box"):
        if not title.endswith(suffix):
            continue
        alias = title[: -len(suffix)].strip()
        if alias and alias not in titles:
            return alias
    return None


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
        stripped = suffix_stripped_alias(entry.title, titles)
        if stripped:
            forms.add(stripped)
        first = entry.title.split()[0]
        if first_names.get(first) == 1:
            forms.add(first)
        aliases[entry.title] = sorted(forms, key=lambda value: (value.lower(), value))
    return aliases


def entry_to_xhtml(
    entry: Entry,
    aliases: list[str],
    entry_id: int,
    title_to_id: dict[str, int] | None = None,
) -> str:
    """Render one Kindle dictionary entry with idx lookup metadata."""

    title = html.escape(entry.title, quote=True)
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
    aliases: dict[str, list[str]],
    title_to_id: dict[str, int] | None = None,
) -> str:
    """Render entries with alphabet page breaks and separators."""

    parts: list[str] = []
    current_label: str | None = None
    for index, entry in enumerate(entries, 1):
        label = alphabet_section_label(entry.title)
        if label != current_label:
            current_label = label
            parts.append(alphabet_section_to_xhtml(label))
        parts.append(entry_to_xhtml(entry, aliases[entry.title], index, title_to_id))
        if index < len(entries):
            parts.append("      <hr />")
    return "\n\n".join(parts)


def write_xhtml(entries: list[Entry], output: Path, title: str) -> None:
    """Write the Kindle dictionary XHTML source file."""

    write_xhtml_with_options(entries, output, title, link_entries=False)


def write_xhtml_with_options(
    entries: list[Entry],
    output: Path,
    title: str,
    link_entries: bool,
) -> None:
    """Write the Kindle dictionary XHTML source file with build options."""

    aliases = build_aliases(entries)
    title_to_id = {entry.title: index for index, entry in enumerate(entries, 1)} if link_entries else None
    body = entries_to_xhtml(entries, aliases, title_to_id)
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
    link_entries: bool = False,
) -> BuildResult:
    """Generate and validate Kindle dictionary source files."""

    output_dir.mkdir(parents=True, exist_ok=True)
    xhtml_path = output_dir / "dictionary.xhtml"
    opf_path = output_dir / "dictionary.opf"
    identifier = f"urn:uuid:{uuid.uuid4()}"

    write_xhtml_with_options(entries, xhtml_path, title, link_entries)
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
