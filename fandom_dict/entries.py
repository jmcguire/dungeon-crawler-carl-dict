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

from fandom_dict.config import SidebarField
from fandom_dict.text import clean_wiki_text_artifacts, strip_wiki_reference_markers


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
DEFAULT_SIDEBAR_FIELDS = tuple(
    SidebarField(source=source, label=label, alias=(label == "Aliases"))
    for source, label in SIDEBAR_FIELD_LABELS.items()
)
CHARACTER_CATEGORY = "Characters"
# Character-only possessives are intentionally hardcoded for now. If another
# fandom needs this on a different category, we can lift it into config later.
PLURAL_ALIAS_CATEGORIES = {"Groups", "Items", "Mob_Types", "Races"}
PLURAL_ITEM_FINAL_WORDS = {
    "Badge",
    "Bandage",
    "Beanie",
    "Biscuit",
    "Bolt",
    "Box",
    "Card",
    "Chest",
    "Chip",
    "Cloak",
    "Condom",
    "Guide",
    "Grenade",
    "Kit",
    "Pass",
    "Patch",
    "Pen",
    "Potion",
    "Scroll",
    "Sigil",
    "Tab",
    "Table",
    "Tattoo",
    "Ticket",
    "Workshop",
}
PLURAL_GROUP_FINAL_WORDS = {
    "Army",
    "Club",
    "Company",
    "Corporation",
    "Court",
    "Empire",
    "Front",
    "Guild",
    "Militia",
    "Sultanate",
    "Syndicate",
}
PLURAL_SKIP_FINAL_WORDS = {
    "Blue",
    "Chee",
    "Controls",
    "Daughters",
    "Experience",
    "Limited",
    "Male",
    "Series",
}
IRREGULAR_PLURAL_FORMS = {
    "Dwarf": ("Dwarfs", "Dwarves"),
    "Elf": ("Elves",),
}
TITLE_COMPONENT_STOP_WORDS = {
    "a",
    "about",
    "all",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "been",
    "being",
    "by",
    "can",
    "did",
    "do",
    "does",
    "for",
    "from",
    "have",
    "how",
    "i",
    "in",
    "into",
    "is",
    "it",
    "it's",
    "its",
    "just",
    "my",
    "not",
    "of",
    "on",
    "or",
    "out",
    "that",
    "the",
    "then",
    "these",
    "this",
    "those",
    "to",
    "was",
    "what",
    "when",
    "where",
    "who",
    "why",
    "with",
    "you",
    "your",
}
FIRST_NAME_SKIP_WORDS = {
    "A",
    "An",
    "Captain",
    "Commander",
    "Count",
    "Crown",
    "Doctor",
    "Dr",
    "Duchess",
    "Duke",
    "King",
    "Lady",
    "Lord",
    "Prince",
    "Princess",
    "Queen",
    "Ser",
    "Sir",
    "The",
}


@dataclass(frozen=True)
class Entry:
    """One dictionary headword and its normalized definition data."""

    title: str
    url: str
    definition: str
    spoiler_notice: str | None = None
    details: tuple[tuple[str, str], ...] = ()
    source_categories: tuple[str, ...] = ()
    redirect_aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class AliasOmission:
    """One alias candidate intentionally omitted from lookup indexes."""

    alias: str
    target: str
    source: str
    reason: str


@dataclass(frozen=True)
class AliasReport:
    """Accepted aliases and rejected alias candidates for one entry set."""

    aliases: dict[str, list[str]]
    omissions: tuple[AliasOmission, ...] = ()

    @property
    def accepted_alias_count(self) -> int:
        """Return the number of non-canonical aliases accepted."""

        return sum(max(0, len(forms) - 1) for forms in self.aliases.values())

    @property
    def omitted_alias_count(self) -> int:
        """Return the number of alias candidates rejected."""

        return len(self.omissions)


@dataclass(frozen=True)
class LookupForm:
    """One lookup word and the canonical entries it should show."""

    word: str
    targets: tuple[str, ...]

    @property
    def is_multi_target(self) -> bool:
        """Return whether this lookup word should show multiple definitions."""

        return len(self.targets) > 1


@dataclass(frozen=True)
class LookupReport:
    """Resolved lookup words, aliases, and omitted candidates for one entry set."""

    aliases: dict[str, list[str]]
    multi_target_lookups: tuple[LookupForm, ...] = ()
    omissions: tuple[AliasOmission, ...] = ()

    @property
    def single_target_alias_count(self) -> int:
        """Return accepted non-canonical aliases with one target."""

        return sum(max(0, len(forms) - 1) for forms in self.aliases.values())

    @property
    def multi_target_lookup_count(self) -> int:
        """Return lookup words that intentionally show multiple entries."""

        return len(self.multi_target_lookups)

    @property
    def omitted_alias_count(self) -> int:
        """Return the number of alias candidates rejected."""

        return len(self.omissions)


@dataclass(frozen=True)
class _AliasCandidate:
    target: str
    alias: str
    source: str

    @property
    def is_title_rule(self) -> bool:
        """Return whether this alias came from a trusted title-shape rule."""

        return self.source.startswith("title-")

    @property
    def allows_multi_target_lookup(self) -> bool:
        """Return whether collisions should become multi-definition lookups."""

        return self.is_title_rule or self.source in {
            "category-plural",
            "character-first-name",
            "character-possessive",
            "bold-intro",
            "description-leading-article",
            "description-parenthetical",
            "wiki-redirect",
        }


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


class InlineAliasParser(HTMLParser):
    """Extract plain text plus bold phrases from sanitized inline HTML."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.chunks: list[str] = []
        self.bold_phrases: list[str] = []
        self._bold_depth = 0
        self._bold_chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if ALLOWED_INLINE_TAGS.get(tag) == "b":
            if self._bold_depth == 0:
                self._bold_chunks = []
            self._bold_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if ALLOWED_INLINE_TAGS.get(tag) != "b" or self._bold_depth == 0:
            return
        self._bold_depth -= 1
        if self._bold_depth == 0:
            phrase = normalize_text("".join(self._bold_chunks))
            if phrase:
                self.bold_phrases.append(phrase)
            self._bold_chunks = []

    def handle_data(self, data: str) -> None:
        self.chunks.append(data)
        if self._bold_depth:
            self._bold_chunks.append(data)


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

        chunks: list[str] = []
        last_end = 0
        for match in self._pattern.finditer(text):
            chunks.append(html.escape(text[last_end : match.start()], quote=False))
            chunks.append(replace(match))
            last_end = match.end()
        chunks.append(html.escape(text[last_end:], quote=False))
        return "".join(chunks)


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

    def __init__(self, sidebar_fields: tuple[SidebarField, ...] = DEFAULT_SIDEBAR_FIELDS) -> None:
        super().__init__(convert_charrefs=True)
        self.field_labels = {field.source: field.label for field in sidebar_fields}
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
            self.current_source = source if source in self.field_labels else None
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
        if self.current_source in self.field_labels:
            value = normalize_text(strip_wiki_reference_markers("".join(self._value_chunks)))
            if value:
                self.fields.setdefault(self.current_source, value)
        self.current_source = None
        self._value_chunks = []


BiographicalInfoParser = SidebarInfoParser


def has_class(classes: str, class_name: str) -> bool:
    """Return true when an HTML class attribute contains a full class token."""

    return class_name in classes.split()


def sidebar_details_from_html(
    raw_html: str | None,
    sidebar_fields: tuple[SidebarField, ...] = DEFAULT_SIDEBAR_FIELDS,
) -> tuple[tuple[str, str], ...]:
    """Extract approved non-spoilery sidebar fields."""

    if not raw_html:
        return ()
    parser = SidebarInfoParser(sidebar_fields)
    parser.feed(raw_html)
    parser.close()
    details = []
    for field in sidebar_fields:
        source = field.source
        if source in parser.fields:
            details.append((field.label, parser.fields[source]))
    return tuple(details)


def biographical_details_from_html(raw_html: str | None) -> tuple[tuple[str, str], ...]:
    """Compatibility wrapper for the old sidebar details function name."""

    return sidebar_details_from_html(raw_html)


def ascii_fold(text: str) -> str:
    """Return an ASCII-only form used for alphabet section labels."""

    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")


def parse_source_categories(value: str | None) -> tuple[str, ...]:
    """Parse the crawler's comma-separated source-category field."""

    if not value:
        return ()
    categories = []
    seen = set()
    for chunk in value.split(","):
        category = normalize_text(chunk)
        if category.startswith("Category:"):
            category = category.removeprefix("Category:").strip()
        if category and category.casefold() not in seen:
            seen.add(category.casefold())
            categories.append(category)
    return tuple(categories)


def load_entries(
    db_path: Path,
    min_definition_length: int,
    *,
    sidebar_fields: tuple[SidebarField, ...] = DEFAULT_SIDEBAR_FIELDS,
    strip_parenthetical_disambiguation: bool = True,
    max_summary_length: int | None = None,
) -> list[Entry]:
    """Load usable dictionary entries from the crawler SQLite database."""

    conn = sqlite3.connect(db_path)
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(pages)").fetchall()}
        source_category_expression = "source_category" if "source_category" in columns else "NULL AS source_category"
        redirect_aliases = load_redirect_aliases(conn)
        rows = conn.execute(
            f"""
            SELECT title, url, first_paragraph, raw_html, {source_category_expression}
            FROM pages
            WHERE status = 'ok' AND COALESCE(first_paragraph, '') != ''
            ORDER BY lower(title)
            """
        ).fetchall()
    finally:
        conn.close()
    entries = []
    for title, url, first_paragraph, raw_html, source_category in rows:
        definition = sanitize_inline_html(clean_wiki_text_artifacts(first_paragraph))
        if max_summary_length:
            from fandom_dict.extraction import trim_inline_html_to_plain_length

            definition = trim_inline_html_to_plain_length(definition, max_summary_length)
        details = sidebar_details_from_html(raw_html, sidebar_fields)
        if len(text_from_inline_html(definition)) >= min_definition_length or forwarding_target_from_definition(definition) or details:
            entries.append(
                Entry(
                    title=normalize_text(title),
                    url=url,
                    definition=definition,
                    spoiler_notice=spoiler_notice_from_html(raw_html),
                    details=details,
                    source_categories=parse_source_categories(source_category),
                    redirect_aliases=redirect_aliases.get(normalize_text(title).casefold(), ()),
                )
            )
    entries = apply_title_munging(entries, strip_parenthetical_disambiguation=strip_parenthetical_disambiguation)
    return filter_low_quality_entries(resolve_forwarding_entries(entries))


def load_redirect_aliases(conn: sqlite3.Connection) -> dict[str, tuple[str, ...]]:
    """Return stored redirect aliases grouped by target title casefold."""

    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()}
    if "redirects" not in tables:
        return {}
    rows = conn.execute(
        """
        SELECT source_title, target_title
        FROM redirects
        WHERE status = 'ok'
          AND COALESCE(source_title, '') != ''
          AND COALESCE(target_title, '') != ''
        ORDER BY lower(source_title)
        """
    ).fetchall()
    grouped: dict[str, list[str]] = {}
    for source_title, target_title in rows:
        source = normalize_text(source_title)
        target = normalize_text(target_title)
        if source and target:
            grouped.setdefault(target.casefold(), []).append(source)
    return {target: tuple(dict.fromkeys(aliases)) for target, aliases in grouped.items()}


def apply_title_munging(entries: list[Entry], *, strip_parenthetical_disambiguation: bool) -> list[Entry]:
    """Apply collision-safe display title cleanup to loaded entries."""

    if not strip_parenthetical_disambiguation:
        return entries
    folded_titles = {entry.title.casefold() for entry in entries}
    stripped_counts: dict[str, int] = {}
    stripped_titles: dict[str, str] = {}
    for entry in entries:
        stripped = strip_parenthetical_suffix(entry.title)
        if not stripped or stripped == entry.title:
            continue
        folded = stripped.casefold()
        stripped_counts[folded] = stripped_counts.get(folded, 0) + 1
        stripped_titles[entry.title] = stripped

    munged: list[Entry] = []
    for entry in entries:
        stripped = stripped_titles.get(entry.title)
        if stripped and stripped_counts[stripped.casefold()] == 1 and stripped.casefold() not in folded_titles:
            munged.append(
                Entry(
                    title=stripped,
                    url=entry.url,
                    definition=entry.definition,
                    spoiler_notice=entry.spoiler_notice,
                    details=entry.details,
                    source_categories=entry.source_categories,
                    redirect_aliases=entry.redirect_aliases,
                )
            )
        else:
            munged.append(entry)
    return munged


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
            source_categories=entry.source_categories,
            redirect_aliases=entry.redirect_aliases,
        )
        cache[entry.title] = resolved
        return resolved

    return [resolve_entry(entry, set()) or entry for entry in entries]


TITLE_SUFFIX_ALIASES = (
    (" Spell", "title-suffix-spell"),
    (" Box", "title-suffix-box"),
    (" Achievement", "title-suffix-achievement"),
    (" Potion", "title-suffix-potion"),
    (" Scroll", "title-suffix-scroll"),
)
TITLE_PREFIX_ALIASES = (
    ("Potion of ", "title-prefix-potion-of"),
    ("Scroll of ", "title-prefix-scroll-of"),
    ("Ring of ", "title-prefix-ring-of"),
    ("Wand of ", "title-prefix-wand-of"),
)


def strip_parenthetical_suffix(title: str) -> str | None:
    """Return ``title`` without a trailing parenthetical disambiguator."""

    stripped = re.sub(r"\s+\([^()]{1,120}\)\s*$", "", title).strip()
    return stripped if stripped and stripped != title else None


def title_alias_candidates(
    title: str,
    *,
    suffixes: tuple[str, ...] = tuple(suffix for suffix, _source in TITLE_SUFFIX_ALIASES),
    prefixes: tuple[str, ...] = tuple(prefix for prefix, _source in TITLE_PREFIX_ALIASES),
    strip_parenthetical_disambiguation: bool = True,
) -> list[_AliasCandidate]:
    """Return trusted title-shape aliases derived directly from the headword."""

    candidates: list[_AliasCandidate] = []
    suffix_sources = {suffix: source for suffix, source in TITLE_SUFFIX_ALIASES}
    prefix_sources = {prefix: source for prefix, source in TITLE_PREFIX_ALIASES}
    for suffix in suffixes:
        if title.endswith(suffix):
            alias = title[: -len(suffix)].strip()
            if alias:
                source = suffix_sources.get(suffix, "title-suffix-custom")
                candidates.append(_AliasCandidate(title, alias, source))
    for prefix in prefixes:
        if title.startswith(prefix):
            alias = title[len(prefix) :].strip()
            if alias:
                source = prefix_sources.get(prefix, "title-prefix-custom")
                candidates.append(_AliasCandidate(title, alias, source))
    if strip_parenthetical_disambiguation:
        alias = strip_parenthetical_suffix(title)
        if alias:
            candidates.append(_AliasCandidate(title, alias, "title-parenthetical"))
    return candidates


def title_component_alias_candidates(
    title: str,
    *,
    suffixes: tuple[str, ...],
    prefixes: tuple[str, ...],
    strip_parenthetical_disambiguation: bool,
    component_ignore_words: tuple[str, ...],
    conflict_counts: Mapping[str, int],
) -> list[_AliasCandidate]:
    """Return a conservative single-token fallback alias from one title."""

    base = title_component_base_title(
        title,
        suffixes=suffixes,
        prefixes=prefixes,
        strip_parenthetical_disambiguation=strip_parenthetical_disambiguation,
    )
    ignored = {word.casefold() for word in component_ignore_words}
    ignored.update(TITLE_COMPONENT_STOP_WORDS)
    tokens = [
        token
        for token in title_component_tokens(base)
        if token.casefold() not in ignored and component_token_is_usable(token)
    ]
    if not tokens or len(tokens) >= 3:
        return []
    if len(tokens) == 1:
        return [_AliasCandidate(title, tokens[0], "title-component")]
    alias = min(enumerate(tokens), key=lambda item: (conflict_counts.get(item[1].casefold(), 0), item[0]))[1]
    return [_AliasCandidate(title, alias, "title-component")]


def title_component_base_title(
    title: str,
    *,
    suffixes: tuple[str, ...],
    prefixes: tuple[str, ...],
    strip_parenthetical_disambiguation: bool,
) -> str:
    """Return a title with configured wrapper words removed for component scoring."""

    base = title
    if strip_parenthetical_disambiguation:
        base = strip_parenthetical_suffix(base) or base
    for prefix in prefixes:
        if base.casefold().startswith(prefix.casefold()):
            base = base[len(prefix) :].strip()
            break
    for suffix in suffixes:
        if base.casefold().endswith(suffix.casefold()):
            base = base[: -len(suffix)].strip()
            break
    return normalize_text(base)


def title_component_tokens(title: str) -> list[str]:
    """Return simple title tokens that are candidates for one-word fallbacks."""

    return re.findall(r"[A-Za-z0-9][A-Za-z0-9'\u2019-]*", title)


def component_token_is_usable(token: str) -> bool:
    """Return whether a title token is safe to consider as a standalone alias."""

    if len(token) < 2:
        return False
    if "'" in token or "\u2019" in token:
        return False
    if not (token[0].isupper() or token[0].isdigit()):
        return False
    return bool(re.search(r"[A-Za-z0-9]", token))


def title_component_conflict_counts(
    entries: list[Entry],
    raw_candidates: list[_AliasCandidate],
) -> Mapping[str, int]:
    """Count existing title tokens and single-word lookup forms for alias scoring."""

    targets_by_word: dict[str, set[str]] = {}
    for entry in entries:
        for token in title_component_tokens(entry.title):
            targets_by_word.setdefault(token.casefold(), set()).add(entry.title)
    for candidate in raw_candidates:
        alias = normalize_text(candidate.alias)
        usable, _reason = alias_candidate_is_usable(alias)
        if usable and len(alias.split()) == 1:
            targets_by_word.setdefault(alias.casefold(), set()).add(candidate.target)
    return {word: len(targets) for word, targets in targets_by_word.items()}


def suffix_stripped_alias(title: str, folded_titles: set[str] | None = None) -> str | None:
    """Compatibility wrapper returning the first legacy suffix-based alias."""

    del folded_titles
    for candidate in title_alias_candidates(title):
        if candidate.source in {"title-suffix-spell", "title-suffix-box"}:
            return candidate.alias
    return None


def inline_alias_data(fragment: str) -> tuple[str, list[str]]:
    """Return plain text and bold phrases from sanitized inline HTML."""

    parser = InlineAliasParser()
    parser.feed(sanitize_inline_html(fragment))
    parser.close()
    return normalize_text("".join(parser.chunks)), parser.bold_phrases


def description_alias_candidates(entry: Entry) -> list[_AliasCandidate]:
    """Infer alias candidates from the opening definition text and emphasis."""

    plain_text, bold_phrases = inline_alias_data(entry.definition)
    candidates: list[_AliasCandidate] = []
    for alias in parenthetical_aliases_from_intro(plain_text, bold_phrases):
        candidates.append(_AliasCandidate(entry.title, alias, "description-parenthetical"))
    if bold_phrases and bold_phrases[0].casefold() != entry.title.casefold():
        candidates.append(_AliasCandidate(entry.title, bold_phrases[0], "bold-intro"))
    candidates.extend(leading_article_title_candidates(entry, plain_text, bold_phrases))
    return candidates


def parenthetical_aliases_from_intro(text: str, bold_phrases: list[str]) -> list[str]:
    """Extract aliases from recognized intro parentheticals."""

    intro = first_sentence_for_aliases(text)
    aliases: list[str] = []
    for parenthetical in re.findall(r"\(([^)]{1,160})\)", intro):
        match = re.match(r"\s*(?:or|aka)\s+(.+?)\s*$", parenthetical, flags=re.I)
        if not match:
            match = re.match(
                r'\s*actually named\s+["“]([^"”]+)["”]',
                parenthetical,
                flags=re.I,
            )
        if not match:
            match = re.match(
                r'\s*shortened to\s+["“]([^"”]+)["”]',
                parenthetical,
                flags=re.I,
            )
        if not match:
            continue
        alias = preferred_bold_alias(match.group(1), bold_phrases)
        if alias:
            aliases.append(alias)
    return aliases


def first_sentence_for_aliases(text: str) -> str:
    """Return the first sentence-ish span used for conservative alias inference."""

    match = re.search(r"(?<=[.!?])\s+", text)
    if not match:
        return text[:500]
    return text[: match.start() + 1]


def preferred_bold_alias(candidate: str, bold_phrases: list[str]) -> str:
    """Prefer a bold phrase contained in a recognized alias expression."""

    candidate = clean_alias_text(candidate)
    folded_candidate = candidate.casefold()
    for phrase in bold_phrases:
        if phrase.casefold() in folded_candidate:
            return phrase
    return candidate


def clean_alias_text(alias: str) -> str:
    """Normalize one automatically discovered alias string."""

    alias = html.unescape(alias)
    alias = alias.strip().strip("'\"“”")
    return normalize_text(alias)


def leading_article_title_candidates(entry: Entry, text: str, bold_phrases: list[str]) -> list[_AliasCandidate]:
    """Return article-included title aliases seen at the start of a definition."""

    if not bold_phrases or bold_phrases[0].casefold() != entry.title.casefold():
        return []
    pattern = rf"^(The|A|An)\s+{re.escape(entry.title)}\b"
    match = re.match(pattern, text, flags=re.I)
    if not match:
        return []
    alias = normalize_text(f"{match.group(1)} {entry.title}")
    return [_AliasCandidate(entry.title, alias, "description-leading-article")]


def sidebar_alias_candidates(entry: Entry, alias_labels: tuple[str, ...] = ("Aliases",)) -> list[_AliasCandidate]:
    """Return filtered alias candidates from one entry's sidebar details."""

    alias_label_set = set(alias_labels)
    candidates: list[_AliasCandidate] = []
    for label, value in entry.details:
        if label not in alias_label_set:
            continue
        for alias in split_sidebar_aliases(value):
            candidates.append(_AliasCandidate(entry.title, alias, "sidebar"))
            stripped = strip_leading_article(alias)
            if stripped != alias:
                candidates.append(_AliasCandidate(entry.title, stripped, "sidebar-leading-article"))
    return candidates


def strip_leading_article(alias: str) -> str:
    """Remove a leading English article from a discovered alias."""

    return normalize_text(re.sub(r"^(?:the|a|an)\s+", "", alias, count=1, flags=re.I))


def split_sidebar_aliases(value: str) -> list[str]:
    """Split a wiki sidebar alias field into conservative alias candidates."""

    value = value.replace(";", ",").replace("|", ",")
    value = re.sub(r"\s+or\s+", ",", value, flags=re.I)
    aliases = []
    for chunk in value.split(","):
        alias = normalize_text(chunk)
        if alias:
            aliases.append(alias)
    return aliases


def human_name_alias_candidates(entry: Entry) -> list[_AliasCandidate]:
    """Return conservative first/last-name aliases for likely human entries."""

    if not is_likely_human_name_entry(entry):
        return []
    first, last = entry.title.split()
    return [
        _AliasCandidate(entry.title, first, "human-name"),
        _AliasCandidate(entry.title, last, "human-name"),
    ]


def is_likely_human_name_entry(entry: Entry) -> bool:
    """Return whether an entry is safe for first/last-name aliases."""

    words = entry.title.split()
    if len(words) != 2:
        return False
    if any(len(word) < 3 or not re.fullmatch(r"[A-Z][A-Za-z'-]*", word) for word in words):
        return False
    race_values = [value for label, value in entry.details if label == "Race"]
    return any("human" in value.casefold() for value in race_values)


def character_first_name_alias_candidates(entry: Entry) -> list[_AliasCandidate]:
    """Return first-name aliases for simple titles from the Characters category."""

    if not entry_is_from_characters_category(entry):
        return []
    words = entry.title.split()
    if len(words) != 2:
        return []
    first, last = words
    if first in FIRST_NAME_SKIP_WORDS:
        return []
    if not (is_person_name_token(first) and is_person_name_token(last)):
        return []
    return [_AliasCandidate(entry.title, first, "character-first-name")]


def character_possessive_alias_candidates(entry: Entry) -> list[_AliasCandidate]:
    """Return possessive lookup forms for Characters entries and their first names."""

    if not entry_is_from_characters_category(entry):
        return []
    bases = [entry.title]
    bases.extend(candidate.alias for candidate in character_first_name_alias_candidates(entry))
    candidates: list[_AliasCandidate] = []
    seen: set[str] = set()
    for base in bases:
        for alias in possessive_lookup_forms(base):
            folded = alias.casefold()
            if folded not in seen:
                candidates.append(_AliasCandidate(entry.title, alias, "character-possessive"))
                seen.add(folded)
    return candidates


def category_plural_alias_candidates(entry: Entry) -> list[_AliasCandidate]:
    """Return conservative plural lookup forms for race, mob, item, and group entries."""

    if not entry_has_any_source_category(entry, PLURAL_ALIAS_CATEGORIES):
        return []
    categories = normalized_source_categories(entry)
    final_word = final_pluralizable_word(entry.title)
    if not final_word:
        return []
    if "items" in categories and final_word not in PLURAL_ITEM_FINAL_WORDS:
        return []
    if "items" not in categories and re.search(r"\bof\b", entry.title, flags=re.I):
        return []
    if "groups" in categories and final_word not in PLURAL_GROUP_FINAL_WORDS:
        return []
    forms = plural_lookup_forms(entry.title)
    return [_AliasCandidate(entry.title, alias, "category-plural") for alias in forms]


def redirect_alias_candidates(entry: Entry) -> list[_AliasCandidate]:
    """Return alias candidates imported from stored wiki redirects."""

    return [_AliasCandidate(entry.title, alias, "wiki-redirect") for alias in entry.redirect_aliases]


def entry_is_from_characters_category(entry: Entry) -> bool:
    """Return true when an entry was reached from the Characters category."""

    return entry_has_any_source_category(entry, {CHARACTER_CATEGORY})


def entry_has_any_source_category(entry: Entry, categories: set[str]) -> bool:
    """Return true when an entry was reached from any category in ``categories``."""

    wanted = {category.casefold() for category in categories}
    return bool(normalized_source_categories(entry) & wanted)


def normalized_source_categories(entry: Entry) -> set[str]:
    """Return normalized source category names without the MediaWiki prefix."""

    normalized = set()
    for category in entry.source_categories:
        value = category.strip()
        if value.casefold().startswith("category:"):
            value = value.split(":", 1)[1]
        if value:
            normalized.add(value.casefold())
    return normalized


def is_person_name_token(value: str) -> bool:
    """Return true for a conservative single name token."""

    if len(value) < 2 or not value[0].isupper():
        return False
    return all(char.isalpha() or char in "'-" for char in value)


def possessive_lookup_forms(value: str) -> tuple[str, ...]:
    """Return conservative ASCII and curly-apostrophe possessive lookup forms."""

    normalized = normalize_text(value)
    if not normalized:
        return ()
    forms = [f"{normalized}'s", f"{normalized}\u2019s"]
    if normalized.endswith(("s", "S")):
        forms.extend((f"{normalized}'", f"{normalized}\u2019"))
    ordered: list[str] = []
    seen: set[str] = set()
    for form in forms:
        folded = form.casefold()
        if folded not in seen:
            ordered.append(form)
            seen.add(folded)
    return tuple(ordered)


def plural_lookup_forms(title: str) -> tuple[str, ...]:
    """Return conservative plural aliases by pluralizing the final title word."""

    normalized = normalize_text(title)
    final_word = final_pluralizable_word(normalized)
    if not final_word:
        return ()
    plural_words = IRREGULAR_PLURAL_FORMS.get(final_word) or (regular_plural_form(final_word),)
    forms = []
    prefix = normalized[: -len(final_word)]
    for plural_word in plural_words:
        alias = f"{prefix}{plural_word}"
        if alias.casefold() != normalized.casefold():
            forms.append(alias)
    return tuple(dict.fromkeys(forms))


def final_pluralizable_word(title: str) -> str | None:
    """Return the final simple word in a title when it is safe to pluralize."""

    if "(" in title or ")" in title or "," in title or ":" in title:
        return None
    words = title.split()
    if not words:
        return None
    final_word = words[-1].strip("'\"")
    if final_word in PLURAL_SKIP_FINAL_WORDS:
        return None
    if not re.fullmatch(r"[A-Z][A-Za-z'-]*", final_word):
        return None
    if final_word.endswith("ii"):
        return None
    if final_word.endswith(("s", "S")) and final_word not in IRREGULAR_PLURAL_FORMS:
        return None
    return final_word


def regular_plural_form(word: str) -> str:
    """Pluralize one simple English-ish title word."""

    if re.search(r"[^aeiou]y$", word, flags=re.I):
        return f"{word[:-1]}ies"
    if re.search(r"(?:s|x|z|ch|sh)$", word, flags=re.I):
        return f"{word}es"
    return f"{word}s"


def alias_candidate_is_usable(alias: str) -> tuple[bool, str]:
    """Validate an alias candidate before collision checks."""

    if not alias:
        return False, "empty"
    if len(alias) < 2:
        return False, "too-short"
    if re.search(r"\[\d+\]", alias):
        return False, "citation-marker"
    if "(" in alias or ")" in alias:
        return False, "parenthetical-note"
    if ":" in alias or "://" in alias:
        return False, "not-a-name"
    if '"' in alias or "“" in alias or "”" in alias:
        return False, "quoted-noise"
    if not (alias[0].isupper() or alias[0].isdigit()):
        return False, "not-title-like"
    return True, ""


def build_lookup_report(
    entries: list[Entry],
    *,
    include_sidebar_aliases: bool = True,
    include_human_name_aliases: bool = True,
    title_suffix_aliases: tuple[str, ...] = tuple(suffix for suffix, _source in TITLE_SUFFIX_ALIASES),
    title_prefix_aliases: tuple[str, ...] = tuple(prefix for prefix, _source in TITLE_PREFIX_ALIASES),
    strip_parenthetical_disambiguation: bool = True,
    title_component_ignore_words: tuple[str, ...] = (),
    sidebar_alias_labels: tuple[str, ...] = ("Aliases",),
) -> LookupReport:
    """Build lookup forms from titles, aliases, and collision-safe groups."""

    folded_titles = {entry.title.casefold(): entry.title for entry in entries}
    raw_candidates: list[_AliasCandidate] = []
    omissions: list[AliasOmission] = []
    for entry in entries:
        raw_candidates.extend(
            title_alias_candidates(
                entry.title,
                suffixes=title_suffix_aliases,
                prefixes=title_prefix_aliases,
                strip_parenthetical_disambiguation=strip_parenthetical_disambiguation,
            )
        )
        raw_candidates.extend(description_alias_candidates(entry))
        if include_sidebar_aliases:
            raw_candidates.extend(sidebar_alias_candidates(entry, sidebar_alias_labels))
        raw_candidates.extend(character_first_name_alias_candidates(entry))
        raw_candidates.extend(character_possessive_alias_candidates(entry))
        raw_candidates.extend(category_plural_alias_candidates(entry))
        raw_candidates.extend(redirect_alias_candidates(entry))
        if include_human_name_aliases:
            raw_candidates.extend(human_name_alias_candidates(entry))
    if title_component_ignore_words:
        conflict_counts = title_component_conflict_counts(entries, raw_candidates)
        for entry in entries:
            raw_candidates.extend(
                title_component_alias_candidates(
                    entry.title,
                    suffixes=title_suffix_aliases,
                    prefixes=title_prefix_aliases,
                    strip_parenthetical_disambiguation=strip_parenthetical_disambiguation,
                    component_ignore_words=title_component_ignore_words,
                    conflict_counts=conflict_counts,
                )
            )

    candidate_map: dict[tuple[str, str], _AliasCandidate] = {}
    canonical_collisions: dict[str, tuple[str, dict[str, str]]] = {}
    alias_collisions: dict[str, tuple[str, dict[str, str]]] = {}
    for candidate in raw_candidates:
        alias = normalize_text(candidate.alias)
        usable, reason = alias_candidate_is_usable(alias)
        omission = AliasOmission(alias, candidate.target, candidate.source, reason)
        if not usable:
            omissions.append(omission)
            continue
        if alias.casefold() == candidate.target.casefold():
            omissions.append(AliasOmission(alias, candidate.target, candidate.source, "self-alias"))
            continue
        canonical_collision = folded_titles.get(alias.casefold())
        if canonical_collision and canonical_collision != candidate.target:
            display_alias, targets = canonical_collisions.setdefault(
                alias.casefold(),
                (alias, {canonical_collision.casefold(): canonical_collision}),
            )
            targets[candidate.target.casefold()] = candidate.target
            continue
        key = (candidate.target, alias.casefold())
        if key not in candidate_map:
            candidate_map[key] = _AliasCandidate(candidate.target, alias, candidate.source)

    grouped_candidates: dict[str, list[_AliasCandidate]] = {}
    for candidate in candidate_map.values():
        grouped_candidates.setdefault(candidate.alias.casefold(), []).append(candidate)

    accepted: dict[str, list[str]] = {entry.title: [] for entry in entries}
    for alias_key, grouped in grouped_candidates.items():
        if len(grouped) == 1:
            candidate = grouped[0]
            accepted[candidate.target].append(candidate.alias)
            continue
        if all(candidate.allows_multi_target_lookup for candidate in grouped):
            display_alias, targets = alias_collisions.setdefault(
                alias_key,
                (grouped[0].alias, {}),
            )
            for candidate in grouped:
                targets[candidate.target.casefold()] = candidate.target
            continue
        for candidate in grouped:
            omissions.append(
                AliasOmission(
                    candidate.alias,
                    candidate.target,
                    candidate.source,
                    "alias-collision",
                )
            )

    multi_target_lookups: list[LookupForm] = []
    for _folded_alias, (alias, target_map) in canonical_collisions.items():
        canonical_title = folded_titles[alias.casefold()]
        targets = [canonical_title]
        targets.extend(
            title
            for title in sorted(target_map.values(), key=lambda value: value.casefold())
            if title.casefold() != canonical_title.casefold()
        )
        multi_target_lookups.append(LookupForm(alias, tuple(targets)))
    for _folded_alias, (alias, target_map) in alias_collisions.items():
        targets = sorted(target_map.values(), key=lambda value: value.casefold())
        multi_target_lookups.append(LookupForm(alias, tuple(targets)))
    multi_target_lookups.sort(key=lambda lookup: lookup.word.casefold())

    aliases: dict[str, list[str]] = {}
    for entry in entries:
        forms = [entry.title]
        seen = {entry.title.casefold()}
        for alias in accepted[entry.title]:
            if alias.casefold() not in seen:
                forms.append(alias)
                seen.add(alias.casefold())
        aliases[entry.title] = forms
    return LookupReport(
        aliases=aliases,
        multi_target_lookups=tuple(multi_target_lookups),
        omissions=tuple(omissions),
    )


def build_alias_report(
    entries: list[Entry],
    *,
    include_sidebar_aliases: bool = True,
    include_human_name_aliases: bool = True,
    title_suffix_aliases: tuple[str, ...] = tuple(suffix for suffix, _source in TITLE_SUFFIX_ALIASES),
    title_prefix_aliases: tuple[str, ...] = tuple(prefix for prefix, _source in TITLE_PREFIX_ALIASES),
    strip_parenthetical_disambiguation: bool = True,
    title_component_ignore_words: tuple[str, ...] = (),
    sidebar_alias_labels: tuple[str, ...] = ("Aliases",),
) -> AliasReport:
    """Build unique single-target aliases from titles, descriptions, sidebars, and names."""

    report = build_lookup_report(
        entries,
        include_sidebar_aliases=include_sidebar_aliases,
        include_human_name_aliases=include_human_name_aliases,
        title_suffix_aliases=title_suffix_aliases,
        title_prefix_aliases=title_prefix_aliases,
        strip_parenthetical_disambiguation=strip_parenthetical_disambiguation,
        title_component_ignore_words=title_component_ignore_words,
        sidebar_alias_labels=sidebar_alias_labels,
    )
    return AliasReport(aliases=report.aliases, omissions=report.omissions)


def build_aliases(
    entries: list[Entry],
    *,
    include_sidebar_aliases: bool = True,
    include_human_name_aliases: bool = True,
    title_suffix_aliases: tuple[str, ...] = tuple(suffix for suffix, _source in TITLE_SUFFIX_ALIASES),
    title_prefix_aliases: tuple[str, ...] = tuple(prefix for prefix, _source in TITLE_PREFIX_ALIASES),
    strip_parenthetical_disambiguation: bool = True,
    sidebar_alias_labels: tuple[str, ...] = ("Aliases",),
) -> dict[str, list[str]]:
    """Build unique lookup aliases for dictionary entries."""

    return build_alias_report(
        entries,
        include_sidebar_aliases=include_sidebar_aliases,
        include_human_name_aliases=include_human_name_aliases,
        title_suffix_aliases=title_suffix_aliases,
        title_prefix_aliases=title_prefix_aliases,
        strip_parenthetical_disambiguation=strip_parenthetical_disambiguation,
        sidebar_alias_labels=sidebar_alias_labels,
    ).aliases
