#!/usr/bin/env python3
"""Build, verify, package, and optionally publish a dictionary release."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Sequence

from dcdict.audit_entries import AuditFinding, audit_entries
from dcdict.badges import validate_badges
from dcdict.config import load_default_project_config
from dcdict.entries import load_entries
from dcdict.fetch_entries import reextract_first_paragraphs
from dcdict.kindle import (
    DEFAULT_AUTHOR,
    DEFAULT_TITLE,
    CompilationResult,
    build_dictionary_sources,
    compile_with_kindlegen,
    find_kindlegen,
)
from dcdict.kobo import (
    DICTGEN_OUTPUT_NAME,
    KoboBuildResult,
    KoboValidationError,
    build_kobo,
    find_dictgen,
    inspect_kobo,
)
from dcdict.mobi import MobiValidationError, inspect_mobi
from dcdict.stardict import (
    BASE_NAME as STARDICT_BASE_NAME,
    StarDictBuildResult,
    StarDictValidationError,
    build_stardict,
    inspect_stardict,
)


LOGGER = logging.getLogger(__name__)
DEFAULT_PROJECT = load_default_project_config()
MOBI_NAME = "Dungeon-Crawler-Carl-Dictionary.mobi"
ZIP_NAME = "Dungeon-Crawler-Carl-Dictionary.zip"
STARDICT_ZIP_NAME = "Dungeon-Crawler-Carl-Dictionary-StarDict.zip"
KOBO_ZIP_NAME = "Dungeon-Crawler-Carl-Dictionary-Kobo.zip"
CHECKSUMS_NAME = "SHA256SUMS.txt"
MANIFEST_NAME = "release-manifest.json"
BADGE_DIR_NAME = "badges"
ALL_FORMATS = frozenset({"kindle", "stardict", "kobo"})
RELEASE_ASSET_NAMES = (
    MOBI_NAME,
    ZIP_NAME,
    STARDICT_ZIP_NAME,
    DICTGEN_OUTPUT_NAME,
    KOBO_ZIP_NAME,
    CHECKSUMS_NAME,
    MANIFEST_NAME,
)
FATAL_AUDIT_KINDS = frozenset({"gallery-credit", "maintenance-text", "source-artifact", "truncated"})
WARNING_AUDIT_KINDS = frozenset({"short", "unresolved-forward"})
SEMVER_PATTERN = re.compile(
    r"^(?:v)?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)


class ReleaseError(RuntimeError):
    """Raised when release packaging or publication cannot proceed."""


@dataclass(frozen=True)
class Version:
    """A validated semantic version and its Git tag."""

    value: str
    tag: str


@dataclass(frozen=True)
class CommandResult:
    """Captured subprocess output used by release orchestration."""

    returncode: int
    stdout: str


CommandRunner = Callable[[Sequence[str], Path], CommandResult]


def parse_version(value: str) -> Version:
    """Validate a SemVer string and normalize its release tag."""

    match = SEMVER_PATTERN.fullmatch(value.strip())
    if not match:
        raise ReleaseError(f"invalid semantic version: {value!r}")
    major, minor, patch, prerelease, build = match.groups()
    if prerelease and any(
        part.isdigit() and len(part) > 1 and part.startswith("0")
        for part in prerelease.split(".")
    ):
        raise ReleaseError(f"invalid semantic version: {value!r}")
    normalized = f"{major}.{minor}.{patch}"
    if prerelease:
        normalized += f"-{prerelease}"
    if build:
        normalized += f"+{build}"
    return Version(normalized, f"v{normalized}")


def run_command(command: Sequence[str], cwd: Path) -> CommandResult:
    """Run a command and capture combined standard output and error."""

    result = subprocess.run(
        list(command),
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    return CommandResult(result.returncode, result.stdout or "")


def require_success(result: CommandResult, description: str) -> str:
    """Return command output or raise a release error with context."""

    if result.returncode != 0:
        detail = result.stdout.strip()
        raise ReleaseError(f"{description} failed" + (f":\n{detail}" if detail else ""))
    return result.stdout.strip()


def git_output(repo_root: Path, *args: str, runner: CommandRunner = run_command) -> str:
    """Run Git and return stripped output."""

    return require_success(runner(("git", *args), repo_root), f"git {' '.join(args)}")


def repository_root(cwd: Path, runner: CommandRunner = run_command) -> Path:
    """Locate the Git repository containing the release command."""

    return Path(git_output(cwd, "rev-parse", "--show-toplevel", runner=runner))


def preflight_local(
    repo_root: Path,
    input_db: Path,
    runner: CommandRunner = run_command,
    formats: frozenset[str] = ALL_FORMATS,
) -> str:
    """Verify local inputs and return the exact commit being packaged."""

    if git_output(repo_root, "status", "--porcelain", runner=runner):
        raise ReleaseError("Git worktree must be clean before packaging a release")
    if not input_db.is_file():
        raise ReleaseError(f"SQLite input does not exist: {input_db}")
    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(f"file:{input_db}?mode=ro", uri=True)
        conn.execute("SELECT 1 FROM pages LIMIT 1").fetchone()
    except sqlite3.Error as exc:
        raise ReleaseError(f"SQLite input is not a usable crawler database: {exc}") from exc
    finally:
        if conn is not None:
            conn.close()
    if "kindle" in formats and not find_kindlegen():
        raise ReleaseError("kindlegen was not found; install Kindle Previewer 3 first")
    if "kobo" in formats and not find_dictgen():
        raise ReleaseError("dictgen was not found; install pgaskin/dictutil dictgen first")
    return git_output(repo_root, "rev-parse", "HEAD", runner=runner)


def snapshot_database(source: Path, destination: Path) -> None:
    """Create a transactionally consistent SQLite snapshot, including WAL data."""

    source_conn = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    destination_conn = sqlite3.connect(destination)
    try:
        source_conn.backup(destination_conn)
    finally:
        destination_conn.close()
        source_conn.close()


def run_unit_tests(repo_root: Path, runner: CommandRunner = run_command) -> None:
    """Run the complete standard-library unit test suite."""

    result = runner((sys.executable, "-m", "unittest", "discover", "-s", "tests"), repo_root)
    if result.returncode != 0:
        raise ReleaseError(f"unit tests failed:\n{result.stdout.strip()}")
    LOGGER.info("unit tests passed")


def classify_audit_findings(
    findings: Sequence[AuditFinding],
) -> tuple[list[AuditFinding], list[AuditFinding]]:
    """Split audit findings into release-blocking errors and warnings."""

    fatal: list[AuditFinding] = []
    warnings: list[AuditFinding] = []
    for finding in findings:
        if finding.kind in WARNING_AUDIT_KINDS:
            warnings.append(finding)
        elif finding.kind in FATAL_AUDIT_KINDS:
            fatal.append(finding)
        else:
            fatal.append(finding)
    return fatal, warnings


def validate_compilation(compilation: CompilationResult) -> None:
    """Require the expected successful kindlegen dictionary build messages."""

    log = compilation.compiler_log
    errors = [line.strip() for line in log.splitlines() if line.strip().lower().startswith("error")]
    if errors:
        raise ReleaseError("kindlegen reported errors:\n" + "\n".join(errors))
    required_messages = {
        "MOBI v7 output": "The file format version is V7",
        "default dictionary index": 'The default lookup index is: "default"',
        "orthographic index build": "Index name: default",
        "naming index build": "Building naming index into record",
    }
    missing = [name for name, message in required_messages.items() if message not in log]
    if missing:
        raise ReleaseError("kindlegen log is missing: " + ", ".join(missing))
    if not compilation.output_path.is_file():
        raise ReleaseError("kindlegen did not create a MOBI output file")


def sha256_file(path: Path) -> str:
    """Return a lowercase SHA-256 digest for a file."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def installation_text() -> str:
    """Return concise Kindle sideloading instructions."""

    return f"""Dungeon Crawler Carl Dictionary - Installation

1. Connect your Kindle to your computer with USB.
2. Copy {MOBI_NAME} into the Kindle's documents/dictionaries folder.
3. Safely eject the Kindle and wait briefly for it to index the dictionary.
4. Open Settings -> Language & Dictionaries -> Dictionaries and select
   Dungeon Crawler Carl Dictionary for English.

The dictionary content is derived from Dungeon Crawler Carl Wiki contributors
and is distributed under CC BY-SA 3.0. See CONTENT_LICENSE and NOTICE.
"""


def write_release_zip(zip_path: Path, mobi_path: Path, repo_root: Path) -> None:
    """Create the Kindle release ZIP with licenses and instructions."""

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(mobi_path, MOBI_NAME)
        archive.writestr("INSTALL.txt", installation_text())
        for name in ("NOTICE", "CONTENT_LICENSE", "LICENSE"):
            archive.write(repo_root / name, name)


def koreader_installation_text() -> str:
    """Return concise KOReader StarDict installation instructions."""

    return f"""Dungeon Crawler Carl Dictionary - KOReader Installation

1. Extract the {STARDICT_ZIP_NAME} download.
2. Keep the extracted {STARDICT_BASE_NAME} folder and all files inside it together.
3. Connect the reader to your computer and copy that folder into:
   koreader/data/dict/
4. Restart KOReader. If needed, open Dictionary settings -> Manage dictionaries
   and enable Dungeon Crawler Carl Dictionary.
5. To make it the default lookup result globally, use the same Manage
   dictionaries screen to move Dungeon Crawler Carl Dictionary above your
   other dictionaries, then accept/save the order.
6. To make it the priority dictionary for one book only, open that book and
   use Dictionary settings -> Set dictionary priority for this book. Select
   Dungeon Crawler Carl Dictionary so it appears first in the preferred list.

The dictionary content is derived from Dungeon Crawler Carl Wiki contributors
and is distributed under CC BY-SA 3.0. See CONTENT_LICENSE and NOTICE.
"""


def write_stardict_zip(
    zip_path: Path,
    build: StarDictBuildResult,
    repo_root: Path,
) -> None:
    """Create one installable KOReader StarDict ZIP."""

    folder = STARDICT_BASE_NAME
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in build.files:
            archive.write(path, f"{folder}/{path.name}")
        archive.writestr(f"{folder}/INSTALL-KOREADER.txt", koreader_installation_text())
        for name in ("NOTICE", "CONTENT_LICENSE", "LICENSE"):
            archive.write(repo_root / name, f"{folder}/{name}")


def kobo_installation_text() -> str:
    """Return concise Kobo sideloading instructions."""

    return f"""Dungeon Crawler Carl Dictionary - Kobo Installation

1. Connect your Kobo to your computer with USB.
2. Copy {DICTGEN_OUTPUT_NAME} into the Kobo's .kobo/custom-dict folder.
   If that folder does not exist, create it.
3. Safely eject the Kobo and restart it.
4. Open a book, select a word, and open the dictionary panel.
5. Use the dictionary selector in the lookup panel to choose the custom
   dictionary named for the dc locale.

On older Kobo firmware, custom dictionaries may require ExtraLocales or a
custom dictionary patch before they can be selected. Current Kobo firmware
supports .kobo/custom-dict for custom dictionaries.

The dictionary content is derived from Dungeon Crawler Carl Wiki contributors
and is distributed under CC BY-SA 3.0. See CONTENT_LICENSE and NOTICE.
"""


def write_kobo_zip(
    zip_path: Path,
    build: KoboBuildResult,
    repo_root: Path,
) -> None:
    """Create one installable Kobo bundle with instructions."""

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(build.dictzip_path, DICTGEN_OUTPUT_NAME)
        archive.writestr("INSTALL-KOBO.txt", kobo_installation_text())
        for name in ("NOTICE", "CONTENT_LICENSE", "LICENSE"):
            archive.write(repo_root / name, name)


def write_manifest(
    path: Path,
    *,
    version: Version,
    commit_sha: str,
    entry_count: int,
    database_hash: str,
    formats: dict[str, object],
    artifact_hashes: dict[str, str],
) -> None:
    """Write machine-readable provenance and smoke-test results."""

    manifest = {
        "schema_version": 2,
        "version": version.value,
        "tag": version.tag,
        "commit_sha": commit_sha,
        "entry_count": entry_count,
        "database_sha256": database_hash,
        "built_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "formats": formats,
        "artifact_sha256": artifact_hashes,
    }
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_checksums(path: Path, assets: Sequence[Path]) -> None:
    """Write conventional SHA-256 lines for release payloads."""

    lines = [f"{sha256_file(asset)}  {asset.name}" for asset in assets]
    path.write_text("\n".join(lines) + "\n", encoding="ascii")


def install_release_directory(staged_dir: Path, final_dir: Path, temp_root: Path) -> None:
    """Atomically install staged assets, restoring an old release on failure."""

    if not final_dir.exists():
        staged_dir.replace(final_dir)
        return

    backup_dir = temp_root / "previous-release"
    final_dir.replace(backup_dir)
    try:
        staged_dir.replace(final_dir)
    except Exception:
        backup_dir.replace(final_dir)
        raise
    shutil.rmtree(backup_dir)


def package_release(
    *,
    version: Version,
    repo_root: Path,
    input_db: Path,
    dist_root: Path,
    commit_sha: str,
    overwrite: bool,
    formats: frozenset[str] = ALL_FORMATS,
    link_entries: bool = False,
    include_sidebar_aliases: bool = True,
    runner: CommandRunner = run_command,
) -> Path:
    """Build and atomically install a verified multi-format release directory."""

    if not formats or not formats <= ALL_FORMATS:
        raise ReleaseError(f"unsupported release formats: {sorted(formats)}")
    final_dir = dist_root / version.tag
    if final_dir.exists() and not overwrite:
        raise ReleaseError(f"release directory already exists: {final_dir}; use --overwrite to replace it")
    dist_root.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix=f".{version.tag}-", dir=dist_root) as temp_name:
        temp_root = Path(temp_name)
        snapshot_path = temp_root / "release.sqlite"
        work_dir = temp_root / "build"
        asset_dir = temp_root / "assets"
        asset_dir.mkdir()

        snapshot_database(input_db, snapshot_path)
        conn = sqlite3.connect(snapshot_path)
        try:
            reextract_count = reextract_first_paragraphs(conn, DEFAULT_PROJECT.max_summary_length)
        finally:
            conn.close()
        LOGGER.info("re-extracted %s stored pages from the database snapshot", reextract_count)

        run_unit_tests(repo_root, runner)
        entries = load_entries(
            snapshot_path,
            min_definition_length=8,
            sidebar_fields=DEFAULT_PROJECT.sidebar_fields,
            strip_parenthetical_disambiguation=DEFAULT_PROJECT.title_aliases.strip_parenthetical,
            max_summary_length=DEFAULT_PROJECT.max_summary_length,
        )
        try:
            validate_badges(repo_root / BADGE_DIR_NAME, version, len(entries))
        except ValueError as exc:
            raise ReleaseError(
                f"badge metadata is stale: {exc}. Run "
                f"`python3 -m dcdict.badges --version {version.value} --input {input_db}` "
                "and commit the badge JSON files before releasing."
            ) from exc
        findings = audit_entries(entries)
        fatal_findings, warning_findings = classify_audit_findings(findings)
        for finding in warning_findings:
            LOGGER.warning("audit warning: %s", finding.format())
        if fatal_findings:
            detail = "\n".join(finding.format() for finding in fatal_findings)
            raise ReleaseError(f"entry audit found release-blocking issues:\n{detail}")
        LOGGER.info("entry audit passed with %s warning(s)", len(warning_findings))

        payload_paths: list[Path] = []
        format_manifest: dict[str, object] = {}
        if "kindle" in formats:
            kindle_dir = work_dir / "kindle"
            kindle_build = build_dictionary_sources(
                entries,
                kindle_dir,
                DEFAULT_PROJECT.title,
                DEFAULT_PROJECT.author,
                link_entries=link_entries,
                include_sidebar_aliases=include_sidebar_aliases,
                release_version=version.tag,
                source_name=DEFAULT_PROJECT.source_name,
                title_suffix_aliases=DEFAULT_PROJECT.title_aliases.suffixes,
                title_prefix_aliases=DEFAULT_PROJECT.title_aliases.prefixes,
                strip_parenthetical_disambiguation=DEFAULT_PROJECT.title_aliases.strip_parenthetical,
                sidebar_alias_labels=DEFAULT_PROJECT.sidebar_alias_labels,
            )
            LOGGER.info(
                "Kindle aliases: %s accepted, %s multi-target, %s omitted",
                kindle_build.alias_count,
                kindle_build.multi_lookup_count,
                kindle_build.omitted_alias_count,
            )
            compilation = compile_with_kindlegen(kindle_build.opf_path, dont_append_source=True)
            if compilation is None:
                raise ReleaseError("kindlegen disappeared after preflight")
            (kindle_dir / "kindlegen.log").write_text(compilation.compiler_log, encoding="utf-8")
            validate_compilation(compilation)

            mobi_path = asset_dir / MOBI_NAME
            shutil.copy2(compilation.output_path, mobi_path)
            canonical_titles = {entry.title for entry in entries}
            mobi_headwords = tuple(word for word in DEFAULT_PROJECT.smoke_headwords if word in canonical_titles)
            mobi_inspection = inspect_mobi(
                mobi_path,
                expected_title=DEFAULT_PROJECT.title,
                representative_headwords=mobi_headwords,
            )
            LOGGER.info("MOBI smoke tests passed (%s checks)", len(mobi_inspection.checks))
            kindle_zip_path = asset_dir / ZIP_NAME
            write_release_zip(kindle_zip_path, mobi_path, repo_root)
            payload_paths.extend((mobi_path, kindle_zip_path))
            format_manifest["kindle"] = {
                "assets": [MOBI_NAME, ZIP_NAME],
                "compiler": {
                    "name": "kindlegen",
                    "version": compilation.compiler_version,
                    "returncode": compilation.returncode,
                    "warnings": list(compilation.warnings),
                },
                "smoke_tests": mobi_inspection.manifest_data(),
                "alias_count": kindle_build.alias_count,
                "multi_lookup_count": kindle_build.multi_lookup_count,
                "omitted_alias_count": kindle_build.omitted_alias_count,
            }

        if "stardict" in formats:
            stardict_build = build_stardict(
                entries,
                work_dir / "stardict",
                DEFAULT_PROJECT.title,
                DEFAULT_PROJECT.author,
                link_entries=link_entries,
                include_sidebar_aliases=include_sidebar_aliases,
                base_name=DEFAULT_PROJECT.file_base_name,
                source_name=DEFAULT_PROJECT.source_name,
                title_suffix_aliases=DEFAULT_PROJECT.title_aliases.suffixes,
                title_prefix_aliases=DEFAULT_PROJECT.title_aliases.prefixes,
                strip_parenthetical_disambiguation=DEFAULT_PROJECT.title_aliases.strip_parenthetical,
                sidebar_alias_labels=DEFAULT_PROJECT.sidebar_alias_labels,
            )
            LOGGER.info(
                "StarDict aliases: %s accepted, %s multi-target, %s omitted",
                stardict_build.alias_count,
                stardict_build.multi_lookup_count,
                stardict_build.omitted_alias_count,
            )
            stardict_inspection = inspect_stardict(
                stardict_build.ifo_path,
                expected_title=DEFAULT_PROJECT.title,
                required_headwords=DEFAULT_PROJECT.smoke_headwords,
                require_links=link_entries,
            )
            LOGGER.info("StarDict smoke tests passed (%s checks)", len(stardict_inspection.checks))
            stardict_zip_path = asset_dir / STARDICT_ZIP_NAME
            write_stardict_zip(stardict_zip_path, stardict_build, repo_root)
            payload_paths.append(stardict_zip_path)
            format_manifest["stardict"] = {
                "assets": [STARDICT_ZIP_NAME],
                "generator": "Python standard library",
                "smoke_tests": stardict_inspection.manifest_data(),
                "alias_count": stardict_build.alias_count,
                "multi_lookup_count": stardict_build.multi_lookup_count,
                "omitted_alias_count": stardict_build.omitted_alias_count,
            }

        if "kobo" in formats:
            kobo_build = build_kobo(
                entries,
                work_dir / "kobo",
                output_name=DEFAULT_PROJECT.kobo_output_name,
                include_sidebar_aliases=include_sidebar_aliases,
                source_name=DEFAULT_PROJECT.source_name,
                title_suffix_aliases=DEFAULT_PROJECT.title_aliases.suffixes,
                title_prefix_aliases=DEFAULT_PROJECT.title_aliases.prefixes,
                strip_parenthetical_disambiguation=DEFAULT_PROJECT.title_aliases.strip_parenthetical,
                sidebar_alias_labels=DEFAULT_PROJECT.sidebar_alias_labels,
            )
            LOGGER.info(
                "Kobo aliases: %s accepted, %s multi-target, %s omitted",
                kobo_build.alias_count,
                kobo_build.multi_lookup_count,
                kobo_build.omitted_alias_count,
            )
            kobo_inspection = inspect_kobo(
                kobo_build.dictzip_path,
                required_headwords=DEFAULT_PROJECT.smoke_headwords,
            )
            LOGGER.info("Kobo smoke tests passed (%s checks)", len(kobo_inspection.checks))
            kobo_dictzip_path = asset_dir / DICTGEN_OUTPUT_NAME
            shutil.copy2(kobo_build.dictzip_path, kobo_dictzip_path)
            kobo_zip_path = asset_dir / KOBO_ZIP_NAME
            write_kobo_zip(kobo_zip_path, kobo_build, repo_root)
            payload_paths.extend((kobo_dictzip_path, kobo_zip_path))
            format_manifest["kobo"] = {
                "assets": [DICTGEN_OUTPUT_NAME, KOBO_ZIP_NAME],
                "compiler": {
                    "name": "dictgen",
                    "version": kobo_build.compiler_version,
                },
                "smoke_tests": kobo_inspection.manifest_data(),
                "alias_count": kobo_build.alias_count,
                "multi_lookup_count": kobo_build.multi_lookup_count,
                "omitted_alias_count": kobo_build.omitted_alias_count,
            }

        manifest_path = asset_dir / MANIFEST_NAME
        payload_hashes = {path.name: sha256_file(path) for path in payload_paths}
        write_manifest(
            manifest_path,
            version=version,
            commit_sha=commit_sha,
            entry_count=len(entries),
            database_hash=sha256_file(snapshot_path),
            formats=format_manifest,
            artifact_hashes=payload_hashes,
        )
        checksums_path = asset_dir / CHECKSUMS_NAME
        write_checksums(checksums_path, (*payload_paths, manifest_path))
        install_release_directory(asset_dir, final_dir, temp_root)

    return final_dir


def release_notes(version: Version) -> str:
    """Return the maintained preface placed before generated GitHub notes."""

    return f"""## Kindle

Download `{MOBI_NAME}`, connect the Kindle by USB, and copy the file into
`documents/dictionaries`. Then select **Dungeon Crawler Carl Dictionary** under
Settings -> Language & Dictionaries -> Dictionaries.

## KOReader

Download `{STARDICT_ZIP_NAME}`, extract its dictionary folder, and copy that
folder into `koreader/data/dict`. Restart KOReader and enable the dictionary if
it is not selected automatically.

## Kobo

Download `{DICTGEN_OUTPUT_NAME}` and copy it into `.kobo/custom-dict` on the
Kobo. Restart the Kobo, then choose the custom dictionary from the lookup
panel's dictionary selector.

## Licensing

Code and documentation are MIT licensed. Dictionary content derived from the
Dungeon Crawler Carl Wiki is CC BY-SA 3.0; attribution and fan-project notices
are included in the ZIP and repository.

Release: `{version.tag}`
"""


def publish_release(
    release_dir: Path,
    version: Version,
    commit_sha: str,
    repo_root: Path,
    runner: CommandRunner = run_command,
) -> None:
    """Create a GitHub Release, upload assets, and verify downloaded hashes."""

    if not shutil.which("gh"):
        raise ReleaseError("GitHub CLI is required for --publish; install it with `brew install gh`")
    assets = [release_dir / name for name in RELEASE_ASSET_NAMES]
    missing_assets = [asset.name for asset in assets if not asset.is_file()]
    if missing_assets:
        raise ReleaseError("release assets are missing: " + ", ".join(missing_assets))
    require_success(runner(("gh", "auth", "status"), repo_root), "GitHub CLI authentication")
    require_success(runner(("git", "fetch", "origin", "main"), repo_root), "fetching origin/main")
    origin_sha = git_output(repo_root, "rev-parse", "origin/main", runner=runner)
    if commit_sha != origin_sha:
        raise ReleaseError(f"HEAD {commit_sha} does not match origin/main {origin_sha}")

    local_tag = runner(("git", "rev-parse", "--verify", "--quiet", f"refs/tags/{version.tag}"), repo_root)
    if local_tag.returncode == 0:
        raise ReleaseError(f"local tag already exists: {version.tag}")
    remote_tag = runner(
        ("git", "ls-remote", "--exit-code", "--tags", "origin", f"refs/tags/{version.tag}"),
        repo_root,
    )
    if remote_tag.returncode == 0:
        raise ReleaseError(f"remote tag already exists: {version.tag}")
    if remote_tag.returncode not in (0, 2):
        raise ReleaseError(f"could not check remote tag:\n{remote_tag.stdout.strip()}")
    existing_release = runner(("gh", "release", "view", version.tag, "--json", "tagName"), repo_root)
    if existing_release.returncode == 0:
        raise ReleaseError(f"GitHub Release already exists: {version.tag}")
    if "not found" not in existing_release.stdout.lower():
        raise ReleaseError(f"could not check for an existing GitHub Release:\n{existing_release.stdout.strip()}")

    with tempfile.TemporaryDirectory(prefix="dcdict-publish-") as temp_name:
        notes_path = Path(temp_name) / "release-notes.md"
        notes_path.write_text(release_notes(version), encoding="utf-8")
        command = (
            "gh",
            "release",
            "create",
            version.tag,
            *(str(asset) for asset in assets),
            "--target",
            commit_sha,
            "--title",
            f"Dungeon Crawler Carl Dictionary {version.value}",
            "--latest",
            "--generate-notes",
            "--notes-file",
            str(notes_path),
        )
        require_success(runner(command, repo_root), "creating GitHub Release")

        download_dir = Path(temp_name) / "downloaded"
        download_dir.mkdir()
        require_success(
            runner(("gh", "release", "download", version.tag, "--dir", str(download_dir)), repo_root),
            "downloading published release assets",
        )
        for asset in assets:
            downloaded = download_dir / asset.name
            if not downloaded.is_file() or sha256_file(downloaded) != sha256_file(asset):
                raise ReleaseError(f"published asset hash mismatch: {asset.name}")

    require_success(
        runner(("git", "fetch", "origin", "tag", version.tag), repo_root),
        f"fetching tag {version.tag}",
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse release command-line arguments."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True, help="Semantic version, such as 1.0.0")
    parser.add_argument("--input", type=Path, default=DEFAULT_PROJECT.database_path)
    parser.add_argument("--dist-dir", type=Path, default=Path("dist"))
    parser.add_argument(
        "--no-sidebar-aliases",
        action="store_true",
        help="Disable lookup aliases derived from wiki sidebar alias fields.",
    )
    parser.add_argument(
        "--format",
        choices=("all", "kindle", "stardict", "kobo"),
        default="all",
        help="Build all formats by default, or one format for local testing.",
    )
    parser.add_argument(
        "--link-entries",
        action="store_true",
        help="Add format-appropriate internal links between known entries.",
    )
    parser.add_argument("--overwrite", action="store_true", help="Replace an existing local version directory.")
    parser.add_argument("--publish", action="store_true", help="Create and verify a GitHub Release after packaging.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the complete release workflow."""

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = parse_args(argv)
    try:
        version = parse_version(args.version)
        repo_root = repository_root(Path.cwd())
        input_db = args.input if args.input.is_absolute() else repo_root / args.input
        dist_root = args.dist_dir if args.dist_dir.is_absolute() else repo_root / args.dist_dir
        formats = ALL_FORMATS if args.format == "all" else frozenset({args.format})
        if args.publish and formats != ALL_FORMATS:
            raise ReleaseError("--publish requires --format all")
        commit_sha = preflight_local(repo_root, input_db, formats=formats)
        release_dir = package_release(
            version=version,
            repo_root=repo_root,
            input_db=input_db,
            dist_root=dist_root,
            commit_sha=commit_sha,
            overwrite=args.overwrite,
            formats=formats,
            link_entries=args.link_entries,
            include_sidebar_aliases=not args.no_sidebar_aliases,
        )
        LOGGER.info("release bundle ready: %s", release_dir)
        if args.publish:
            publish_release(release_dir, version, commit_sha, repo_root)
            LOGGER.info("published and verified GitHub Release %s", version.tag)
        return 0
    except (
        ReleaseError,
        MobiValidationError,
        KoboValidationError,
        StarDictValidationError,
        OSError,
        sqlite3.Error,
        subprocess.SubprocessError,
    ) as exc:
        LOGGER.error("release failed: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
