"""Inspect classic MOBI dictionary files using only the standard library."""

from __future__ import annotations

import struct
from dataclasses import asdict, dataclass
from pathlib import Path


MIN_REASONABLE_MOBI_SIZE = 1024
PALM_DATABASE_HEADER_SIZE = 78
PALM_RECORD_ENTRY_SIZE = 8
MOBI_HEADER_OFFSET = 16
UNUSED_INDEX = 0xFFFFFFFF


class MobiValidationError(ValueError):
    """Raised when a compiled dictionary fails one or more smoke checks."""


@dataclass(frozen=True)
class MobiInspection:
    """Verified metadata from a classic MOBI dictionary."""

    path: Path
    file_size: int
    record_count: int
    text_record_count: int
    encoding: int
    version: int
    encryption: int
    title: str
    orthographic_index: int
    inflection_index: int
    naming_index: int
    checks: tuple[str, ...]

    def manifest_data(self) -> dict[str, object]:
        """Return JSON-serializable inspection data for a release manifest."""

        data = asdict(self)
        data["path"] = self.path.name
        data["checks"] = list(self.checks)
        return data


def _u16(data: bytes, offset: int) -> int:
    return struct.unpack_from(">H", data, offset)[0]


def _u32(data: bytes, offset: int) -> int:
    return struct.unpack_from(">I", data, offset)[0]


def inspect_mobi(
    path: Path,
    *,
    expected_title: str,
    representative_headwords: tuple[str, ...] = ("Carl", "Donut", "Mordecai"),
) -> MobiInspection:
    """Validate a classic MOBI v7 dictionary and return its key metadata."""

    data = path.read_bytes()
    errors: list[str] = []
    checks: list[str] = []

    if len(data) < MIN_REASONABLE_MOBI_SIZE:
        errors.append(f"file is unexpectedly small ({len(data)} bytes)")
    else:
        checks.append("reasonable file size")

    if len(data) < PALM_DATABASE_HEADER_SIZE:
        raise MobiValidationError("; ".join(errors + ["Palm database header is truncated"]))

    if data[60:68] != b"BOOKMOBI":
        errors.append("BOOKMOBI Palm database signature is missing")
    else:
        checks.append("BOOKMOBI signature")

    record_count = _u16(data, 76)
    table_end = PALM_DATABASE_HEADER_SIZE + record_count * PALM_RECORD_ENTRY_SIZE
    if record_count < 2 or table_end > len(data):
        errors.append("Palm record table is invalid")
        offsets: list[int] = []
    else:
        offsets = [_u32(data, PALM_DATABASE_HEADER_SIZE + index * 8) for index in range(record_count)]
        if offsets != sorted(offsets) or offsets[0] < table_end or offsets[-1] >= len(data):
            errors.append("Palm record offsets are not ordered within the file")
        elif any(left == right for left, right in zip(offsets, offsets[1:])):
            errors.append("Palm record table contains empty records")
        else:
            checks.append("valid Palm record table")

    if not offsets:
        raise MobiValidationError("; ".join(errors))

    first_record_end = offsets[1]
    first_record = data[offsets[0]:first_record_end]
    if len(first_record) < MOBI_HEADER_OFFSET + 92:
        raise MobiValidationError("; ".join(errors + ["first Palm record is truncated"]))
    if first_record[MOBI_HEADER_OFFSET:MOBI_HEADER_OFFSET + 4] != b"MOBI":
        errors.append("first record does not contain a MOBI header")
    else:
        checks.append("MOBI header")

    text_record_count = _u16(first_record, 8)
    encryption = _u16(first_record, 12)
    mobi_length = _u32(first_record, MOBI_HEADER_OFFSET + 4)
    encoding = _u32(first_record, MOBI_HEADER_OFFSET + 12)
    version = _u32(first_record, MOBI_HEADER_OFFSET + 20)
    orthographic_index = _u32(first_record, MOBI_HEADER_OFFSET + 24)
    inflection_index = _u32(first_record, MOBI_HEADER_OFFSET + 28)
    naming_index = _u32(first_record, MOBI_HEADER_OFFSET + 32)

    if mobi_length < 92 or MOBI_HEADER_OFFSET + mobi_length > len(first_record):
        errors.append("MOBI header length is invalid")
    if encoding != 65001:
        errors.append(f"MOBI encoding is {encoding}, expected UTF-8 (65001)")
    else:
        checks.append("UTF-8 encoding")
    if version != 7:
        errors.append(f"MOBI version is {version}, expected 7")
    else:
        checks.append("MOBI v7")
    if encryption != 0:
        errors.append(f"MOBI encryption is enabled ({encryption})")
    else:
        checks.append("encryption disabled")

    if text_record_count < 1 or text_record_count >= record_count:
        errors.append("text record count is invalid")
    else:
        text_offsets = offsets[1:text_record_count + 1]
        text_ends = offsets[2:text_record_count + 1] + [offsets[text_record_count + 1] if text_record_count + 1 < record_count else len(data)]
        if any(end <= start for start, end in zip(text_offsets, text_ends)):
            errors.append("one or more MOBI text records are empty")
        else:
            checks.append("nonempty text records")

    title_offset = _u32(first_record, MOBI_HEADER_OFFSET + 68)
    title_length = _u32(first_record, MOBI_HEADER_OFFSET + 72)
    title_end = title_offset + title_length
    if title_end > len(first_record):
        title = ""
        errors.append("embedded title points outside the first record")
    else:
        try:
            title = first_record[title_offset:title_end].decode("utf-8")
        except UnicodeDecodeError:
            title = ""
            errors.append("embedded title is not valid UTF-8")
    if not title:
        errors.append("embedded title is empty")
    elif title != expected_title:
        errors.append(f"embedded title is {title!r}, expected {expected_title!r}")
    else:
        checks.append("stable embedded title")

    indexes = {
        "orthographic": orthographic_index,
        "naming": naming_index,
    }
    for name, index in indexes.items():
        if index == UNUSED_INDEX or index >= record_count:
            errors.append(f"{name} dictionary index is missing or invalid ({index})")
    if not any("dictionary index" in error for error in errors):
        checks.append("direct dictionary index pointers")

    required_bytes = (b"INDX", b"default") + tuple(word.encode("utf-8") for word in representative_headwords)
    missing = [value.decode("utf-8") for value in required_bytes if value not in data]
    if missing:
        errors.append(f"compiled bytes are missing: {', '.join(missing)}")
    else:
        checks.append("index markers and representative headwords")

    if errors:
        raise MobiValidationError("; ".join(errors))

    return MobiInspection(
        path=path,
        file_size=len(data),
        record_count=record_count,
        text_record_count=text_record_count,
        encoding=encoding,
        version=version,
        encryption=encryption,
        title=title,
        orthographic_index=orthographic_index,
        inflection_index=inflection_index,
        naming_index=naming_index,
        checks=tuple(checks),
    )
