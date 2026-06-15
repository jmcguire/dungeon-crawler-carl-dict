"""Build and inspect StarDict dictionaries for readers such as KOReader."""

from __future__ import annotations

import html
import json
import shutil
import struct
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import cmp_to_key
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable

from dcdict.entries import (
    Entry,
    build_aliases,
    link_definition_references,
    sanitize_inline_html,
)


STARDICT_VERSION = "2.4.2"
BASE_NAME = "Dungeon-Crawler-Carl-Dictionary"
CSS_TEXT = """body { line-height: 1.3; }
.headword { margin: 0 0 0.35em 0; font-size: 1.15em; }
.spoiler-note { margin: 0 0 0.35em 0; }
.definition { margin: 0; padding: 0 1.7em; }
.source { margin-top: 0.6em; font-size: 0.8em; }
"""
ALLOWED_HTML_TAGS = frozenset({"div", "p", "b", "i", "ul", "li", "br", "a"})


class StarDictValidationError(ValueError):
    """Raised when a StarDict bundle is malformed or incomplete."""


@dataclass(frozen=True)
class StarDictBuildResult:
    """Files and counts produced by a StarDict build."""

    ifo_path: Path
    idx_path: Path
    dict_path: Path
    syn_path: Path
    css_path: Path
    entry_count: int
    alias_count: int

    @property
    def files(self) -> tuple[Path, ...]:
        """Return the files that make up the installable dictionary."""

        return (self.ifo_path, self.idx_path, self.dict_path, self.syn_path, self.css_path)


@dataclass(frozen=True)
class StarDictIndexEntry:
    """One parsed canonical StarDict index entry."""

    word: str
    offset: int
    size: int


@dataclass(frozen=True)
class StarDictSynonym:
    """One parsed synonym and its canonical index number."""

    word: str
    original_index: int


@dataclass(frozen=True)
class StarDictInspection:
    """Validated metadata and lookup data from a StarDict bundle."""

    title: str
    entries: tuple[StarDictIndexEntry, ...]
    synonyms: tuple[StarDictSynonym, ...]
    definitions: tuple[str, ...]
    checks: tuple[str, ...]
    sdcv_checked: bool = False

    def lookup(self, word: str) -> str | None:
        """Return the exact canonical or synonym definition for a word."""

        for index, entry in enumerate(self.entries):
            if entry.word == word:
                return self.definitions[index]
        for synonym in self.synonyms:
            if synonym.word == word:
                return self.definitions[synonym.original_index]
        return None

    def canonical_word(self, word: str) -> str | None:
        """Return the canonical headword selected by an exact lookup."""

        for entry in self.entries:
            if entry.word == word:
                return entry.word
        for synonym in self.synonyms:
            if synonym.word == word:
                return self.entries[synonym.original_index].word
        return None

    def manifest_data(self) -> dict[str, object]:
        """Return JSON-compatible smoke-test details."""

        return {
            "format": "StarDict 2.4.2",
            "title": self.title,
            "entry_count": len(self.entries),
            "alias_count": len(self.synonyms),
            "checks": list(self.checks),
            "sdcv_checked": self.sdcv_checked,
        }


def stardict_compare(left: str, right: str) -> int:
    """Implement StarDict's required ``stardict_strcmp`` ordering."""

    left_bytes = left.encode("utf-8")
    right_bytes = right.encode("utf-8")
    left_folded = _ascii_lower(left_bytes)
    right_folded = _ascii_lower(right_bytes)
    if left_folded < right_folded:
        return -1
    if left_folded > right_folded:
        return 1
    if left_bytes < right_bytes:
        return -1
    if left_bytes > right_bytes:
        return 1
    return 0


def _ascii_lower(value: bytes) -> bytes:
    """Lowercase ASCII bytes without applying locale or Unicode rules."""

    return bytes(byte + 32 if 65 <= byte <= 90 else byte for byte in value)


def _sorted_words(values: Iterable[str]) -> list[str]:
    return sorted(values, key=cmp_to_key(stardict_compare))


def render_definition(
    entry: Entry,
    known_titles: set[str],
    *,
    link_entries: bool,
) -> str:
    """Render one conservative HTML definition for KOReader."""

    definition = sanitize_inline_html(entry.definition)
    if link_entries:
        targets = {title: title for title in known_titles}
        definition = link_definition_references(
            definition,
            targets,
            entry.title,
            href_for_target=lambda title, _target: f"bword://{title}",
            accepted_href_prefix="bword://",
        )

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
    chunks.append(f"<li>{definition}</li>")
    for label, value in entry.details:
        chunks.append(
            f"<li><b>{html.escape(label, quote=False)}:</b> "
            f"{sanitize_inline_html(value)}</li>"
        )
    chunks.append("</ul>")
    chunks.append(
        '<p class="source">Source: '
        f"{html.escape(entry.title, quote=False)} on Dungeon Crawler Carl Wiki<br />"
        f"{html.escape(entry.url, quote=False)}</p>"
    )
    chunks.append("</div>")
    return "".join(chunks)


def _validate_headword(word: str) -> None:
    encoded = word.encode("utf-8")
    if not encoded or len(encoded) >= 256 or b"\0" in encoded or "\n" in word or "\r" in word:
        raise StarDictValidationError(f"invalid StarDict headword: {word!r}")


def build_stardict(
    entries: list[Entry],
    output_dir: Path,
    title: str,
    author: str,
    *,
    link_entries: bool = False,
    base_name: str = BASE_NAME,
) -> StarDictBuildResult:
    """Generate a StarDict 2.4.2 bundle from normalized entries."""

    output_dir.mkdir(parents=True, exist_ok=True)
    aliases = build_aliases(entries)
    entries_by_title = {entry.title: entry for entry in entries}
    if len(entries_by_title) != len(entries):
        raise StarDictValidationError("canonical entry titles must be unique")
    folded_titles = [entry.title.casefold() for entry in entries]
    if len(set(folded_titles)) != len(folded_titles):
        raise StarDictValidationError("canonical entry titles must be case-insensitively unique")

    ordered_titles = _sorted_words(entries_by_title)
    known_titles = set(entries_by_title)
    dict_chunks: list[bytes] = []
    index_records: list[tuple[str, int, int]] = []
    offset = 0
    for word in ordered_titles:
        _validate_headword(word)
        definition = render_definition(
            entries_by_title[word],
            known_titles,
            link_entries=link_entries,
        ).encode("utf-8")
        if not definition:
            raise StarDictValidationError(f"empty definition for {word!r}")
        dict_chunks.append(definition)
        index_records.append((word, offset, len(definition)))
        offset += len(definition)
        if offset > 0xFFFFFFFF:
            raise StarDictValidationError("StarDict data exceeds the 32-bit format limit")

    title_to_index = {word: index for index, word in enumerate(ordered_titles)}
    synonym_records = []
    for canonical, forms in aliases.items():
        for alias in forms:
            if alias != canonical:
                _validate_headword(alias)
                synonym_records.append((alias, title_to_index[canonical]))
    synonym_records.sort(key=cmp_to_key(lambda left, right: stardict_compare(left[0], right[0])))

    idx_bytes = b"".join(
        word.encode("utf-8") + b"\0" + struct.pack(">II", record_offset, size)
        for word, record_offset, size in index_records
    )
    syn_bytes = b"".join(
        word.encode("utf-8") + b"\0" + struct.pack(">I", original_index)
        for word, original_index in synonym_records
    )

    ifo_path = output_dir / f"{base_name}.ifo"
    idx_path = output_dir / f"{base_name}.idx"
    dict_path = output_dir / f"{base_name}.dict"
    syn_path = output_dir / f"{base_name}.syn"
    css_path = output_dir / f"{base_name}.css"
    dict_path.write_bytes(b"".join(dict_chunks))
    idx_path.write_bytes(idx_bytes)
    syn_path.write_bytes(syn_bytes)
    css_path.write_text(CSS_TEXT, encoding="utf-8")

    date = datetime.now(timezone.utc).strftime("%Y.%m.%d")
    ifo_path.write_text(
        "\n".join(
            (
                "StarDict's dict ifo file",
                f"version={STARDICT_VERSION}",
                f"wordcount={len(index_records)}",
                f"synwordcount={len(synonym_records)}",
                f"idxfilesize={len(idx_bytes)}",
                f"bookname={title}",
                f"author={author}",
                "website=https://github.com/jmcguire/dungeon-crawler-carl-dict",
                "description=Fan dictionary generated from Dungeon Crawler Carl Wiki contributors; content CC BY-SA 3.0.",
                f"date={date}",
                "sametypesequence=h",
                "lang=en-en",
                "",
            )
        ),
        encoding="utf-8",
    )
    return StarDictBuildResult(
        ifo_path=ifo_path,
        idx_path=idx_path,
        dict_path=dict_path,
        syn_path=syn_path,
        css_path=css_path,
        entry_count=len(index_records),
        alias_count=len(synonym_records),
    )


class _DefinitionHtmlInspector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: list[str] = []
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append(tag)
        if tag not in ALLOWED_HTML_TAGS:
            raise StarDictValidationError(f"unsupported definition HTML tag: {tag}")
        if tag == "a":
            href = next((value for key, value in attrs if key == "href"), None)
            if not href or not href.startswith("bword://"):
                raise StarDictValidationError(f"unsupported dictionary link: {href!r}")
            self.links.append(href.removeprefix("bword://"))


def _parse_ifo(path: Path) -> dict[str, str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise StarDictValidationError(f"could not read StarDict .ifo: {exc}") from exc
    if not lines or lines[0] != "StarDict's dict ifo file":
        raise StarDictValidationError("invalid StarDict .ifo magic")
    fields: dict[str, str] = {}
    for line in lines[1:]:
        if not line:
            continue
        if "=" not in line:
            raise StarDictValidationError(f"invalid StarDict .ifo line: {line!r}")
        key, value = line.split("=", 1)
        fields[key] = value
    return fields


def _parse_idx(data: bytes) -> list[StarDictIndexEntry]:
    entries = []
    cursor = 0
    while cursor < len(data):
        end = data.find(b"\0", cursor)
        if end < 0 or end - cursor >= 256 or end + 9 > len(data):
            raise StarDictValidationError("truncated or invalid StarDict .idx record")
        try:
            word = data[cursor:end].decode("utf-8")
        except UnicodeError as exc:
            raise StarDictValidationError("StarDict .idx headword is not UTF-8") from exc
        _validate_headword(word)
        offset, size = struct.unpack(">II", data[end + 1 : end + 9])
        entries.append(StarDictIndexEntry(word, offset, size))
        cursor = end + 9
    return entries


def _parse_syn(data: bytes) -> list[StarDictSynonym]:
    synonyms = []
    cursor = 0
    while cursor < len(data):
        end = data.find(b"\0", cursor)
        if end < 0 or end - cursor >= 256 or end + 5 > len(data):
            raise StarDictValidationError("truncated or invalid StarDict .syn record")
        try:
            word = data[cursor:end].decode("utf-8")
        except UnicodeError as exc:
            raise StarDictValidationError("StarDict .syn headword is not UTF-8") from exc
        _validate_headword(word)
        (original_index,) = struct.unpack(">I", data[end + 1 : end + 5])
        synonyms.append(StarDictSynonym(word, original_index))
        cursor = end + 5
    return synonyms


def inspect_stardict(
    ifo_path: Path,
    *,
    expected_title: str | None = None,
    required_headwords: Iterable[str] = (),
    require_links: bool = False,
    check_sdcv: bool = True,
) -> StarDictInspection:
    """Parse and smoke-test a complete StarDict bundle."""

    fields = _parse_ifo(ifo_path)
    base = ifo_path.with_suffix("")
    idx_path = base.with_suffix(".idx")
    dict_path = base.with_suffix(".dict")
    syn_path = base.with_suffix(".syn")
    css_path = base.with_suffix(".css")
    for path in (idx_path, dict_path, syn_path, css_path):
        if not path.is_file():
            raise StarDictValidationError(f"missing StarDict component: {path.name}")

    required_fields = {
        "version": STARDICT_VERSION,
        "sametypesequence": "h",
        "lang": "en-en",
    }
    for key, expected in required_fields.items():
        if fields.get(key) != expected:
            raise StarDictValidationError(f"invalid {key}: {fields.get(key)!r}")
    title = fields.get("bookname", "")
    if not title or expected_title is not None and title != expected_title:
        raise StarDictValidationError(f"unexpected StarDict title: {title!r}")

    idx_data = idx_path.read_bytes()
    dict_data = dict_path.read_bytes()
    syn_data = syn_path.read_bytes()
    try:
        wordcount = int(fields["wordcount"])
        synwordcount = int(fields["synwordcount"])
        idxfilesize = int(fields["idxfilesize"])
    except (KeyError, ValueError) as exc:
        raise StarDictValidationError("invalid StarDict count metadata") from exc
    if idxfilesize != len(idx_data):
        raise StarDictValidationError("idxfilesize does not match the .idx file")

    entries = _parse_idx(idx_data)
    synonyms = _parse_syn(syn_data)
    if len(entries) != wordcount or len(synonyms) != synwordcount:
        raise StarDictValidationError("StarDict entry counts do not match the .ifo file")
    if len({entry.word for entry in entries}) != len(entries):
        raise StarDictValidationError("duplicate canonical StarDict headword")
    if len({entry.word.casefold() for entry in entries}) != len(entries):
        raise StarDictValidationError("case-insensitive canonical StarDict collision")
    if entries != sorted(entries, key=cmp_to_key(lambda left, right: stardict_compare(left.word, right.word))):
        raise StarDictValidationError("StarDict .idx is not correctly sorted")
    if synonyms != sorted(synonyms, key=cmp_to_key(lambda left, right: stardict_compare(left.word, right.word))):
        raise StarDictValidationError("StarDict .syn is not correctly sorted")
    if len({synonym.word.casefold() for synonym in synonyms}) != len(synonyms):
        raise StarDictValidationError("duplicate StarDict synonym")

    definitions = []
    canonical_titles = {entry.word for entry in entries}
    saw_link = False
    for entry in entries:
        if entry.size == 0 or entry.offset + entry.size > len(dict_data):
            raise StarDictValidationError(f"definition offset is outside .dict: {entry.word}")
        try:
            definition = dict_data[entry.offset : entry.offset + entry.size].decode("utf-8")
        except UnicodeError as exc:
            raise StarDictValidationError(f"definition is not UTF-8: {entry.word}") from exc
        if not definition.strip() or "idx:" in definition or "<idx" in definition:
            raise StarDictValidationError(f"invalid definition markup: {entry.word}")
        parser = _DefinitionHtmlInspector()
        parser.feed(definition)
        parser.close()
        for target in parser.links:
            saw_link = True
            if target not in canonical_titles:
                raise StarDictValidationError(f"dictionary link target does not exist: {target}")
        definitions.append(definition)

    for synonym in synonyms:
        if synonym.original_index >= len(entries):
            raise StarDictValidationError(f"synonym index is outside .idx: {synonym.word}")
        if synonym.word.casefold() in {word.casefold() for word in canonical_titles}:
            raise StarDictValidationError(f"synonym collides with a canonical headword: {synonym.word}")
    if require_links and not saw_link:
        raise StarDictValidationError("no KOReader internal links were generated")

    inspection = StarDictInspection(
        title=title,
        entries=tuple(entries),
        synonyms=tuple(synonyms),
        definitions=tuple(definitions),
        checks=(
            "StarDict 2.4.2 metadata",
            "sorted canonical index",
            "valid definition offsets",
            "UTF-8 HTML definitions",
            "sorted synonym index",
            "valid synonym targets",
            "valid KOReader internal links" if saw_link else "no internal links requested",
        ),
        sdcv_checked=False,
    )
    for word in required_headwords:
        if inspection.lookup(word) is None:
            raise StarDictValidationError(f"required lookup is missing: {word}")

    sdcv_checked = check_sdcv and _run_sdcv_smoke(ifo_path.parent, title, required_headwords)
    if sdcv_checked:
        inspection = StarDictInspection(
            title=inspection.title,
            entries=inspection.entries,
            synonyms=inspection.synonyms,
            definitions=inspection.definitions,
            checks=(*inspection.checks, "sdcv exact lookup"),
            sdcv_checked=True,
        )
    return inspection


def _run_sdcv_smoke(data_dir: Path, title: str, required_headwords: Iterable[str]) -> bool:
    """Run optional exact lookups through sdcv when it is installed."""

    executable = shutil.which("sdcv")
    words = list(required_headwords)
    if not executable or not words:
        return False
    command = [
        executable,
        "--utf8-input",
        "--utf8-output",
        "--json-output",
        "--non-interactive",
        "--exact-search",
        "--data-dir",
        str(data_dir),
        "-u",
        title,
        "--",
        *words,
    ]
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if result.returncode != 0:
        raise StarDictValidationError(f"sdcv smoke test failed:\n{result.stdout.strip()}")
    output = result.stdout.strip()
    if not output:
        raise StarDictValidationError("sdcv smoke test returned no results")
    for line in output.splitlines():
        try:
            json.loads(line)
        except json.JSONDecodeError as exc:
            raise StarDictValidationError("sdcv returned invalid JSON") from exc
    return True
