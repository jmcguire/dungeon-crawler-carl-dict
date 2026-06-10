"""Shared text helpers for wiki-derived dictionary content."""

from __future__ import annotations

import re


_CITATION_MARKER_RE = re.compile(r"\s*\[\s*\d+\s*\]")


def strip_wiki_reference_markers(text: str) -> str:
    """Remove inline numeric citation markers like ``[1]`` from text."""

    return _CITATION_MARKER_RE.sub("", text)
