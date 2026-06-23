"""Extract dictionary-ready summaries from Fandom article HTML."""

from __future__ import annotations

import html as html_lib
import re
from html.parser import HTMLParser

from fandom_dict.text import clean_wiki_text_artifacts, collapse_whitespace


SHORT_DESCRIPTION_THRESHOLD = 100


class FirstParagraphParser(HTMLParser):
    """Extract the first meaningful paragraph while ignoring common chrome."""

    ALLOWED_INLINE_TAGS = {"b": "b", "strong": "b", "i": "i", "em": "i"}
    SUMMARY_SECTIONS = {"intro", "description"}
    SKIP_TAGS = {
        "aside",
        "blockquote",
        "dl",
        "figure",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "ol",
        "pre",
        "script",
        "style",
        "sup",
        "table",
        "ul",
    }
    VOID_TAGS = {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "source",
        "track",
        "wbr",
    }
    INLINE_TAGS = {"a", "b", "cite", "code", "em", "i", "small", "span", "strong"}
    SKIP_CLASSES = (
        "infobox",
        "portable-infobox",
        "toc",
        "mw-editsection",
        "reference",
        "noprint",
        "dcc-highlight",
        "gallery",
        "gallerybox",
        "gallerytext",
        "pi-caption",
    )

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_until: list[str] = []
        self._heading_depth = 0
        self._current_section = "intro"
        self._paragraph_depth = 0
        self._chunks: list[str] = []
        self._loose_chunks: list[str] = []
        self._inline_stack: list[str] = []
        self.blocks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        # Fandom pages can contain malformed or loosely nested generated HTML.
        # Tracking the tag that opened a skipped region is more tolerant than
        # blindly counting every nested start/end tag.
        attrs_dict = {key: value or "" for key, value in attrs}
        if self._skip_until:
            if self._heading_depth and tag == "span" and attrs_dict.get("id"):
                self._current_section = attrs_dict["id"].replace("_", " ").lower()
            if tag == self._skip_until[-1] and tag not in self.VOID_TAGS:
                self._skip_until.append(tag)
            return

        classes = attrs_dict.get("class", "")
        if not self._paragraph_depth and tag not in self.INLINE_TAGS:
            self._finalize_loose_text()
        if tag in {"h2", "h3"}:
            self._heading_depth += 1
        if (
            tag in self.SKIP_TAGS
            or "mw-empty-elt" in classes
            or any(name in classes for name in self.SKIP_CLASSES)
        ):
            if tag not in self.VOID_TAGS:
                self._skip_until.append(tag)
            return
        if tag == "p" and len(self.blocks) < 2:
            self._paragraph_depth += 1
            self._chunks = []
        elif self._paragraph_depth and tag == "br":
            self._chunks.append(" ")
        elif self._paragraph_depth and tag in self.ALLOWED_INLINE_TAGS:
            self._open_inline_tag(tag, self._chunks)
        elif not self._paragraph_depth and tag in self.ALLOWED_INLINE_TAGS:
            self._open_inline_tag(tag, self._loose_chunks)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._paragraph_depth and tag == "br":
            self._chunks.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if self._skip_until:
            if tag == self._skip_until[-1]:
                if tag in {"h2", "h3"} and self._heading_depth:
                    self._heading_depth -= 1
                self._skip_until.pop()
            return
        if self._paragraph_depth and tag in self.ALLOWED_INLINE_TAGS:
            self._close_inline_tag(tag, self._chunks)
            return
        if not self._paragraph_depth and tag in self.ALLOWED_INLINE_TAGS:
            self._close_inline_tag(tag, self._loose_chunks)
            return
        if tag == "p" and self._paragraph_depth:
            self._close_open_inline_tags(self._chunks)
            text = normalize_inline_html("".join(self._chunks))
            plain_text = text_from_inline_html(text)
            self._paragraph_depth -= 1
            self._chunks = []
            if self._accept_summary_block(plain_text):
                self.blocks.append(text)

    def handle_data(self, data: str) -> None:
        if not self._skip_until and self._paragraph_depth and len(self.blocks) < 2:
            self._chunks.append(html_lib.escape(data, quote=False))
        elif not self._skip_until and len(self.blocks) < 2:
            self._loose_chunks.append(html_lib.escape(data, quote=False))

    def close(self) -> None:
        self._finalize_loose_text()
        super().close()

    def _finalize_loose_text(self) -> None:
        """Accept summary text that appears outside paragraph tags."""

        if len(self.blocks) >= 2 or not self._loose_chunks:
            self._loose_chunks = []
            return
        self._close_open_inline_tags(self._loose_chunks)
        text = normalize_inline_html("".join(self._loose_chunks))
        plain_text = text_from_inline_html(text)
        self._loose_chunks = []
        if len(plain_text) >= 20 and self._accept_summary_block(plain_text):
            self.blocks.append(text)

    def _open_inline_tag(self, tag: str, chunks: list[str]) -> None:
        kindle_tag = self.ALLOWED_INLINE_TAGS[tag]
        chunks.append(f"<{kindle_tag}>")
        self._inline_stack.append(kindle_tag)

    def _close_inline_tag(self, tag: str, chunks: list[str]) -> None:
        kindle_tag = self.ALLOWED_INLINE_TAGS[tag]
        if kindle_tag not in self._inline_stack:
            return
        while self._inline_stack:
            open_tag = self._inline_stack.pop()
            chunks.append(f"</{open_tag}>")
            if open_tag == kindle_tag:
                return

    def _close_open_inline_tags(self, chunks: list[str]) -> None:
        while self._inline_stack:
            chunks.append(f"</{self._inline_stack.pop()}>")

    def _accept_summary_block(self, plain_text: str) -> bool:
        """Return true when a block belongs to an intro/description summary."""

        return (
            self._current_section in self.SUMMARY_SECTIONS
            and bool(plain_text)
            and not is_non_summary_paragraph(plain_text)
        )


class InfoboxSummaryParser(HTMLParser):
    """Build a terse fallback summary from portable infobox fields."""

    WANTED_SOURCES = {
        "species": "race/species",
        "race": "race/species",
        "class": "class",
        "occupation": "occupation",
        "origin": "origin",
        "first_appearance": "first scene",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._source_stack: list[str] = []
        self._value_depth = 0
        self._chunks: list[str] = []
        self.values: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Capture values from portable-infobox data blocks."""

        attrs_dict = {key: value or "" for key, value in attrs}
        classes = attrs_dict.get("class", "")
        source = attrs_dict.get("data-source", "")
        if tag == "div" and "pi-data" in classes and source in self.WANTED_SOURCES:
            self._source_stack.append(source)
            return
        if self._source_stack and tag == "div" and "pi-data-value" in classes:
            self._value_depth += 1
            self._chunks = []

    def handle_endtag(self, tag: str) -> None:
        if self._value_depth and tag == "div":
            text = normalize_text("".join(self._chunks))
            source = self._source_stack[-1]
            if text:
                self.values.setdefault(self.WANTED_SOURCES[source], text)
            self._value_depth -= 1
            self._chunks = []
            return
        if self._source_stack and tag == "div":
            self._source_stack.pop()

    def handle_data(self, data: str) -> None:
        if self._value_depth:
            self._chunks.append(data)


def normalize_text(text: str) -> str:
    """Collapse wiki whitespace and non-breaking spaces into plain text spacing."""

    return collapse_whitespace(text)


def normalize_inline_html(fragment: str) -> str:
    """Collapse whitespace in safe inline XHTML while preserving emphasis tags."""

    return " ".join(fragment.replace("\xa0", " ").split())


class InlineTextParser(HTMLParser):
    """Strip safe inline XHTML back to text for filtering and length checks."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.chunks: list[str] = []

    def handle_data(self, data: str) -> None:
        self.chunks.append(data)


def text_from_inline_html(fragment: str) -> str:
    """Return the plain text contained in a safe inline XHTML fragment."""

    parser = InlineTextParser()
    parser.feed(fragment)
    parser.close()
    return normalize_text("".join(parser.chunks))


class InlineHtmlPrefixParser(HTMLParser):
    """Keep a plain-text prefix of safe inline HTML and close open tags."""

    ALLOWED_INLINE_TAGS = {"b", "i"}

    def __init__(self, max_chars: int) -> None:
        super().__init__(convert_charrefs=True)
        self.max_chars = max_chars
        self.count = 0
        self.chunks: list[str] = []
        self.stack: list[str] = []
        self.stopped = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if not self.stopped and tag in self.ALLOWED_INLINE_TAGS:
            self.chunks.append(f"<{tag}>")
            self.stack.append(tag)

    def handle_endtag(self, tag: str) -> None:
        if self.stopped or tag not in self.ALLOWED_INLINE_TAGS or tag not in self.stack:
            return
        while self.stack:
            open_tag = self.stack.pop()
            self.chunks.append(f"</{open_tag}>")
            if open_tag == tag:
                return

    def handle_data(self, data: str) -> None:
        if self.stopped:
            return
        remaining = self.max_chars - self.count
        if remaining <= 0:
            self.stopped = True
            return
        kept = data[:remaining]
        self.chunks.append(html_lib.escape(kept, quote=False))
        self.count += len(kept)
        if len(kept) < len(data):
            self.stopped = True

    def close(self) -> None:
        while self.stack:
            self.chunks.append(f"</{self.stack.pop()}>")
        super().close()


def trim_inline_html_to_plain_length(fragment: str, max_length: int | None) -> str:
    """Trim safe inline HTML at a sentence boundary when it is too long."""

    if not max_length:
        return fragment
    plain_text = text_from_inline_html(fragment)
    if len(plain_text) <= max_length:
        return fragment
    boundary = 0
    for match in re.finditer(r"(?<=[.!?])\s+", plain_text):
        if match.start() + 1 <= max_length:
            boundary = match.start() + 1
        else:
            break
    if boundary < min(80, max_length // 3):
        boundary = max_length
    parser = InlineHtmlPrefixParser(boundary)
    parser.feed(fragment)
    parser.close()
    return re.sub(r"\s+(</[bi]>)", r"\1", normalize_inline_html("".join(parser.chunks)))


def is_stub_like_description(title: str, description: str) -> bool:
    """Return true for broken one-line intros like ``Dwight is``."""

    plain_title = normalize_text(title).lower()
    plain_description = normalize_text(text_from_inline_html(description)).lower().rstrip(".")
    return plain_description == f"{plain_title} is" or plain_description == f"{plain_title} was" or plain_description == f"{plain_title} are"


def ai_description_paragraph_from_html(html: str) -> str:
    """Extract the first paragraph from an ``AI Description`` section, if present."""

    heading = re.search(r"<h2[^>]*>\s*<span[^>]*id=\"AI_Description\"[^>]*>AI Description</span>\s*</h2>", html, re.I)
    if not heading:
        return ""
    section_start = heading.end()
    next_heading = re.search(r"<h2\b", html[section_start:], re.I)
    section_end = section_start + next_heading.start() if next_heading else len(html)
    section_html = re.sub(r"</?blockquote[^>]*>", "", html[section_start:section_end], flags=re.I)
    for match in re.finditer(r"<p\b[^>]*>.*?</p>", section_html, re.I | re.S):
        paragraph = first_paragraph_from_html(match.group(0))
        if not paragraph:
            continue
        plain_text = text_from_inline_html(paragraph)
        if is_ai_statline_paragraph(plain_text):
            continue
        if re.fullmatch(r"<b>[^<]+</b>", paragraph):
            continue
        return paragraph
    return ""


def is_ai_statline_paragraph(text: str) -> bool:
    """Return true for AI Description stat lines rather than prose."""

    lowered = normalize_text(text).lower()
    return bool(
        re.fullmatch(r"(cost|target|duration|range|cooldown|type|source)\s*:.*", lowered)
        or lowered.startswith("environmental factors")
    )


def is_non_summary_paragraph(text: str) -> bool:
    """Return true for wiki boilerplate paragraphs that are not page summaries."""

    lowered = text.lower()
    return (
        lowered.startswith("system message")
        or lowered.startswith("collection of fan art")
        or lowered.startswith("art by ")
        or lowered.startswith("official art by ")
        or lowered.startswith("for more information:")
        or bool(re.match(r"^[\w '&-]+ effect:", lowered))
        or "posting book 9 spoilers" in lowered
        or lowered.startswith("spoilers for book")
        or lowered == "this article or section is a stub. you can help by expanding it."
        or lowered == "this article or section is a candidate for deletion."
    )


def first_paragraph_from_html(html: str) -> str:
    """Extract the first useful article paragraph from parsed wiki HTML."""

    parser = FirstParagraphParser()
    parser.feed(html)
    parser.close()
    return parser.blocks[0] if parser.blocks else ""


def summary_blocks_from_html(html: str) -> list[str]:
    """Extract up to two useful summary blocks from article HTML."""

    parser = FirstParagraphParser()
    parser.feed(html)
    parser.close()
    return parser.blocks


def is_small_description(description: str) -> bool:
    """Return true when a summary is short enough to benefit from expansion."""

    return len(text_from_inline_html(description)) < SHORT_DESCRIPTION_THRESHOLD


def is_truncated_description(description: str) -> bool:
    """Return true when a summary appears to end mid-sentence."""

    return bool(re.search(r"\b(and|or|but|because|with|of|to)\s*$", text_from_inline_html(description), re.I))


def is_generic_small_description(title: str, description: str) -> bool:
    """Return true for tiny boilerplate summaries that AI prose can improve."""

    plain_title = normalize_text(title).lower()
    plain_description = normalize_text(text_from_inline_html(description)).lower().rstrip(".")
    generic_forms = {
        f"{plain_title} is a spell",
        f"{plain_title} is an item",
        f"{plain_title} is a loot box",
        f"{plain_title} is a box",
        f"{plain_title} are loot boxes",
    }
    return len(plain_description) < SHORT_DESCRIPTION_THRESHOLD and plain_description in generic_forms


def lowercase_first_text_character(fragment: str) -> str:
    """Lowercase the first visible character in a safe inline HTML fragment."""

    return re.sub(
        r"^((?:<[^>]+>|\s)*)([A-Z])",
        lambda match: f"{match.group(1)}{match.group(2).lower()}",
        fragment,
        count=1,
    )


def expand_small_description(summary: str, blocks: list[str]) -> str:
    """Append one more useful text block when the initial summary is very short."""

    if not summary or not (is_small_description(summary) or is_truncated_description(summary)) or len(blocks) < 2:
        return summary
    next_block = lowercase_first_text_character(blocks[1]) if is_truncated_description(summary) else blocks[1]
    return normalize_inline_html(f"{summary} {next_block}")


def summary_from_infobox(title: str, html: str) -> str:
    """Build a short fallback summary from portable-infobox fields."""

    parser = InfoboxSummaryParser()
    parser.feed(html)
    parser.close()
    if not parser.values:
        return ""
    parts = [
        f"{html_lib.escape(label, quote=False)}: {html_lib.escape(value, quote=False)}"
        for label, value in parser.values.items()
    ]
    return f"{html_lib.escape(title, quote=False)}: {'; '.join(parts)}."


def summary_from_html(title: str, html: str, max_summary_length: int | None = None) -> str:
    """Extract a page summary, falling back to infobox fields when needed."""

    blocks = summary_blocks_from_html(html)
    summary = blocks[0] if blocks else ""
    if summary:
        ai_summary = ""
        if (
            is_stub_like_description(title, summary)
            or is_generic_small_description(title, summary)
            or is_truncated_description(summary)
        ):
            ai_summary = ai_description_paragraph_from_html(html)
        summary = ai_summary or expand_small_description(summary, blocks)
    if not summary:
        summary = summary_from_infobox(title, html)
    return clean_wiki_text_artifacts(trim_inline_html_to_plain_length(summary, max_summary_length))


def extract_summary_status(title: str, html: str, max_summary_length: int | None = None) -> tuple[str, str]:
    """Classify a page as usable or empty based on extracted summary content."""

    summary = summary_from_html(title, html, max_summary_length)
    if summary:
        return "ok", summary
    return "empty", ""
