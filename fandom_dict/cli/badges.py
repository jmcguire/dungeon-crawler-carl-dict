#!/usr/bin/env python3
"""Generate local Shields.io endpoint badge files."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import trace
from dataclasses import dataclass
from pathlib import Path

from fandom_dict.config import DEFAULT_CONFIG_PATH, load_project_config
from fandom_dict.entries import load_entries


BADGE_NAMES = ("release", "coverage", "python", "licenses", "output")
SHIELDS_SCHEMA_VERSION = 1
SEMVER_PATTERN = re.compile(
    r"^(?:v)?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)


@dataclass(frozen=True)
class Version:
    """A validated semantic version and its Git tag."""

    value: str
    tag: str


@dataclass(frozen=True)
class CoverageResult:
    """Line coverage summary for project modules."""

    covered_lines: int
    executable_lines: int

    @property
    def percent(self) -> int:
        """Return rounded integer line coverage."""

        if self.executable_lines == 0:
            return 100
        return round((self.covered_lines / self.executable_lines) * 100)


def parse_version(value: str) -> Version:
    """Validate a SemVer string and normalize its release tag."""

    match = SEMVER_PATTERN.fullmatch(value.strip())
    if not match:
        raise ValueError(f"invalid semantic version: {value!r}")
    major, minor, patch, prerelease, build = match.groups()
    if prerelease and any(
        part.isdigit() and len(part) > 1 and part.startswith("0")
        for part in prerelease.split(".")
    ):
        raise ValueError(f"invalid semantic version: {value!r}")
    normalized = f"{major}.{minor}.{patch}"
    if prerelease:
        normalized += f"-{prerelease}"
    if build:
        normalized += f"+{build}"
    return Version(normalized, f"v{normalized}")


def badge(label: str, message: str, color: str) -> dict[str, object]:
    """Return one deterministic Shields endpoint payload."""

    return {
        "schemaVersion": SHIELDS_SCHEMA_VERSION,
        "label": label,
        "message": message,
        "color": color,
    }


def coverage_color(percent: int) -> str:
    """Return a conventional badge color for coverage percentage."""

    if percent >= 90:
        return "brightgreen"
    if percent >= 80:
        return "green"
    if percent >= 70:
        return "yellowgreen"
    if percent >= 60:
        return "yellow"
    return "red"


def entry_count_color(entry_count: int) -> str:
    """Return a stable color for output-size badges."""

    if entry_count >= 1000:
        return "brightgreen"
    if entry_count >= 500:
        return "green"
    if entry_count > 0:
        return "yellowgreen"
    return "red"


def format_count(value: int) -> str:
    """Format integers for README-facing badge text."""

    return f"{value:,}"


def parse_trace_summary(output: str, repo_root: Path) -> CoverageResult:
    """Parse ``python -m trace --summary`` output for files in ``fandom_dict``."""

    covered_lines = 0
    executable_lines = 0
    package_root = (repo_root / "fandom_dict").resolve()
    pattern = re.compile(r"^\s*(\d+)\s+(\d+(?:\.\d+)?)%\s+\S+\s+\((.+)\)\s*$")
    for line in output.splitlines():
        match = pattern.match(line)
        if not match:
            continue
        lines = int(match.group(1))
        percent = float(match.group(2))
        path = Path(match.group(3)).resolve()
        try:
            path.relative_to(package_root)
        except ValueError:
            continue
        executable_lines += lines
        covered_lines += round(lines * percent / 100)
    if executable_lines == 0:
        raise RuntimeError("trace output did not include any fandom_dict modules")
    return CoverageResult(covered_lines=covered_lines, executable_lines=executable_lines)


def project_python_files(repo_root: Path) -> list[Path]:
    """Return tracked project modules that should count toward coverage."""

    package_root = repo_root / "fandom_dict"
    return sorted(
        path
        for path in package_root.rglob("*.py")
        if path.name != "__main__.py" and "__pycache__" not in path.parts
    )


def project_executable_line_count(repo_root: Path) -> int:
    """Count executable lines in all project modules, including unexecuted files."""

    total = 0
    for path in project_python_files(repo_root):
        # The public trace command reports only files that executed. Its own
        # helper gives us the same executable-line model for files with 0 hits.
        total += len(trace._find_executable_linenos(str(path)))
    if total == 0:
        raise RuntimeError("no executable lines found in fandom_dict modules")
    return total


def run_trace_coverage(repo_root: Path) -> CoverageResult:
    """Run the test suite with stdlib trace and return project line coverage."""

    with tempfile.TemporaryDirectory(prefix="fandom_dict-trace-") as cover_dir:
        command = (
            sys.executable,
            "-m",
            "trace",
            "--count",
            "--summary",
            "--coverdir",
            cover_dir,
            "--ignore-dir",
            _trace_ignore_dirs(repo_root),
            "--module",
            "unittest",
            "discover",
            "-s",
            "tests",
        )
        result = subprocess.run(
            command,
            cwd=repo_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
    if result.returncode != 0:
        raise RuntimeError(f"coverage test run failed:\n{result.stdout.strip()}")
    executed_coverage = parse_trace_summary(result.stdout, repo_root)
    executable_lines = project_executable_line_count(repo_root)
    covered_lines = min(executed_coverage.covered_lines, executable_lines)
    return CoverageResult(covered_lines=covered_lines, executable_lines=executable_lines)



def _trace_ignore_dirs(repo_root: Path) -> str:
    """Return OS-specific trace ignore paths outside this project."""

    paths = [
        Path(sys.base_prefix).resolve(),
        Path(tempfile.gettempdir()).resolve(),
        (repo_root / "venv").resolve(),
    ]
    if sys.prefix != sys.base_prefix:
        paths.append(Path(sys.prefix).resolve())
    return os.pathsep.join(str(path) for path in paths)


def count_entries(db_path: Path, config_path: Path = DEFAULT_CONFIG_PATH) -> int:
    """Return the number of usable release entries in a crawler database."""

    config = load_project_config(config_path)
    return len(
        load_entries(
            db_path,
            min_definition_length=8,
            sidebar_fields=config.sidebar_fields,
            strip_parenthetical_disambiguation=config.title_aliases.strip_parenthetical,
            max_summary_length=config.max_summary_length,
        )
    )


def build_badges(version: Version, coverage: CoverageResult, entry_count: int) -> dict[str, dict[str, object]]:
    """Build all tracked badge payloads."""

    percent = coverage.percent
    return {
        "release": badge("release", version.tag, "blue"),
        "coverage": badge("coverage", f"{percent}% lines", coverage_color(percent)),
        "python": badge("python", "3.11+", "blue"),
        "licenses": badge("licenses", "MIT + CC BY-SA 3.0", "blueviolet"),
        "output": badge("output", f"{format_count(entry_count)} entries", entry_count_color(entry_count)),
    }


def write_badge_files(badge_dir: Path, badges: dict[str, dict[str, object]]) -> None:
    """Write badge JSON files with stable formatting."""

    badge_dir.mkdir(parents=True, exist_ok=True)
    for name in BADGE_NAMES:
        payload = badges[name]
        (badge_dir / f"{name}.json").write_text(
            json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )


def read_badge(path: Path) -> dict[str, object]:
    """Read and lightly validate one Shields endpoint JSON file."""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid badge file {path}: {exc}") from exc
    if payload.get("schemaVersion") != SHIELDS_SCHEMA_VERSION:
        raise ValueError(f"invalid badge schema in {path}")
    for key in ("label", "message", "color"):
        if not isinstance(payload.get(key), str) or not payload[key]:
            raise ValueError(f"badge {path} is missing {key}")
    return payload


def validate_badges(badge_dir: Path, version: Version, entry_count: int) -> None:
    """Raise if tracked badge metadata is missing or stale for a release."""

    expected = {
        "release": version.tag,
        "output": f"{format_count(entry_count)} entries",
    }
    for name in BADGE_NAMES:
        path = badge_dir / f"{name}.json"
        payload = read_badge(path)
        if name in expected and payload.get("message") != expected[name]:
            raise ValueError(
                f"{path} is stale: expected message {expected[name]!r}, "
                f"found {payload.get('message')!r}"
            )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse badge command arguments."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True, help="Dictionary release version, such as 1.0.0")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--output-dir", type=Path, default=Path("badges"))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Generate badge files for the current release-prep commit."""

    args = parse_args(argv)
    try:
        version = parse_version(args.version)
        repo_root = Path.cwd()
        config = load_project_config(args.config)
        input_arg = args.input or config.database_path
        input_db = input_arg if input_arg.is_absolute() else repo_root / input_arg
        output_dir = args.output_dir if args.output_dir.is_absolute() else repo_root / args.output_dir
        coverage = run_trace_coverage(repo_root)
        entry_count = count_entries(input_db, args.config)
        write_badge_files(output_dir, build_badges(version, coverage, entry_count))
        print(f"wrote {output_dir}")
        print(f"coverage: {coverage.percent}% lines")
        print(f"entries: {format_count(entry_count)}")
        print(f"release: {version.tag}")
        return 0
    except Exception as exc:
        print(f"badge generation failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
