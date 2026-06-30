"""Build and inspect Kobo dicthtml dictionaries."""

from __future__ import annotations

import html
import gzip
import shutil
import subprocess
import unicodedata
import zipfile
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable

from fandom_dict.entries import Entry, build_lookup_report, sanitize_inline_html


DICTGEN_OUTPUT_NAME = "dicthtml-dc.zip"
DICTFILE_NAME = "dictionary.df"
ALLOWED_HTML_TAGS = frozenset({"html", "w", "a", "var", "variant", "div", "p", "b", "i", "ul", "li", "br"})


class KoboValidationError(ValueError):
    """Raised when a Kobo dictionary bundle is malformed or incomplete."""


@dataclass(frozen=True)
class KoboBuildResult:
    """Files and counts produced by a Kobo build."""

    dictfile_path: Path
    dictzip_path: Path
    entry_count: int
    alias_count: int
    compiler_log: str
    compiler_version: str | None
    multi_lookup_count: int = 0
    omitted_alias_count: int = 0
    lookup_record_count: int = 0


@dataclass(frozen=True)
class KoboWordEntry:
    """One parsed Kobo dicthtml word entry."""

    prefix: str
    headword: str
    variants: tuple[str, ...]
    html: str


@dataclass(frozen=True)
class KoboInspection:
    """Validated metadata and lookup data from a Kobo dictionary."""

    entries: tuple[KoboWordEntry, ...]
    words_size: int
    checks: tuple[str, ...]

    @property
    def alias_count(self) -> int:
        """Return the number of unique variant lookup names."""

        return len({variant for entry in self.entries for variant in entry.variants})

    def lookup(self, word: str) -> str | None:
        """Return the exact canonical or variant definition for a word."""

        normalized_variant = normalize_kobo_variant(word)
        for entry in self.entries:
            if entry.headword == normalize_kobo_headword(word) or normalized_variant in entry.variants:
                return entry.html
        return None

    def canonical_word(self, word: str) -> str | None:
        """Return the canonical headword selected by an exact lookup."""

        normalized_variant = normalize_kobo_variant(word)
        for entry in self.entries:
            if entry.headword == normalize_kobo_headword(word) or normalized_variant in entry.variants:
                return entry.headword
        return None

    def manifest_data(self) -> dict[str, object]:
        """Return JSON-compatible smoke-test details."""

        return {
            "format": "Kobo dicthtml v2",
            "entry_count": len({entry.headword for entry in self.entries}),
            "alias_count": self.alias_count,
            "words_size": self.words_size,
            "checks": list(self.checks),
        }


def find_dictgen() -> str | None:
    """Return the installed dictgen executable path, if available."""

    return shutil.which("dictgen")


def kobo_prefix(word: str) -> str:
    """Return Kobo's v2 non-Japanese dicthtml shard prefix for a word."""

    chars: list[str] = []
    for char in word:
        if char == "\0" or len(chars) >= 2:
            break
        chars.append(char.lower())
    while chars and chars[0].isspace():
        chars.pop(0)
    while chars and chars[-1].isspace():
        chars.pop()
    if not chars:
        return "11"
    if not _is_cyrillic(chars[0]):
        while len(chars) < 2:
            chars.append("a")
        if not chars[0].isalpha() or not chars[1].isalpha():
            return "11"
    return "".join(chars)


def _is_cyrillic(char: str) -> bool:
    """Return whether a character is in a Unicode Cyrillic block."""

    return "CYRILLIC" in unicodedata.name(char, "")


def normalize_kobo_headword(word: str) -> str:
    """Normalize a Kobo headword reference for ``<a name="..." />``."""

    return word.strip()


def normalize_kobo_variant(word: str) -> str:
    """Normalize a Kobo variant reference for ``<variant name="..." />``."""

    return word.strip().casefold()


def render_definition(entry: Entry, source_name: str = "Dungeon Crawler Carl Wiki") -> str:
    """Render one conservative raw HTML definition for Kobo."""

    chunks = [
        '<div class="entry">',
        f'<p class="headword"><b>{html.escape(entry.title, quote=False)}</b></p>',
    ]
    if entry.spoiler_notice:
        chunks.append(
            '<p class="spoiler-note"><b>Spoiler note:</b> '
            f"{sanitize_inline_html(entry.spoiler_notice)}</p>"
        )
    chunks.append('<ul class="definition">')
    chunks.append(f"<li>{sanitize_inline_html(entry.definition)}</li>")
    for label, value in entry.details:
        chunks.append(
            f"<li><b>{html.escape(label, quote=False)}:</b> "
            f"{sanitize_inline_html(value)}</li>"
        )
    chunks.append("</ul>")
    chunks.append(
        '<p class="source">Source: '
        f"{html.escape(entry.title, quote=False)} on {html.escape(source_name, quote=False)}<br />"
        f"{html.escape(entry.url, quote=False)}</p>"
    )
    chunks.append("</div>")
    return "".join(chunks)


def render_combined_definition(
    targets: tuple[str, ...],
    entries_by_title: dict[str, Entry],
    source_name: str = "Dungeon Crawler Carl Wiki",
) -> str:
    """Render multiple canonical entries as one Kobo lookup result."""

    chunks = ['<div class="multi-lookup">']
    for target in targets:
        chunks.append(render_definition(entries_by_title[target], source_name))
    chunks.append("</div>")
    return "".join(chunks)


def entries_to_dictfile(
    entries: list[Entry],
    *,
    include_sidebar_aliases: bool = True,
    source_name: str = "Dungeon Crawler Carl Wiki",
    title_suffix_aliases: tuple[str, ...] | None = None,
    title_prefix_aliases: tuple[str, ...] | None = None,
    strip_parenthetical_disambiguation: bool = True,
    title_component_ignore_words: tuple[str, ...] = (),
    sidebar_alias_labels: tuple[str, ...] = ("Aliases",),
) -> tuple[str, int, int, int, int]:
    """Render Kobo dictgen input and return it with lookup counts."""

    lookup_options = {
        "include_sidebar_aliases": include_sidebar_aliases,
        "strip_parenthetical_disambiguation": strip_parenthetical_disambiguation,
        "title_component_ignore_words": title_component_ignore_words,
        "sidebar_alias_labels": sidebar_alias_labels,
    }
    if title_suffix_aliases is not None:
        lookup_options["title_suffix_aliases"] = title_suffix_aliases
    if title_prefix_aliases is not None:
        lookup_options["title_prefix_aliases"] = title_prefix_aliases
    lookup_report = build_lookup_report(entries, **lookup_options)
    aliases = lookup_report.aliases
    entries_by_title = {entry.title: entry for entry in entries}
    combined_definitions = {
        lookup.word: render_combined_definition(lookup.targets, entries_by_title, source_name)
        for lookup in lookup_report.multi_target_lookups
    }
    chunks: list[str] = []
    alias_count = 0
    for entry in entries:
        chunks.append(f"@ {entry.title}")
        for alias in aliases[entry.title]:
            if alias != entry.title:
                chunks.append(f"& {alias}")
                alias_count += 1
        chunks.append("::")
        chunks.append(f"<html>{combined_definitions.get(entry.title, render_definition(entry, source_name))}")
        chunks.append("")
    extra_lookup_words = sorted(set(combined_definitions) - set(entries_by_title), key=str.casefold)
    for word in extra_lookup_words:
        chunks.append(f"@ {word}")
        chunks.append("::")
        chunks.append(f"<html>{combined_definitions[word]}")
        chunks.append("")
    return (
        "\n".join(chunks),
        alias_count,
        lookup_report.multi_target_lookup_count,
        lookup_report.omitted_alias_count,
        len(entries) + len(extra_lookup_words),
    )


def detect_dictgen_version(executable: str) -> str | None:
    """Best-effort dictgen version or help banner."""

    result = subprocess.run(
        (executable, "-h"),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    lines = [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]
    for line in lines:
        if line.lower().startswith("version:"):
            return line.removeprefix("Version:").strip()
    return lines[0] if lines else None


def build_kobo(
    entries: list[Entry],
    output_dir: Path,
    *,
    output_name: str = DICTGEN_OUTPUT_NAME,
    include_sidebar_aliases: bool = True,
    source_name: str = "Dungeon Crawler Carl Wiki",
    title_suffix_aliases: tuple[str, ...] | None = None,
    title_prefix_aliases: tuple[str, ...] | None = None,
    strip_parenthetical_disambiguation: bool = True,
    title_component_ignore_words: tuple[str, ...] = (),
    sidebar_alias_labels: tuple[str, ...] = ("Aliases",),
) -> KoboBuildResult:
    """Generate a Kobo dictfile and compile it with dictgen."""

    executable = find_dictgen()
    if not executable:
        raise KoboValidationError("dictgen was not found")
    output_dir.mkdir(parents=True, exist_ok=True)
    dictfile_text, alias_count, multi_lookup_count, omitted_alias_count, lookup_record_count = entries_to_dictfile(
        entries,
        include_sidebar_aliases=include_sidebar_aliases,
        source_name=source_name,
        title_suffix_aliases=title_suffix_aliases,
        title_prefix_aliases=title_prefix_aliases,
        strip_parenthetical_disambiguation=strip_parenthetical_disambiguation,
        title_component_ignore_words=title_component_ignore_words,
        sidebar_alias_labels=sidebar_alias_labels,
    )
    dictfile_path = output_dir / DICTFILE_NAME
    dictfile_path.write_text(dictfile_text, encoding="utf-8")
    dictzip_path = output_dir / output_name
    result = subprocess.run(
        (executable, "-I", "remove", "-o", str(dictzip_path), str(dictfile_path)),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    log = result.stdout or ""
    if result.returncode != 0 or not dictzip_path.is_file():
        detail = log.strip()
        raise KoboValidationError("dictgen failed" + (f":\n{detail}" if detail else ""))
    return KoboBuildResult(
        dictfile_path=dictfile_path,
        dictzip_path=dictzip_path,
        entry_count=len(entries),
        alias_count=alias_count,
        compiler_log=log,
        compiler_version=detect_dictgen_version(executable),
        multi_lookup_count=multi_lookup_count,
        omitted_alias_count=omitted_alias_count,
        lookup_record_count=lookup_record_count,
    )


class _KoboDicthtmlParser(HTMLParser):
    def __init__(self, prefix: str, source: str) -> None:
        super().__init__(convert_charrefs=True)
        self.prefix = prefix
        self.source = source
        self.entries: list[KoboWordEntry] = []
        self._w_depth = 0
        self._headword: str | None = None
        self._variants: list[str] = []
        self._chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._start_tag(tag, attrs, closed=False)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._start_tag(tag, attrs, closed=True)

    def handle_endtag(self, tag: str) -> None:
        if tag not in ALLOWED_HTML_TAGS:
            raise KoboValidationError(f"unsupported Kobo HTML tag: {tag}")
        if self._w_depth:
            self._chunks.append(f"</{tag}>")
            if tag == "w":
                self._save_entry()
            else:
                self._w_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._w_depth:
            self._chunks.append(html.escape(data, quote=False))

    def _start_tag(self, tag: str, attrs: list[tuple[str, str | None]], *, closed: bool) -> None:
        if tag not in ALLOWED_HTML_TAGS:
            raise KoboValidationError(f"unsupported Kobo HTML tag: {tag}")
        attrs_dict = {key: value or "" for key, value in attrs}
        if tag == "w":
            if self._w_depth:
                raise KoboValidationError("nested Kobo <w> entry")
            self._w_depth = 1
            self._headword = None
            self._variants = []
            self._chunks = ["<w>"]
            return
        if not self._w_depth:
            return
        if tag == "a" and "name" in attrs_dict:
            self._headword = normalize_kobo_headword(attrs_dict["name"])
        if tag == "variant" and "name" in attrs_dict:
            self._variants.append(normalize_kobo_variant(attrs_dict["name"]))
        rendered_attrs = "".join(
            f' {key}="{html.escape(value, quote=True)}"' for key, value in attrs if value is not None
        )
        self._chunks.append(f"<{tag}{rendered_attrs}{' /' if closed else ''}>")
        if not closed:
            self._w_depth += 1

    def _save_entry(self) -> None:
        if not self._headword:
            raise KoboValidationError(f"Kobo entry in {self.source} has no headword")
        lookup_words = [self._headword, *self._variants]
        if self.prefix not in {kobo_prefix(word) for word in lookup_words}:
            raise KoboValidationError(f"Kobo entry {self._headword!r} is in the wrong prefix file")
        html_text = "".join(self._chunks)
        if not html_text.strip() or "idx:" in html_text or "<idx" in html_text:
            raise KoboValidationError(f"invalid Kobo definition markup: {self._headword}")
        self.entries.append(
            KoboWordEntry(
                prefix=self.prefix,
                headword=self._headword,
                variants=tuple(self._variants),
                html=html_text,
            )
        )
        self._w_depth = 0
        self._headword = None
        self._variants = []
        self._chunks = []


def _parse_dicthtml(prefix: str, source: str, text: str) -> list[KoboWordEntry]:
    parser = _KoboDicthtmlParser(prefix, source)
    parser.feed(text)
    parser.close()
    return parser.entries


def _decode_dicthtml(data: bytes, name: str) -> str:
    """Return UTF-8 dicthtml, decompressing dictgen gzip members if needed."""

    if data.startswith(b"\x1f\x8b"):
        try:
            data = gzip.decompress(data)
        except OSError as exc:
            raise KoboValidationError(f"Kobo dicthtml gzip member is invalid: {name}") from exc
    try:
        return data.decode("utf-8")
    except UnicodeError as exc:
        raise KoboValidationError(f"Kobo dicthtml is not UTF-8: {name}") from exc


def inspect_kobo(
    dictzip_path: Path,
    *,
    required_headwords: Iterable[str] = (),
) -> KoboInspection:
    """Parse and smoke-test a complete Kobo dictionary zip."""

    if not dictzip_path.is_file():
        raise KoboValidationError(f"Kobo dictzip does not exist: {dictzip_path}")
    try:
        archive = zipfile.ZipFile(dictzip_path)
    except zipfile.BadZipFile as exc:
        raise KoboValidationError(f"invalid Kobo dictzip: {exc}") from exc
    with archive:
        names = archive.namelist()
        if not names:
            raise KoboValidationError("Kobo dictzip is empty")
        if any(name.endswith("/") or "/" in name for name in names):
            raise KoboValidationError("Kobo dictzip files must be top-level only")
        if "words" not in names:
            raise KoboValidationError("Kobo dictzip is missing words index")
        unsupported = [name for name in names if name != "words" and not name.endswith(".html")]
        if unsupported:
            raise KoboValidationError("unsupported Kobo dictzip file: " + ", ".join(unsupported))
        words_size = len(archive.read("words"))
        if words_size == 0:
            raise KoboValidationError("Kobo words index is empty")
        entries: list[KoboWordEntry] = []
        for name in sorted(name for name in names if name.endswith(".html")):
            text = _decode_dicthtml(archive.read(name), name)
            if not text.strip():
                raise KoboValidationError(f"Kobo dicthtml is empty: {name}")
            entries.extend(_parse_dicthtml(Path(name).stem, name, text))
    if not entries:
        raise KoboValidationError("Kobo dictzip contains no entries")
    lookup_words: dict[str, str] = {}
    for entry in entries:
        for word in (entry.headword, *entry.variants):
            key = normalize_kobo_variant(word)
            if key in lookup_words and lookup_words[key] != entry.headword:
                raise KoboValidationError(f"duplicate Kobo lookup word: {word}")
            lookup_words[key] = entry.headword
    inspection = KoboInspection(
        entries=tuple(entries),
        words_size=words_size,
        checks=(
            "valid Kobo zip structure",
            "nonempty words index",
            "UTF-8 dicthtml files",
            "valid Kobo word entries",
            "valid Kobo prefix placement",
            "valid Kobo aliases",
        ),
    )
    for word in required_headwords:
        if inspection.lookup(word) is None:
            raise KoboValidationError(f"required lookup is missing: {word}")
    return inspection


def synthetic_kobo_zip(
    path: Path,
    entries: list[Entry],
    *,
    title_suffix_aliases: tuple[str, ...] | None = None,
    title_prefix_aliases: tuple[str, ...] | None = None,
    title_component_ignore_words: tuple[str, ...] = (),
) -> None:
    """Write a small inspectable Kobo-like zip for tests which do not run dictgen."""

    lookup_options = {"title_component_ignore_words": title_component_ignore_words}
    if title_suffix_aliases is not None:
        lookup_options["title_suffix_aliases"] = title_suffix_aliases
    if title_prefix_aliases is not None:
        lookup_options["title_prefix_aliases"] = title_prefix_aliases
    lookup_report = build_lookup_report(entries, **lookup_options)
    aliases = lookup_report.aliases
    entries_by_title = {entry.title: entry for entry in entries}
    combined_definitions = {
        lookup.word: render_combined_definition(lookup.targets, entries_by_title)
        for lookup in lookup_report.multi_target_lookups
    }
    grouped: dict[str, list[str]] = {}
    words = set(entries_by_title) | set(combined_definitions)

    def add_word(headword: str, definition: str, variants: list[str]) -> None:
        word_html = (
            "<w>"
            f'<p><a name="{html.escape(normalize_kobo_headword(headword), quote=True)}" />'
            f"<b>{html.escape(headword, quote=False)}</b></p>"
            "<var>"
            + "".join(f'<variant name="{html.escape(variant, quote=True)}"/>' for variant in variants)
            + "</var>"
            + definition
            + "</w>"
        )
        prefixes = {kobo_prefix(headword), *(kobo_prefix(variant) for variant in variants)}
        for prefix in prefixes:
            grouped.setdefault(prefix, []).append(word_html)
        words.update(variants)

    for entry in entries:
        variants = [normalize_kobo_variant(alias) for alias in aliases[entry.title] if alias != entry.title]
        definition = combined_definitions.get(entry.title, render_definition(entry))
        add_word(entry.title, definition, variants)
    for word in sorted(set(combined_definitions) - set(entries_by_title), key=str.casefold):
        add_word(word, combined_definitions[word], [])

    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("words", "\n".join(sorted(words, key=str.casefold)).encode("utf-8"))
        for prefix, word_entries in grouped.items():
            archive.writestr(f"{prefix}.html", "<html>" + "".join(word_entries) + "</html>")
