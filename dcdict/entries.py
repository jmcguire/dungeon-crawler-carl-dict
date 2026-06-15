"""Load and normalize format-neutral dictionary entries."""

from __future__ import annotations

import html
import logging
import re
import sqlite3
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable, Mapping

from dcdict.text import clean_wiki_text_artifacts


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
    """One dictionary headword and its normalized definition data."""

    title: str
    url: str
    definition: str
    spoiler_notice: str | None = None
    details: tuple[tuple[str, str], ...] = ()


def normalize_text(text: str) -> str:
    """Normalize Unicode and collapse whitespace."""

    text = unicodedata.normalize("NFKC", text.replace("\xa0", " "))
    return " ".join(text.split())


def normalize_inline_html(fragment: str) -> str:
    """Collapse whitespace in safe inline HTML."""

    return " ".join(fragment.replace("\xa0", " ").split())


class SafeInlineHtmlParser(HTMLParser):
    """Keep only safe emphasis tags and escape everything else."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.chunks: list[str] = []
        self._tag_stack: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag not in ALLOWED_INLINE_TAGS:
            return
        safe_tag = ALLOWED_INLINE_TAGS[tag]
        self.chunks.append(f"<{safe_tag}>")
        self._tag_stack.append(safe_tag)

    def handle_endtag(self, tag: str) -> None:
        if tag not in ALLOWED_INLINE_TAGS:
            return
        safe_tag = ALLOWED_INLINE_TAGS[tag]
        if safe_tag not in self._tag_stack:
            return
        while self._tag_stack:
            open_tag = self._tag_stack.pop()
            self.chunks.append(f"</{open_tag}>")
            if open_tag == safe_tag:
                return

    def handle_data(self, data: str) -> None:
        self.chunks.append(html.escape(data, quote=False))

    def close(self) -> None:
        while self._tag_stack:
            self.chunks.append(f"</{self._tag_stack.pop()}>")
        super().close()


class InlineTextParser(HTMLParser):
    """Extract plain text from sanitized inline HTML."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.chunks: list[str] = []

    def handle_data(self, data: str) -> None:
        self.chunks.append(data)


class LinkedDefinitionParser(HTMLParser):
    """Add links to known entry names in safe inline HTML."""

    def __init__(self, linker: "EntryReferenceLinker") -> None:
        super().__init__(convert_charrefs=True)
        self.linker = linker
        self.chunks: list[str] = []
        self._tag_stack: list[str] = []
        self._inside_link = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag not in {"a", *ALLOWED_INLINE_TAGS}:
            return
        if tag == "a":
            href = next((value for key, value in attrs if key == "href"), None)
            if href and self.linker.accepts_existing_href(href):
                self.chunks.append(f'<a href="{html.escape(href, quote=True)}">')
                self._tag_stack.append("a")
                self._inside_link = True
            return
        safe_tag = ALLOWED_INLINE_TAGS[tag]
        self.chunks.append(f"<{safe_tag}>")
        self._tag_stack.append(safe_tag)

    def handle_endtag(self, tag: str) -> None:
        safe_tag = "a" if tag == "a" else ALLOWED_INLINE_TAGS.get(tag)
        if not safe_tag or safe_tag not in self._tag_stack:
            return
        while self._tag_stack:
            open_tag = self._tag_stack.pop()
            self.chunks.append(f"</{open_tag}>")
            if open_tag == "a":
                self._inside_link = False
            if open_tag == safe_tag:
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
    """Link the first occurrence of known entry names in definition text."""

    def __init__(
        self,
        targets: Mapping[str, object],
        current_title: str,
        href_for_target: Callable[[str, object], str] | None = None,
        accepted_href_prefix: str = "#entry-",
    ) -> None:
        self.targets = {
            title: target
            for title, target in targets.items()
            if is_linkable_title(title)
        }
        self.current_title = current_title
        self.href_for_target = href_for_target or (
            lambda _title, target: f"#entry-{target}"
        )
        self.accepted_href_prefix = accepted_href_prefix
        self._linked_titles: set[str] = set()
        self._pattern = compile_title_pattern(self.targets)

    def accepts_existing_href(self, href: str) -> bool:
        """Return whether an existing link belongs to this output format."""

        return href.startswith(self.accepted_href_prefix)

    def link_text(self, text: str) -> str:
        """Link the first occurrence of each target in one text node."""

        if not self._pattern:
            return html.escape(text, quote=False)

        def replace(match: re.Match[str]) -> str:
            title = match.group(0)
            if title == self.current_title or title in self._linked_titles:
                return html.escape(title, quote=False)
            self._linked_titles.add(title)
            href = self.href_for_target(title, self.targets[title])
            return f'<a href="{html.escape(href, quote=True)}">{html.escape(title, quote=False)}</a>'

        return self._pattern.sub(replace, text)


def sanitize_inline_html(fragment: str) -> str:
    """Return safe inline HTML containing only bold and italic tags."""

    parser = SafeInlineHtmlParser()
    parser.feed(fragment)
    parser.close()
    return normalize_inline_html("".join(parser.chunks))


def link_definition_references(
    fragment: str,
    targets: Mapping[str, object],
    current_title: str,
    href_for_target: Callable[[str, object], str] | None = None,
    accepted_href_prefix: str = "#entry-",
) -> str:
    """Link entry-title references using a format-specific target URI."""

    if href_for_target is None:
        href_for_target = lambda _title, target: f"#entry-{target}"
    linker = EntryReferenceLinker(
        targets,
        current_title,
        href_for_target,
        accepted_href_prefix,
    )
    parser = LinkedDefinitionParser(linker)
    parser.feed(sanitize_inline_html(fragment))
    parser.close()
    return normalize_inline_html("".join(parser.chunks))


def is_linkable_title(title: str) -> bool:
    """Return true for titles unlikely to create noisy accidental links."""

    return len(title) >= 4 or any(char in title for char in " -'")


def compile_title_pattern(targets: Mapping[str, object]) -> re.Pattern[str] | None:
    """Compile a longest-first matcher for known entry titles."""

    if not targets:
        return None
    return _compile_title_pattern_cached(tuple(sorted(targets)))


@lru_cache(maxsize=8)
def _compile_title_pattern_cached(titles: tuple[str, ...]) -> re.Pattern[str]:
    """Compile and cache the matcher shared by all entries in one build."""

    alternatives = sorted((re.escape(title) for title in titles), key=len, reverse=True)
    return re.compile(r"(?<![\w])(" + "|".join(alternatives) + r")(?![\w])")


def text_from_inline_html(fragment: str) -> str:
    """Return plain text from sanitized inline HTML."""

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
        classes = dict(attrs).get("class") or ""
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
            self.current_source = source if source in SIDEBAR_FIELD_LABELS else None
            return
        if self.current_source and tag == "h3" and has_class(classes, "pi-data-label"):
            self._label_depth = 1
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

    def handle_data(self, data: str) -> None:
        if self._value_depth:
            self._value_chunks.append(data)

    def _save_current_field(self) -> None:
        if self.current_source in SIDEBAR_FIELD_LABELS:
            value = normalize_text("".join(self._value_chunks))
            if value:
                self.fields.setdefault(self.current_source, value)
        self.current_source = None
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
    """Return an ASCII-only form used for alphabet section labels."""

    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")


def load_entries(db_path: Path, min_definition_length: int) -> list[Entry]:
    """Load usable dictionary entries from the crawler SQLite database."""

    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            """
            SELECT title, url, first_paragraph, raw_html
            FROM pages
            WHERE status = 'ok' AND COALESCE(first_paragraph, '') != ''
            ORDER BY lower(title)
            """
        ).fetchall()
    finally:
        conn.close()
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
        candidates = [title, title[:-1]] if title.endswith(".") else [title]
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
        resolved = Entry(
            title=entry.title,
            url=entry.url,
            definition=resolved_target.definition,
            spoiler_notice=resolved_target.spoiler_notice,
            details=resolved_target.details,
        )
        cache[entry.title] = resolved
        return resolved

    return [resolve_entry(entry, set()) or entry for entry in entries]


def suffix_stripped_alias(title: str, folded_titles: set[str]) -> str | None:
    """Return a lookup alias with a generic suffix removed, when safe."""

    for suffix in (" Spell", " Box"):
        if title.endswith(suffix):
            alias = title[: -len(suffix)].strip()
            if alias and alias.casefold() not in folded_titles:
                return alias
    return None


def build_aliases(entries: list[Entry]) -> dict[str, list[str]]:
    """Build unique aliases for titles ending in Box or Spell."""

    folded_titles = {entry.title.casefold() for entry in entries}
    candidates: dict[str, str] = {}
    owners: dict[str, set[str]] = {}
    for entry in entries:
        alias = suffix_stripped_alias(entry.title, folded_titles)
        if alias:
            candidates[entry.title] = alias
            owners.setdefault(alias.casefold(), set()).add(entry.title)

    aliases: dict[str, list[str]] = {}
    for entry in entries:
        forms = [entry.title]
        alias = candidates.get(entry.title)
        if alias and owners[alias.casefold()] == {entry.title}:
            forms.append(alias)
        aliases[entry.title] = forms
    return aliases
