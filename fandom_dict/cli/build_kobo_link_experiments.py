#!/usr/bin/env python3
"""Build small Kobo dictionaries for physical internal-link experiments."""

from __future__ import annotations

import argparse
import html
import json
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

from fandom_dict.cli.output import add_output_arguments, output_from_args
from fandom_dict.formats.kobo import KoboValidationError, find_dictgen, inspect_kobo, kobo_prefix


DEFAULT_OUTPUT_DIR = Path("build/kobo-link-experiments")
TEST_ENTRIES = (
    "Carl",
    "Carlton",
    "Donut",
    "Mordecai",
    "Valtay Corporation",
    "Desperado Club",
)
TEST_ACTIONS = (
    "Look up Carl and tap the same-prefix Carlton link",
    "Look up Carl and tap the cross-prefix Donut link",
    "Look up Donut and tap the cross-prefix Mordecai link",
    "Look up Valtay Corporation and tap the cross-prefix Desperado Club link",
)


@dataclass(frozen=True)
class KoboLinkVariant:
    """One Kobo link href strategy to test on a physical device."""

    slug: str
    output_name: str
    title: str
    description: str
    href_style: str
    allowed_href_prefixes: tuple[str, ...]
    risky: bool = False


@dataclass(frozen=True)
class KoboLinkBuild:
    """Paths and metadata for one Kobo link experiment dictionary."""

    slug: str
    title: str
    description: str
    dictfile: str
    dictzip: str
    link_count: int
    checks: tuple[str, ...]
    risky: bool


VARIANTS = (
    KoboLinkVariant(
        "kobo-link-1-hash",
        "dicthtml-kobo-link-1-hash.zip",
        "Kobo Link Test 1",
        "Plain fragment links, such as #Donut. This tests whether the dictionary webview resolves anchors.",
        "hash",
        ("#",),
    ),
    KoboLinkVariant(
        "kobo-link-2-shard-relative",
        "dicthtml-kobo-link-2-shard-relative.zip",
        "Kobo Link Test 2",
        "Shard-relative links, such as do.html#Donut. This tests whether links can cross dicthtml files.",
        "shard-relative",
        tuple(f"{kobo_prefix(word)}.html#" for word in TEST_ENTRIES),
    ),
    KoboLinkVariant(
        "kobo-link-3-dict-scheme",
        "dicthtml-kobo-link-3-dict-scheme.zip",
        "Kobo Link Test 3",
        "Risky dictionary-scheme links, such as dict:///Donut. These are isolated because dict:/// resources have known Kobo webview risk.",
        "dict-scheme",
        ("dict:///",),
        risky=True,
    ),
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments for the experiment builder."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    add_output_arguments(parser)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Build Kobo link experiment dictionaries and instructions."""

    args = parse_args(argv)
    output = output_from_args(args)
    try:
        builds = build_experiment_bundle(args.output_dir)
    except KoboValidationError as exc:
        output.error(f"Kobo link experiment failed: {exc}")
        output.close()
        return 1
    output.path(args.output_dir / "MANIFEST.md")
    output.path(args.output_dir / "TESTING_CHECKLIST.md")
    for build in builds:
        output.path(build.dictzip)
    output.info(f"experiment dictionaries: {len(builds)}")
    output.close()
    return 0


def build_experiment_bundle(output_dir: Path) -> list[KoboLinkBuild]:
    """Build all Kobo internal-link experiment dictionaries."""

    executable = find_dictgen()
    if not executable:
        raise KoboValidationError("dictgen was not found")
    output_dir.mkdir(parents=True, exist_ok=True)
    builds = [build_variant(variant, output_dir, executable) for variant in VARIANTS]
    write_manifest(output_dir, builds)
    write_checklist(output_dir, builds)
    return builds


def build_variant(variant: KoboLinkVariant, output_dir: Path, executable: str) -> KoboLinkBuild:
    """Build and inspect one Kobo link experiment dictionary."""

    variant_dir = output_dir / variant.slug
    variant_dir.mkdir(parents=True, exist_ok=True)
    dictfile_path = variant_dir / "dictionary.df"
    dictzip_path = variant_dir / variant.output_name
    dictfile_path.write_text(render_dictfile(variant), encoding="utf-8")
    result = subprocess.run(
        (executable, "-I", "remove", "-o", str(dictzip_path), str(dictfile_path)),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    if result.returncode != 0 or not dictzip_path.is_file():
        detail = (result.stdout or "").strip()
        raise KoboValidationError(f"dictgen failed for {variant.slug}" + (f":\n{detail}" if detail else ""))
    inspection = inspect_kobo(
        dictzip_path,
        required_headwords=TEST_ENTRIES,
        allowed_href_prefixes=variant.allowed_href_prefixes,
    )
    return KoboLinkBuild(
        slug=variant.slug,
        title=variant.title,
        description=variant.description,
        dictfile=str(dictfile_path),
        dictzip=str(dictzip_path),
        link_count=sum(len(entry.links) for entry in inspection.entries),
        checks=inspection.checks,
        risky=variant.risky,
    )


def render_dictfile(variant: KoboLinkVariant) -> str:
    """Render dictgen input for one Kobo link experiment variant."""

    chunks: list[str] = []
    for word in TEST_ENTRIES:
        chunks.append(f"@ {word}")
        chunks.append("::")
        chunks.append(f"<html>{render_definition(word, variant)}")
        chunks.append("")
    return "\n".join(chunks)


def render_definition(word: str, variant: KoboLinkVariant) -> str:
    """Render one definition with same-prefix and cross-prefix test links."""

    links = {
        "Carl": ("Carlton", "Donut"),
        "Carlton": ("Carl", "Donut"),
        "Donut": ("Carl", "Mordecai"),
        "Mordecai": ("Donut", "Carl"),
        "Valtay Corporation": ("Desperado Club", "Carl"),
        "Desperado Club": ("Valtay Corporation", "Carl"),
    }[word]
    rendered_links = " and ".join(
        f'<a href="{html.escape(href_for_target(target, variant), quote=True)}">{html.escape(target, quote=False)}</a>'
        for target in links
    )
    return (
        '<div class="entry">'
        f"<p><b>{html.escape(word, quote=False)}</b> is a Kobo internal-link experiment entry.</p>"
        f"<p>Tap {rendered_links}.</p>"
        f"<p>Href style: {html.escape(variant.href_style, quote=False)}.</p>"
        "</div>"
    )


def href_for_target(target: str, variant: KoboLinkVariant) -> str:
    """Return the experimental href for one target word."""

    if variant.href_style == "hash":
        return f"#{target}"
    if variant.href_style == "shard-relative":
        return f"{kobo_prefix(target)}.html#{target}"
    if variant.href_style == "dict-scheme":
        return f"dict:///{target}"
    raise ValueError(f"unsupported Kobo link href style: {variant.href_style}")


def write_manifest(output_dir: Path, builds: list[KoboLinkBuild]) -> None:
    """Write machine-readable and human-readable Kobo link experiment manifests."""

    data = {"variants": [asdict(build) for build in builds], "test_actions": list(TEST_ACTIONS)}
    (output_dir / "MANIFEST.json").write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    lines = [
        "# Kobo Internal Link Experiment Manifest",
        "",
        "Load one experiment dictionary at a time. These files are for physical-device testing only.",
        "Production Kobo dictionaries do not include internal links until one of these shapes works reliably on a real Kobo.",
        "",
    ]
    for build in builds:
        risky_note = " Note: test this last and remove it immediately if the dictionary view behaves oddly." if build.risky else ""
        lines.extend(
            [
                f"## {build.title} ({build.slug})",
                "",
                build.description + risky_note,
                "",
                f"- ZIP: `{Path(build.dictzip).name}`",
                f"- Dictfile: `{Path(build.dictfile).name}`",
                f"- Preserved links: {build.link_count}",
                f"- Checks: {', '.join(build.checks)}",
                "",
            ]
        )
    (output_dir / "MANIFEST.md").write_text("\n".join(lines), encoding="utf-8")


def write_checklist(output_dir: Path, builds: list[KoboLinkBuild]) -> None:
    """Write a manual physical-Kobo testing checklist."""

    lines = [
        "# Kobo Internal Link Experiment Checklist",
        "",
        "Copy exactly one experiment ZIP at a time into `.kobo/custom-dict/`, restart the Kobo, and choose that custom dictionary.",
        "",
        "Suggested result codes: `opens target`, `does nothing`, `blank view`, `opens browser`, `wrong entry`, `reader crash/restart`.",
        "",
        "## Actions",
        "",
    ]
    lines.extend(f"- {action}" for action in TEST_ACTIONS)
    lines.extend(["", "## Results", ""])
    for build in builds:
        lines.extend([f"### {build.title} ({build.slug})", ""])
        if build.risky:
            lines.append("This variant uses `dict:///` links. Test it last.")
            lines.append("")
        lines.extend(f"- {action}: " for action in TEST_ACTIONS)
        lines.append("")
    (output_dir / "TESTING_CHECKLIST.md").write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
