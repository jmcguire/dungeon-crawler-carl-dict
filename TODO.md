# TODO

This file separates engineering work from tasks that require a person, a physical device, or community judgment.

## Engineering Priorities

### P1 - Book-Aware Spoiler Editions

- Research whether source pages contain enough reliable first-appearance or spoiler-level metadata to produce separate dictionaries by book.
- Do not infer book safety from a page-level spoiler warning alone.
- Prefer an explicit, inspectable metadata source over AI classification.

### P2 - Generic Fandom Releases

- Generalize release packaging only when a second fandom is ready to publish.
- Decide whether one repository should publish multiple fandoms or whether each published dictionary should have its own repository and release feed.
- Keep source attribution, reader documentation, release assets, smoke headwords, and download URLs project-specific.

### P2 - Alias Policy Configuration

- Revisit whether character first-name, possessive, plural, and generic-name rules should be configurable by category.
- Use the Ice and Fire health report as the stress test: useful ambiguity is acceptable, but malformed or generic aliases are not.
- Keep collision handling deterministic and visible in `health_report`.

### P3 - Optional Kindle Tooling

- Keep KindleGen as the official compiler until a Kindling-built dictionary passes physical-device lookup tests.
- See `KINDLING_INVESTIGATION.md` for the completed research and safe experiment boundaries.

## Manual Work For The Maintainer

These are intentionally not automated away.

### Source Wiki Cleanup

- Run `./bin/health_report --verbosity full` and improve the highest-priority wiki cleanup candidates.
- Fix malformed lead markup, truncated sentences, title-only definitions, and generic one-line definitions on the source wiki.
- Add concise non-spoilery lead paragraphs, useful sidebar values, and real wiki redirects where appropriate.
- Re-crawl or run `--reextract-only` after wiki changes, then confirm the candidate disappears from the report.

### Physical Device Testing

- Run the Kobo internal-link experiment with `./bin/build_kobo_link_experiments`, one ZIP at a time, and complete its generated checklist on a real Kobo.
- Keep testing representative Kindle lookups after meaningful alias/index changes; Kindle Previewer cannot establish popup lookup behavior.
- Verify installation and dictionary selection instructions after major Kindle, Kobo, KOReader, or BOOX firmware changes.

### Release Review

- Before publishing, read the small health report and inspect any new alias-quality findings or unusually broad multi-target lookups.
- Confirm the release downloads, install notes, checksums, and homepage status after publishing.
- Read generated release notes for reader-facing clarity; edit them when commit messages do not explain the practical change.

### Community And Rights

- Get wiki-admin approval before publishing a dictionary based on another Fandom community.
- Confirm that the source wiki license and attribution requirements are compatible with redistribution.
- Ask the relevant community whether a public dictionary is wanted; keep one-off private builds private when publication is not appropriate.
- Decide the site and repository information architecture before offering multiple public fandom dictionaries.

### Reader Support

- Review help-form responses for missing entries, incorrect definitions, failed lookups, and outdated installation steps.
- Turn repeated reports into reproducible health checks or tests; keep one-off source-content fixes on the wiki.
