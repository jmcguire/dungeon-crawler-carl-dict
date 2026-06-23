import struct
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from fandom_dict.formats.mobi import MobiValidationError, inspect_mobi


TITLE = "Dungeon Crawler Carl Dictionary"


def synthetic_mobi() -> bytes:
    """Build the smallest useful uncompressed Palm/MOBI test fixture."""

    record_count = 3
    first_offset = 104
    second_offset = 904
    third_offset = 1104
    header = bytearray(first_offset)
    header[60:68] = b"BOOKMOBI"
    struct.pack_into(">H", header, 76, record_count)
    for index, offset in enumerate((first_offset, second_offset, third_offset)):
        struct.pack_into(">I", header, 78 + index * 8, offset)

    first = bytearray(800)
    struct.pack_into(">HHIHHHH", first, 0, 1, 0, 100, 1, 4096, 0, 0)
    first[16:20] = b"MOBI"
    struct.pack_into(">I", first, 20, 264)
    struct.pack_into(">I", first, 24, 2)
    struct.pack_into(">I", first, 28, 65001)
    struct.pack_into(">I", first, 36, 7)
    struct.pack_into(">III", first, 40, 2, 0xFFFFFFFF, 2)
    encoded_title = TITLE.encode("utf-8")
    struct.pack_into(">II", first, 84, 668, len(encoded_title))
    first[668:668 + len(encoded_title)] = encoded_title

    text_record = b"dictionary text".ljust(200, b"\0")
    index_record = b"INDX default Carl Donut Mordecai".ljust(300, b"\0")
    return bytes(header + first + text_record + index_record)


class MobiInspectionTests(unittest.TestCase):
    def test_inspect_mobi_accepts_valid_dictionary(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "dictionary.mobi"
            path.write_bytes(synthetic_mobi())

            inspection = inspect_mobi(path, expected_title=TITLE)

        self.assertEqual(inspection.version, 7)
        self.assertEqual(inspection.encoding, 65001)
        self.assertEqual(inspection.encryption, 0)
        self.assertEqual(inspection.title, TITLE)
        self.assertEqual(inspection.inflection_index, 0xFFFFFFFF)
        self.assertIn("direct dictionary index pointers", inspection.checks)

    def test_inspect_mobi_accepts_configured_representative_headwords(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "dictionary.mobi"
            path.write_bytes(synthetic_mobi())

            inspection = inspect_mobi(
                path,
                expected_title=TITLE,
                representative_headwords=("Carl", "Donut"),
            )

        self.assertIn("index markers and representative headwords", inspection.checks)

    def test_inspect_mobi_rejects_bad_signature(self) -> None:
        data = bytearray(synthetic_mobi())
        data[60:68] = b"NOTMOBI!"
        with TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "dictionary.mobi"
            path.write_bytes(data)
            with self.assertRaisesRegex(MobiValidationError, "BOOKMOBI"):
                inspect_mobi(path, expected_title=TITLE)

    def test_inspect_mobi_rejects_wrong_version_and_missing_headword(self) -> None:
        data = bytearray(synthetic_mobi())
        first_offset = struct.unpack_from(">I", data, 78)[0]
        struct.pack_into(">I", data, first_offset + 36, 8)
        data[data.find(b"Mordecai"):data.find(b"Mordecai") + 8] = b"Missing!"
        with TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "dictionary.mobi"
            path.write_bytes(data)
            with self.assertRaisesRegex(MobiValidationError, "version is 8.*Mordecai"):
                inspect_mobi(path, expected_title=TITLE)


if __name__ == "__main__":
    unittest.main()
