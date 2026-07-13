# Agent Instructions

These instructions are for AI agents working in this repository. Follow them unless the user explicitly says otherwise.

## Project Priorities

- Build release-quality e-reader dictionaries from wiki content.
- Preserve attribution and licensing information for both code and source content.
- Keep the tools useful for other Fandom wikis where practical, but do not over-engineer abstractions before the next real use case appears.
- Prefer repeatable local commands over manual release steps. This keeps the project understandable for a solo maintainer.

## Working Style

- When the user asks for an implementation, do the work end to end: inspect, edit, test, and report.
- Before large or ambiguous changes, provide a short plan and ask only the questions that materially affect the result.
- Keep changes scoped to one concern at a time. The user prefers a clean git history with separate commits for separate concerns.
- Commit only when the user explicitly asks for a commit. When committing, use one concise commit message unless the user requests otherwise.
- Do not create badge-only commits unless the user explicitly asks. Badge updates should travel with the feature, release, or documentation change that made them stale.
- Do not push, publish, tag, or create GitHub releases unless the user explicitly asks.
- GitHub Issues are the project's sole backlog. When completing an issue, update or close it in the same change unless the user asks otherwise.
- Keep exploratory research with its active GitHub issue. Do not retain unreferenced root-level scratch notes after their useful findings have been migrated.

## Dependencies

- Prefer Python standard-library solutions.
- Avoid adding third-party runtime dependencies. If one seems genuinely useful, explain the tradeoff and ask before adding it.
- Test-only or development dependencies also require user approval. The low-dependency setup is an intentional project feature.

## Architecture

- Keep crawler/network behavior separate from extraction, normalized entry handling, and output rendering.
- Keep format-specific rendering isolated from shared entry logic. Kindle and StarDict should not depend on each other's file formats.
- Do not move major responsibilities between modules without a clear reason. The architecture may change as support for other fandoms and formats grows.
- Future e-reader formats are likely, so avoid hard-coding Kindle assumptions into shared data structures.
- Preserve CLI compatibility unless the user approves a breaking change.

## Data And Content

- The SQLite database is source material for builds and releases. Do not delete crawled page data just because an entry is omitted from one output format.
- Dictionary quality filters should skip bad generated entries at render/build time while retaining raw crawled data for later improvement.
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

- Coverage, Python, license, and output badges are tracked Shields endpoint JSON files in `badges/`.
- The Release badge comes from GitHub Releases and should not have a tracked `badges/release.json`.
- When changing tests, release packaging, dictionary output, supported formats, licenses, output counts, or release version examples, run:

```sh
./bin/badges --input data/dungeon-crawler-carl.sqlite
```

- Include any changed badge JSON files in the same commit as the related work.
- Do not add a GitHub Actions workflow just to update badges. The current strategy intentionally avoids automatic badge-only commits.

## Git Hygiene

- Never revert user changes unless the user asks.
- Do not use destructive git commands such as `git reset --hard` or `git checkout --` unless explicitly requested.
- Keep generated release artifacts out of commits unless they are intentionally tracked project files.
- Before committing, check `git status` and make sure the staged changes match the requested concern.
