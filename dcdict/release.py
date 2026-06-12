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
from dcdict.fetch_characters import reextract_first_paragraphs
from dcdict.kindle import (
    DEFAULT_AUTHOR,
    DEFAULT_TITLE,
    CompilationResult,
    build_dictionary_sources,
    compile_with_kindlegen,
    find_kindlegen,
    load_entries,
)
from dcdict.mobi import MobiInspection, MobiValidationError, inspect_mobi


LOGGER = logging.getLogger(__name__)
MOBI_NAME = "Dungeon-Crawler-Carl-Dictionary.mobi"
ZIP_NAME = "Dungeon-Crawler-Carl-Dictionary.zip"
CHECKSUMS_NAME = "SHA256SUMS.txt"
MANIFEST_NAME = "release-manifest.json"
RELEASE_ASSET_NAMES = (MOBI_NAME, ZIP_NAME, CHECKSUMS_NAME, MANIFEST_NAME)
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


def preflight_local(repo_root: Path, input_db: Path, runner: CommandRunner = run_command) -> str:
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
    if not find_kindlegen():
        raise ReleaseError("kindlegen was not found; install Kindle Previewer 3 first")
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
    """Return concise sideloading instructions bundled with releases."""

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
    """Create the user-facing release ZIP with licenses and instructions."""

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(mobi_path, MOBI_NAME)
        archive.writestr("INSTALL.txt", installation_text())
        for name in ("NOTICE", "CONTENT_LICENSE", "LICENSE"):
            archive.write(repo_root / name, name)


def write_manifest(
    path: Path,
    *,
    version: Version,
    commit_sha: str,
    entry_count: int,
    database_hash: str,
    compilation: CompilationResult,
    inspection: MobiInspection,
    artifact_hashes: dict[str, str],
) -> None:
    """Write machine-readable provenance and smoke-test results."""

    manifest = {
        "version": version.value,
        "tag": version.tag,
        "commit_sha": commit_sha,
        "entry_count": entry_count,
        "database_sha256": database_hash,
        "compiler": {
            "name": "kindlegen",
            "version": compilation.compiler_version,
            "returncode": compilation.returncode,
            "warnings": list(compilation.warnings),
        },
        "built_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "smoke_tests": inspection.manifest_data(),
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
    runner: CommandRunner = run_command,
) -> Path:
    """Build and atomically install a verified local release directory."""

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
            reextract_count = reextract_first_paragraphs(conn)
        finally:
            conn.close()
        LOGGER.info("re-extracted %s stored pages from the database snapshot", reextract_count)

        run_unit_tests(repo_root, runner)
        entries = load_entries(snapshot_path, min_definition_length=8)
        findings = audit_entries(entries)
        fatal_findings, warning_findings = classify_audit_findings(findings)
        for finding in warning_findings:
            LOGGER.warning("audit warning: %s", finding.format())
        if fatal_findings:
            detail = "\n".join(finding.format() for finding in fatal_findings)
            raise ReleaseError(f"entry audit found release-blocking issues:\n{detail}")
        LOGGER.info("entry audit passed with %s warning(s)", len(warning_findings))

        build = build_dictionary_sources(entries, work_dir, DEFAULT_TITLE, DEFAULT_AUTHOR)
        compilation = compile_with_kindlegen(build.opf_path, dont_append_source=True)
        if compilation is None:
            raise ReleaseError("kindlegen disappeared after preflight")
        (work_dir / "kindlegen.log").write_text(compilation.compiler_log, encoding="utf-8")
        validate_compilation(compilation)

        mobi_path = asset_dir / MOBI_NAME
        shutil.copy2(compilation.output_path, mobi_path)
        inspection = inspect_mobi(mobi_path, expected_title=DEFAULT_TITLE)
        LOGGER.info("MOBI smoke tests passed (%s checks)", len(inspection.checks))

        zip_path = asset_dir / ZIP_NAME
        write_release_zip(zip_path, mobi_path, repo_root)
        manifest_path = asset_dir / MANIFEST_NAME
        payload_hashes = {
            MOBI_NAME: sha256_file(mobi_path),
            ZIP_NAME: sha256_file(zip_path),
        }
        write_manifest(
            manifest_path,
            version=version,
            commit_sha=commit_sha,
            entry_count=build.entry_count,
            database_hash=sha256_file(snapshot_path),
            compilation=compilation,
            inspection=inspection,
            artifact_hashes=payload_hashes,
        )
        checksums_path = asset_dir / CHECKSUMS_NAME
        write_checksums(checksums_path, (mobi_path, zip_path, manifest_path))

        install_release_directory(asset_dir, final_dir, temp_root)

    return final_dir


def release_notes(version: Version) -> str:
    """Return the maintained preface placed before generated GitHub notes."""

    return f"""## Install

Download `{MOBI_NAME}`, connect the Kindle by USB, and copy the file into
`documents/dictionaries`. Then select **Dungeon Crawler Carl Dictionary** under
Settings -> Language & Dictionaries -> Dictionaries.

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
    parser.add_argument("--input", type=Path, default=Path("data/characters.sqlite"))
    parser.add_argument("--dist-dir", type=Path, default=Path("dist"))
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
        commit_sha = preflight_local(repo_root, input_db)
        release_dir = package_release(
            version=version,
            repo_root=repo_root,
            input_db=input_db,
            dist_root=dist_root,
            commit_sha=commit_sha,
            overwrite=args.overwrite,
        )
        LOGGER.info("release bundle ready: %s", release_dir)
        if args.publish:
            publish_release(release_dir, version, commit_sha, repo_root)
            LOGGER.info("published and verified GitHub Release %s", version.tag)
        return 0
    except (ReleaseError, MobiValidationError, OSError, sqlite3.Error, subprocess.SubprocessError) as exc:
        LOGGER.error("release failed: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
