import json
import shutil
import sqlite3
import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from dcdict.audit_entries import AuditFinding
from dcdict.badges import CoverageResult, build_badges, parse_version as parse_badge_version, write_badge_files
from dcdict.kindle import (
    DEFAULT_AUTHOR,
    DEFAULT_TITLE,
    BuildResult,
    CompilationResult,
    Entry,
    build_dictionary_sources,
    compile_with_kindlegen,
    find_kindlegen,
)
from dcdict.mobi import inspect_mobi
from dcdict.release import (
    ALL_FORMATS,
    CHECKSUMS_NAME,
    DICTGEN_OUTPUT_NAME,
    KOBO_ZIP_NAME,
    MANIFEST_NAME,
    MOBI_NAME,
    RELEASE_ASSET_NAMES,
    STARDICT_ZIP_NAME,
    ZIP_NAME,
    CommandResult,
    ReleaseError,
    Version,
    classify_audit_findings,
    install_release_directory,
    package_release,
    parse_args,
    parse_version,
    preflight_local,
    publish_release,
    sha256_file,
    validate_compilation,
    write_checksums,
    write_manifest,
    write_kobo_zip,
    write_release_zip,
    write_stardict_zip,
)
from dcdict.stardict import build_stardict
from dcdict.kobo import KoboBuildResult, synthetic_kobo_zip


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

    def test_stardict_only_preflight_does_not_require_kindlegen(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            database = root / "entries.sqlite"
            conn = sqlite3.connect(database)
            conn.execute("CREATE TABLE pages (title TEXT)")
            conn.execute("INSERT INTO pages VALUES ('Carl')")
            conn.commit()
            conn.close()

            def runner(command, cwd):
                if command[1:3] == ("status", "--porcelain"):
                    return CommandResult(0, "")
                return CommandResult(0, "abc123\n")

            with mock.patch("dcdict.release.find_kindlegen", return_value=None), mock.patch(
                "dcdict.release.find_dictgen", return_value=None
            ):
                commit = preflight_local(
                    root,
                    database,
                    runner,
                    formats=frozenset({"stardict"}),
                )
                self.assertEqual(commit, "abc123")
                with self.assertRaisesRegex(ReleaseError, "kindlegen"):
                    preflight_local(root, database, runner, formats=frozenset({"kindle"}))
                with self.assertRaisesRegex(ReleaseError, "dictgen"):
                    preflight_local(root, database, runner, formats=frozenset({"kobo"}))

    def test_release_cli_defaults_to_all_formats(self) -> None:
        args = parse_args(["--version", "1.2.3", "--link-entries"])
        self.assertEqual(args.format, "all")
        self.assertTrue(args.link_entries)
        self.assertEqual(ALL_FORMATS, frozenset({"kindle", "stardict", "kobo"}))
        self.assertEqual(parse_args(["--version", "1.2.3", "--format", "kobo"]).format, "kobo")

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

            stardict_build = build_stardict(
                [Entry("Carl", "https://example/Carl", "Carl is a crawler.")],
                root / "stardict",
                DEFAULT_TITLE,
                DEFAULT_AUTHOR,
            )
            stardict_archive = root / STARDICT_ZIP_NAME
            write_stardict_zip(stardict_archive, stardict_build, root)
            with zipfile.ZipFile(stardict_archive) as bundle:
                prefix = "Dungeon-Crawler-Carl-Dictionary/"
                self.assertEqual(
                    set(bundle.namelist()),
                    {
                        prefix + "Dungeon-Crawler-Carl-Dictionary.ifo",
                        prefix + "Dungeon-Crawler-Carl-Dictionary.idx",
                        prefix + "Dungeon-Crawler-Carl-Dictionary.dict",
                        prefix + "Dungeon-Crawler-Carl-Dictionary.syn",
                        prefix + "Dungeon-Crawler-Carl-Dictionary.css",
                        prefix + "INSTALL-KOREADER.txt",
                        prefix + "NOTICE",
                        prefix + "CONTENT_LICENSE",
                        prefix + "LICENSE",
                    },
                )
                instructions = bundle.read(prefix + "INSTALL-KOREADER.txt").decode("utf-8")
                self.assertIn("Manage dictionaries", instructions)
                self.assertIn("Set dictionary priority for this book", instructions)

            kobo_dir = root / "kobo"
            kobo_dir.mkdir()
            kobo_dictzip = kobo_dir / DICTGEN_OUTPUT_NAME
            synthetic_kobo_zip(kobo_dictzip, [Entry("Carl", "https://example/Carl", "Carl is a crawler.")])
            kobo_build = KoboBuildResult(
                dictfile_path=kobo_dir / "dictionary.df",
                dictzip_path=kobo_dictzip,
                entry_count=1,
                alias_count=0,
                compiler_log="",
                compiler_version="dictgen",
            )
            kobo_archive = root / KOBO_ZIP_NAME
            write_kobo_zip(kobo_archive, kobo_build, root)
            with zipfile.ZipFile(kobo_archive) as bundle:
                self.assertEqual(
                    set(bundle.namelist()),
                    {DICTGEN_OUTPUT_NAME, "INSTALL-KOBO.txt", "NOTICE", "CONTENT_LICENSE", "LICENSE"},
                )
                instructions = bundle.read("INSTALL-KOBO.txt").decode("utf-8")
                self.assertIn(".kobo/custom-dict", instructions)
                self.assertIn("dictionary selector", instructions)

            manifest = root / MANIFEST_NAME
            write_manifest(
                manifest,
                version=Version("1.0.0", "v1.0.0"),
                commit_sha="abc123",
                entry_count=575,
                database_hash="dbhash",
                formats={
                    "kindle": {"smoke_tests": {"checks": ["MOBI v7"]}},
                    "stardict": {"smoke_tests": {"checks": ["StarDict 2.4.2"]}},
                    "kobo": {"smoke_tests": {"checks": ["Kobo dicthtml"]}},
                },
                artifact_hashes={
                    MOBI_NAME: sha256_file(mobi),
                    ZIP_NAME: sha256_file(archive),
                    STARDICT_ZIP_NAME: sha256_file(stardict_archive),
                    DICTGEN_OUTPUT_NAME: sha256_file(kobo_dictzip),
                    KOBO_ZIP_NAME: sha256_file(kobo_archive),
                },
            )
            manifest_data = json.loads(manifest.read_text(encoding="utf-8"))
            self.assertEqual(manifest_data["entry_count"], 575)
            self.assertEqual(manifest_data["schema_version"], 2)
            self.assertIn("stardict", manifest_data["formats"])
            self.assertIn("kobo", manifest_data["formats"])

            checksums = root / CHECKSUMS_NAME
            write_checksums(checksums, (mobi, archive, stardict_archive, kobo_dictzip, kobo_archive, manifest))
            text = checksums.read_text(encoding="ascii")
            self.assertIn(f"{sha256_file(mobi)}  {MOBI_NAME}", text)
            self.assertIn(MANIFEST_NAME, text)
            self.assertIn(STARDICT_ZIP_NAME, text)
            self.assertIn(DICTGEN_OUTPUT_NAME, text)
            self.assertIn(KOBO_ZIP_NAME, text)

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

    def test_stardict_only_release_packages_only_stardict_assets(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            for name in ("NOTICE", "CONTENT_LICENSE", "LICENSE"):
                (root / name).write_text(name, encoding="utf-8")
            write_badge_files(
                root / "badges",
                build_badges(parse_badge_version("1.2.3"), CoverageResult(100, 100), 5),
            )
            database = root / "entries.sqlite"
            conn = sqlite3.connect(database)
            conn.execute(
                """
                CREATE TABLE pages (
                    title TEXT, url TEXT, first_paragraph TEXT,
                    raw_html TEXT, status TEXT
                )
                """
            )
            conn.executemany(
                "INSERT INTO pages VALUES (?, ?, ?, '', 'ok')",
                [
                    ("Carl", "https://example/Carl", "Carl travels with Donut."),
                    ("Donut", "https://example/Donut", "Donut is a crawler."),
                    ("Mordecai", "https://example/Mordecai", "Mordecai is a guide."),
                    ("1914 Box", "https://example/1914", "A reward box."),
                    ("Fire Fingers Spell", "https://example/Fire", "A fire spell."),
                ],
            )
            conn.commit()
            conn.close()

            with mock.patch("dcdict.release.run_unit_tests"), mock.patch(
                "dcdict.release.reextract_first_paragraphs", return_value=5
            ):
                release_dir = package_release(
                    version=Version("1.2.3", "v1.2.3"),
                    repo_root=root,
                    input_db=database,
                    dist_root=root / "dist",
                    commit_sha="abc123",
                    overwrite=False,
                    formats=frozenset({"stardict"}),
                    link_entries=True,
                )

            self.assertEqual(
                {path.name for path in release_dir.iterdir()},
                {STARDICT_ZIP_NAME, CHECKSUMS_NAME, MANIFEST_NAME},
            )
            manifest = json.loads((release_dir / MANIFEST_NAME).read_text(encoding="utf-8"))
            self.assertEqual(set(manifest["formats"]), {"stardict"})
            self.assertEqual(manifest["formats"]["stardict"]["smoke_tests"]["alias_count"], 2)
            self.assertEqual(manifest["formats"]["stardict"]["multi_lookup_count"], 0)

    def test_kobo_only_release_packages_only_kobo_assets(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            for name in ("NOTICE", "CONTENT_LICENSE", "LICENSE"):
                (root / name).write_text(name, encoding="utf-8")
            write_badge_files(
                root / "badges",
                build_badges(parse_badge_version("1.2.3"), CoverageResult(100, 100), 5),
            )
            database = root / "entries.sqlite"
            conn = sqlite3.connect(database)
            conn.execute(
                """
                CREATE TABLE pages (
                    title TEXT, url TEXT, first_paragraph TEXT,
                    raw_html TEXT, status TEXT
                )
                """
            )
            conn.executemany(
                "INSERT INTO pages VALUES (?, ?, ?, '', 'ok')",
                [
                    ("Carl", "https://example/Carl", "Carl travels with Donut."),
                    ("Donut", "https://example/Donut", "Donut is a crawler."),
                    ("Mordecai", "https://example/Mordecai", "Mordecai is a guide."),
                    ("1914 Box", "https://example/1914", "A reward box."),
                    ("Fire Fingers Spell", "https://example/Fire", "A fire spell."),
                ],
            )
            conn.commit()
            conn.close()

            def fake_build_kobo(entries, output_dir, **_kwargs):
                output_dir.mkdir(parents=True)
                dictzip_path = output_dir / DICTGEN_OUTPUT_NAME
                synthetic_kobo_zip(dictzip_path, entries)
                return KoboBuildResult(
                    dictfile_path=output_dir / "dictionary.df",
                    dictzip_path=dictzip_path,
                    entry_count=len(entries),
                    alias_count=2,
                    compiler_log="",
                    compiler_version="test-dictgen",
                )

            with mock.patch("dcdict.release.run_unit_tests"), mock.patch(
                "dcdict.release.reextract_first_paragraphs", return_value=5
            ), mock.patch("dcdict.release.build_kobo", side_effect=fake_build_kobo):
                release_dir = package_release(
                    version=Version("1.2.3", "v1.2.3"),
                    repo_root=root,
                    input_db=database,
                    dist_root=root / "dist",
                    commit_sha="abc123",
                    overwrite=False,
                    formats=frozenset({"kobo"}),
                    link_entries=True,
                )

            self.assertEqual(
                {path.name for path in release_dir.iterdir()},
                {DICTGEN_OUTPUT_NAME, KOBO_ZIP_NAME, CHECKSUMS_NAME, MANIFEST_NAME},
            )
            manifest = json.loads((release_dir / MANIFEST_NAME).read_text(encoding="utf-8"))
            self.assertEqual(set(manifest["formats"]), {"kobo"})
            self.assertEqual(manifest["formats"]["kobo"]["smoke_tests"]["alias_count"], 2)
            self.assertEqual(manifest["formats"]["kobo"]["multi_lookup_count"], 0)

    def test_kindle_release_passes_version_tag_to_opf_builder(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            for name in ("NOTICE", "CONTENT_LICENSE", "LICENSE"):
                (root / name).write_text(name, encoding="utf-8")
            write_badge_files(
                root / "badges",
                build_badges(parse_badge_version("1.2.3"), CoverageResult(100, 100), 3),
            )
            database = root / "entries.sqlite"
            conn = sqlite3.connect(database)
            conn.execute(
                """
                CREATE TABLE pages (
                    title TEXT, url TEXT, first_paragraph TEXT,
                    raw_html TEXT, status TEXT
                )
                """
            )
            conn.executemany(
                "INSERT INTO pages VALUES (?, ?, ?, '', 'ok')",
                [
                    ("Carl", "https://example/Carl", "Carl travels with Donut."),
                    ("Donut", "https://example/Donut", "Donut is a crawler."),
                    ("Mordecai", "https://example/Mordecai", "Mordecai is a guide."),
                ],
            )
            conn.commit()
            conn.close()
            captured: dict[str, str] = {}

            def fake_build_dictionary_sources(entries, output_dir, title, author, **kwargs):
                captured["release_version"] = kwargs["release_version"]
                output_dir.mkdir(parents=True)
                opf_path = output_dir / "dictionary.opf"
                xhtml_path = output_dir / "dictionary.xhtml"
                opf_path.write_text("<package />", encoding="utf-8")
                xhtml_path.write_text("<html />", encoding="utf-8")
                return BuildResult(
                    xhtml_path=xhtml_path,
                    opf_path=opf_path,
                    entry_count=len(entries),
                    alias_count=0,
                    multi_lookup_count=0,
                    omitted_alias_count=0,
                )

            def fake_compile(opf_path, **_kwargs):
                mobi_path = opf_path.with_suffix(".mobi")
                mobi_path.write_bytes(b"MOBI")
                return CompilationResult(mobi_path, VALID_COMPILER_LOG, (), "2.9", 0)

            inspection = mock.Mock()
            inspection.checks = ("valid MOBI",)
            inspection.manifest_data.return_value = {"checks": ["valid MOBI"]}

            with mock.patch("dcdict.release.run_unit_tests"), mock.patch(
                "dcdict.release.reextract_first_paragraphs", return_value=3
            ), mock.patch(
                "dcdict.release.build_dictionary_sources",
                side_effect=fake_build_dictionary_sources,
            ), mock.patch("dcdict.release.compile_with_kindlegen", side_effect=fake_compile), mock.patch(
                "dcdict.release.inspect_mobi", return_value=inspection
            ):
                package_release(
                    version=Version("1.2.3", "v1.2.3"),
                    repo_root=root,
                    input_db=database,
                    dist_root=root / "dist",
                    commit_sha="abc123",
                    overwrite=False,
                    formats=frozenset({"kindle"}),
                    link_entries=True,
                )

            self.assertEqual(captured["release_version"], "v1.2.3")

    def test_package_release_rejects_stale_badge_metadata(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            for name in ("NOTICE", "CONTENT_LICENSE", "LICENSE"):
                (root / name).write_text(name, encoding="utf-8")
            write_badge_files(
                root / "badges",
                build_badges(parse_badge_version("1.2.2"), CoverageResult(100, 100), 5),
            )
            database = root / "entries.sqlite"
            conn = sqlite3.connect(database)
            conn.execute(
                """
                CREATE TABLE pages (
                    title TEXT, url TEXT, first_paragraph TEXT,
                    raw_html TEXT, status TEXT
                )
                """
            )
            conn.executemany(
                "INSERT INTO pages VALUES (?, ?, ?, '', 'ok')",
                [
                    ("Carl", "https://example/Carl", "Carl travels with Donut."),
                    ("Donut", "https://example/Donut", "Donut is a crawler."),
                    ("Mordecai", "https://example/Mordecai", "Mordecai is a guide."),
                    ("1914 Box", "https://example/1914", "A reward box."),
                    ("Fire Fingers Spell", "https://example/Fire", "A fire spell."),
                ],
            )
            conn.commit()
            conn.close()

            with mock.patch("dcdict.release.run_unit_tests"), mock.patch(
                "dcdict.release.reextract_first_paragraphs", return_value=5
            ), self.assertRaisesRegex(ReleaseError, "badge metadata is stale"):
                package_release(
                    version=Version("1.2.3", "v1.2.3"),
                    repo_root=root,
                    input_db=database,
                    dist_root=root / "dist",
                    commit_sha="abc123",
                    overwrite=False,
                    formats=frozenset({"stardict"}),
                    link_entries=True,
                )

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
