# Agent Instructions

These instructions are for AI agents working in this repository. Follow them unless the user explicitly says otherwise.

## Project Priorities

- Build release-quality e-reader dictionaries from wiki content.
- Preserve attribution and licensing information for both code and source content.
- Keep the tools useful for other Fandom wikis where practical, but do not over-engineer abstractions before the next real use case appears.
- Prefer repeatable local commands over manual release steps. This keeps the project understandable for a solo maintainer.

## Working Style

- Keep changes scoped to one concern at a time. The user prefers a clean git history with separate commits for separate concerns.
- Commit only when the user explicitly asks for a commit. When committing, use one concise commit message unless the user requests otherwise.
- Do not create badge-only commits unless the user explicitly asks. Badge updates should travel with the feature, release, or documentation change that made them stale.
- Do not push, publish, tag, or create GitHub releases unless the user explicitly asks.
- GitHub Issues are the project's sole backlog. When completing an issue, update or close it in the same change unless the user asks otherwise.
- Keep exploratory research with its active GitHub issue. Do not retain unreferenced root-level scratch notes after their useful findings have been migrated.

## Dependencies

- Prefer Python standard-library solutions.
- Avoid third-party runtime, test, and development dependencies unless the user approves a clearly worthwhile exception. The low-dependency setup is intentional.

## Architecture

- Keep crawler/network behavior, extraction, shared normalized entries, and format-specific rendering separate. Formats must not depend on one another's file structures.
- Avoid major reshuffles or Kindle-specific shared assumptions without a concrete new use case. More fandoms and e-reader formats are likely.
- Preserve CLI compatibility unless the user approves a breaking change.

## Data And Content

- SQLite is source material for builds and releases. Retain raw crawled data even when output-quality filters omit an entry.
- Preserve useful inline formatting such as bold and italics where supported by the target format.
- Treat wiki text as CC BY-SA content. Keep `ATTRIBUTION.md`, source links, and attribution behavior intact.

## Testing And Verification

- Run focused tests for small changes and the full suite for broad changes.
- For release, packaging, output-format, entry-loading, extraction, or badge changes, run:

```sh
python3 -m unittest discover -s tests
```

- If changing Kindle output, validate XHTML/OPF generation and, when available, compile and smoke-test the MOBI.
- If changing StarDict output, run the StarDict inspector and verify aliases, source attribution, and representative lookups.
- If adding or changing an output format, include user instructions for both installing the dictionary and selecting/enabling it in the target reader UI.
- If changing release packaging, run a local packaging smoke test before reporting success.

## Release Versioning

- Treat GitHub Release tags as the only public project version.
- Do not add or maintain a separate Python package version such as `__version__`.
- Release commands require an explicit SemVer value and normalize it to a `vX.Y.Z` tag.
- Use the release command only after badge files are current and committed.

## Badges

- Coverage, Python, license, and output badges are tracked Shields endpoint JSON files in `badges/`; the Release badge comes from GitHub Releases.
- When changing tests, release packaging, dictionary output, formats, licenses, output counts, or release-version examples, run:

```sh
./bin/badges --input data/dungeon-crawler-carl.sqlite
```

- Include changed badge JSON in the related commit. Do not add automation that creates badge-only commits.

## Git Hygiene

- Preserve user changes and do not use destructive Git commands without explicit direction.
- Keep generated release artifacts out of commits unless intentionally tracked, and confirm the staged scope before committing.
