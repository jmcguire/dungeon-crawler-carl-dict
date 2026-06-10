"""Shared text helpers for wiki-derived dictionary content."""

from __future__ import annotations

import re


_CITATION_MARKER_RE = re.compile(r"\s*\[\s*\d+\s*\]")
_NAVIGATIONAL_TAIL_RE = re.compile(r"\s+For more information:\s*[^.]+\.?$", re.I)
_TRAILING_ART_CREDIT_RE = re.compile(r"\s+(?:official\s+art|art)\s+by\b.*$", re.I)


def strip_wiki_reference_markers(text: str) -> str:
    """Remove inline numeric citation markers like ``[1]`` from text."""

    return _CITATION_MARKER_RE.sub("", text)


def collapse_whitespace(text: str) -> str:
    """Collapse wiki whitespace and non-breaking spaces into plain text spacing."""

    return " ".join(text.replace("\xa0", " ").split())


def clean_wiki_text_artifacts(text: str) -> str:
    """Remove small source artifacts that do not belong in dictionary prose."""

    text = strip_wiki_reference_markers(text)
    text = re.sub(r"\bisa\b", "is a", text)
    text = re.sub(r"\ba god of\s*\.", "a god.", text, flags=re.I)
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    text = _NAVIGATIONAL_TAIL_RE.sub("", text)
    text = _TRAILING_ART_CREDIT_RE.sub("", text)
    return collapse_whitespace(text)
