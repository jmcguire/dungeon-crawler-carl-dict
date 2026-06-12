import json
import shutil
import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from dcdict.audit_entries import AuditFinding
from dcdict.kindle import (
    DEFAULT_AUTHOR,
    DEFAULT_TITLE,
    CompilationResult,
    Entry,
    build_dictionary_sources,
    compile_with_kindlegen,
    find_kindlegen,
)
from dcdict.mobi import inspect_mobi
from dcdict.release import (
    CHECKSUMS_NAME,
    MANIFEST_NAME,
    MOBI_NAME,
    RELEASE_ASSET_NAMES,
    ZIP_NAME,
    CommandResult,
    ReleaseError,
    Version,
    classify_audit_findings,
    install_release_directory,
    parse_version,
    preflight_local,
    publish_release,
    sha256_file,
    validate_compilation,
    write_checksums,
    write_manifest,
    write_release_zip,
)


VALID_COMPILER_LOG = """Amazon kindlegen(MAC OSX) V2.9 build 0000
Info(prcgen):I1019: Building index into record 0000000 Index name: default
Info(prcgen):I1029: The default lookup index is: "default"
Info(prcgen):I1020: Building inflexions into record 0000005
Info(prcgen):I1021: Building naming index into record 0000008
Info(prcgen):I1041: The file format version is V7
Info(prcgen):I1037: Mobi file built with WARNINGS!
"""


class ReleaseTests(unittest.TestCase):
    def test_parse_version_normalizes_semver_tag(self) -> None:
        self.assertEqual(parse_version("1.2.3"), Version("1.2.3", "v1.2.3"))
        self.assertEqual(
            parse_version("v2.0.0-rc.1+build.4"),
            Version("2.0.0-rc.1+build.4", "v2.0.0-rc.1+build.4"),
        )
        for invalid in ("1.2", "01.2.3", "1.2.3-01", "release-1.2.3"):
            with self.subTest(invalid=invalid), self.assertRaises(ReleaseError):
                parse_version(invalid)

    def test_preflight_rejects_dirty_worktree_and_missing_database(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)

            def dirty_runner(command, cwd):
                if command[1:3] == ("status", "--porcelain"):
                    return CommandResult(0, " M README.md\n")
                return CommandResult(0, "abc123\n")

            with self.assertRaisesRegex(ReleaseError, "worktree must be clean"):
                preflight_local(root, root / "missing.sqlite", dirty_runner)

            def clean_runner(command, cwd):
                return CommandResult(0, "" if command[1] == "status" else "abc123\n")

            with self.assertRaisesRegex(ReleaseError, "does not exist"):
                preflight_local(root, root / "missing.sqlite", clean_runner)

    def test_audit_policy_warns_only_for_short_and_unresolved_forward(self) -> None:
        findings = [
            AuditFinding("short", "Tiny", "Tiny."),
            AuditFinding("unresolved-forward", "Alias", "Missing"),
            AuditFinding("source-artifact", "Broken", "Broken isa thing"),
        ]
        fatal, warnings = classify_audit_findings(findings)
        self.assertEqual([finding.kind for finding in fatal], ["source-artifact"])
        self.assertEqual([finding.kind for finding in warnings], ["short", "unresolved-forward"])

    def test_validate_compilation_requires_dictionary_messages_but_accepts_warning_exit(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "dictionary.mobi"
            path.write_bytes(b"MOBI")
            compilation = CompilationResult(path, VALID_COMPILER_LOG, ("warning",), "2.9", 1)
            validate_compilation(compilation)
            with self.assertRaisesRegex(ReleaseError, "default dictionary index"):
                validate_compilation(
                    CompilationResult(
                        path,
                        VALID_COMPILER_LOG.replace('The default lookup index is: "default"', "lookup omitted"),
                        (),
                        "2.9",
                        0,
                    )
                )

    def test_release_zip_checksums_and_manifest(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            for name in ("NOTICE", "CONTENT_LICENSE", "LICENSE"):
                (root / name).write_text(name, encoding="utf-8")
            mobi = root / MOBI_NAME
            mobi.write_bytes(b"test mobi")
            archive = root / ZIP_NAME
            write_release_zip(archive, mobi, root)
            with zipfile.ZipFile(archive) as bundle:
                self.assertEqual(
                    set(bundle.namelist()),
                    {MOBI_NAME, "INSTALL.txt", "NOTICE", "CONTENT_LICENSE", "LICENSE"},
                )

            manifest = root / MANIFEST_NAME
            compilation = CompilationResult(mobi, VALID_COMPILER_LOG, (), "2.9", 1)
            inspection = mock.Mock()
            inspection.manifest_data.return_value = {"checks": ["MOBI v7"]}
            write_manifest(
                manifest,
                version=Version("1.0.0", "v1.0.0"),
                commit_sha="abc123",
                entry_count=575,
                database_hash="dbhash",
                compilation=compilation,
                inspection=inspection,
                artifact_hashes={MOBI_NAME: sha256_file(mobi), ZIP_NAME: sha256_file(archive)},
            )
            manifest_data = json.loads(manifest.read_text(encoding="utf-8"))
            self.assertEqual(manifest_data["entry_count"], 575)
            self.assertEqual(manifest_data["compiler"]["version"], "2.9")

            checksums = root / CHECKSUMS_NAME
            write_checksums(checksums, (mobi, archive, manifest))
            text = checksums.read_text(encoding="ascii")
            self.assertIn(f"{sha256_file(mobi)}  {MOBI_NAME}", text)
            self.assertIn(MANIFEST_NAME, text)

    def test_install_release_directory_replaces_existing_version(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            final = root / "v1.0.0"
            staged = root / "staged"
            temp_root = root / "temp"
            final.mkdir()
            staged.mkdir()
            temp_root.mkdir()
            (final / "old").write_text("old", encoding="ascii")
            (staged / "new").write_text("new", encoding="ascii")

            install_release_directory(staged, final, temp_root)

            self.assertFalse((final / "old").exists())
            self.assertEqual((final / "new").read_text(encoding="ascii"), "new")
            self.assertFalse((temp_root / "previous-release").exists())

    def test_publish_release_creates_and_verifies_assets(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            release_dir = root / "release"
            release_dir.mkdir()
            for name in RELEASE_ASSET_NAMES:
                (release_dir / name).write_bytes(f"asset:{name}".encode("ascii"))
            commands = []

            def runner(command, cwd):
                command = tuple(command)
                commands.append(command)
                if command[:3] == ("git", "rev-parse", "origin/main"):
                    return CommandResult(0, "abc123\n")
                if command[:3] == ("git", "rev-parse", "--verify"):
                    return CommandResult(1, "")
                if command[:2] == ("git", "ls-remote"):
                    return CommandResult(2, "")
                if command[:3] == ("gh", "release", "view"):
                    return CommandResult(1, "not found")
                if command[:3] == ("gh", "release", "download"):
                    destination = Path(command[command.index("--dir") + 1])
                    for name in RELEASE_ASSET_NAMES:
                        shutil.copy2(release_dir / name, destination / name)
                return CommandResult(0, "ok")

            with mock.patch("dcdict.release.shutil.which", return_value="/usr/local/bin/gh"):
                publish_release(release_dir, Version("1.0.0", "v1.0.0"), "abc123", root, runner)

        create = next(command for command in commands if command[:3] == ("gh", "release", "create"))
        self.assertIn("--latest", create)
        self.assertIn("--generate-notes", create)
        self.assertTrue(any(command[:4] == ("git", "fetch", "origin", "tag") for command in commands))

    @unittest.skipUnless(find_kindlegen(), "Kindle Previewer kindlegen is not installed")
    def test_real_kindlegen_output_passes_mobi_smoke_tests(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            entries = [
                Entry("Carl Spell", "https://example/Carl", "Carl is a crawler and the dictionary's central character."),
                Entry("Donut Box", "https://example/Donut", "Donut is a cat and an accomplished crawler in the dungeon."),
                Entry("Mordecai Spell", "https://example/Mordecai", "Mordecai is an experienced guide who advises the crawlers."),
            ]
            build = build_dictionary_sources(entries, root, DEFAULT_TITLE, DEFAULT_AUTHOR)
            compilation = compile_with_kindlegen(build.opf_path, dont_append_source=True)
            self.assertIsNotNone(compilation)
            assert compilation is not None
            validate_compilation(compilation)

            inspection = inspect_mobi(compilation.output_path, expected_title=DEFAULT_TITLE)

        self.assertEqual(inspection.version, 7)
        self.assertEqual(inspection.title, DEFAULT_TITLE)


if __name__ == "__main__":
    unittest.main()
