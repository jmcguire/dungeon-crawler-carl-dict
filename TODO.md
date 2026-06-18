# TODO

This file is prioritized by what is most likely to improve real reader lookup behavior.

## P0 - Understand Kindle Lookup Failures

- Build a small Kindle lookup diagnostic workflow.
  - Goal: separate dictionary-index problems from Kindle UI selection behavior.
  - Check generated `idx:orth` values, compiled MOBI indexes, and exact selected text examples.
  - Known confusing case: selecting multi-word proper nouns can suppress the Dictionary tab entirely. Kindle may show X-Ray, Wikipedia, Translate, or Search instead because it decided the selection is a phrase/entity, not a dictionary lookup candidate.
  - Examples to test manually: `The Valtay Corporation`, `Valtay Corporation`, `Valtay`, `Heal spell`, `street urchin`, `Kua-Tin`, `Lucia Mar`.

- Investigate whether lowercase, punctuation, apostrophes, or surrounding markup affect Kindle lookup.
  - Examples: `Heal spell` versus `Heal Spell`, periods attached to selections, and text inside italics.
  - Check whether single quotes encoded as `&#x27;` in Kindle XHTML affect lookup display or matching.
  - Decide whether to add lowercase variants as direct aliases or `idx:infl` entries.

## P1 - Improve Alias Coverage

- Add an explicit alias system separate from suffix stripping.
  - Current automatic aliases only remove trailing ` Box` or ` Spell`.
  - Add a small curated alias source for important DCC names that do not fit generic rules.
  - The same alias data should feed Kindle direct headwords, StarDict `.syn`, and Kobo variants.

- Add high-confidence curated aliases.
  - `Valtay` -> `Valtay Corporation`
  - `Borant` -> `Borant Corporation`
  - `Gravy Boat` -> `Ferdinand`
  - `Prince Stalwart` -> `Stalwart`
  - `Daniel Bautista` -> `Bautista`
  - `Sac` -> `Saccathian`
  - `Null` -> `Nullian`
  - `Brain Boilers` -> `Brain Boiler`
  - `Ringmaster Grimaldi` -> `Grimaldi`
  - `Katia` -> `Katia Grim`

- Consider sidebar-derived aliases.
  - Some pages already expose useful alias fields, such as `Valtay Corporation` listing `The Valtay, The Brain Worms`.
  - Parse sidebar aliases into lookup aliases only after collision handling is clear.
  - This could also find cases like `Saccathian` / `Sac` if the wiki sidebar contains them.

- Consider conservative human-name aliases.
  - For clear human full names, support first-name or last-name lookup only when there is no collision.
  - Start with explicit tests for `Katia Grim` -> `Katia`.
  - Do not broadly alias every two-word title; many DCC names are groups, items, spells, or places.

## P1 - Fix Known Missing Or Misnamed Entries

- Investigate true missing entries.
  - `Shambling Berserker`
  - `The Final War` / `The Final War Spell`
  - These are not present in the current normalized entries.

- Fix parenthetical disambiguation where it is clearly noise.
  - `Torch (Item)` should display and index as `Torch`, unless another `Torch` entry exists.
  - Add collision protection before stripping parenthetical suffixes.

- Review specific low-quality or confusing entries.
  - `Krakaren`
  - `Krakaren Clone (Fourth Floor)`
  - `Krakaren Clone (Second Floor)`
  - Determine whether these need better extraction, aliases, disambiguation, or manual cleanup.

## P2 - Improve Default Crawl Scope

- Make the default categories match the normal DCC build.
  - Current manual crawl examples use multiple categories such as `Characters`, `Groups`, `Spells`, `Achievements`, and `Items`.
  - Decide the default set for this project while keeping CLI overrides generic for other Fandom wikis.

- Rename `fetch_characters` to `fetch_entries`.
  - Keep a compatibility wrapper or module alias so existing commands do not break abruptly.
  - Update README examples and tests when this happens.

- Add defensive subcategory loop protection.
  - MediaWiki categories can theoretically loop.
  - DCC does not appear to trigger this, so this is defensive rather than urgent.

## P2 - Improve Kindle Indexing

- Add inflection support for selected aliases and possessives.
  - Example shape:

```xml
<idx:orth>Carl</idx:orth>
<idx:infl>
  <idx:iform value="carl's" />
</idx:infl>
```

  - Use this for true inflections, not as a replacement for direct lookup aliases.
  - Candidate cases: possessives, lowercase forms, plural forms such as `Brain Boilers`.

- Support multiple entries for one lookup word.
  - Example: `Earth` and `Earth Box`.
  - Today ambiguous aliases are omitted; that is safe but can hide useful lookup results.
  - Research how Kindle, StarDict, and Kobo should represent multiple definitions under one lookup.

- Revisit additional suffix/prefix alias rules.
  - Possible suffixes: ` Achievement`, ` Potion`.
  - Possible prefix: `Potion of ...`.
  - Only add these after collision tests and manual spot checks; previous broad aliasing created bad one-word aliases.

- Change Kindle OPF/XML identifier to include dictionary name plus release version.
  - This may help Kindle treat updates as the same dictionary with a newer build.
  - Coordinate with release versioning; do not add a separate Python package version.

## P3 - Release And User Experience Polish

- Improve GitHub release notes for non-expert users.
  - Make the download choice clearer: Kindle, KOReader/StarDict, or Kobo.
  - Keep installation steps short and device-specific.
  - Consider whether the Kindle release should emphasize the single `.mobi` more than the ZIP.

- Improve README troubleshooting for Kindle lookup tabs.
  - Explain that the Dictionary tab sometimes disappears for multi-word phrases or proper nouns.
  - Explain that selecting a shorter phrase may produce different tabs.
  - Mention that X-Ray results can appear even when the custom dictionary is not searched.

- Research Kobo internal links.
  - Kobo output does not currently include internal dictionary links.
  - Determine whether Kobo dicthtml supports reliable in-dictionary links before adding them.

## P3 - Portability And Future Formats

- Test the crawler/build pipeline against a different Fandom wiki.
  - Goal: produce a good-but-not-refined dictionary without DCC-specific assumptions.
  - Identify which extraction or alias rules need fandom-specific configuration.

- Research other dictionary tooling when useful.
  - Review `kindling`: <https://github.com/ciscoriordan/kindling>
  - Look for Kindle XHTML/indexing tricks that could improve lookup reliability.
  - Future format candidates should be justified by reader demand and side-loading practicality.

## Reference Notes

- Current normalized-entry scan results:
  - Present as entries: `Lucia Mar`, `Miss Quill`, `Skull Empire`, `King Rust`, `Katia Grim`, `Kua-Tin`, `Sheol Glass Reaper Case`, `Heal Spell`.
  - Present through existing suffix alias: `Suppurating Eye` -> `Suppurating Eye Spell`.
  - Not present: `Shambling Berserker`, `The Final War`, `The Final War Spell`, `street urchin`, `Kua-Tin Company`.
  - Present but missing desired aliases: `Valtay Corporation`, `Borant Corporation`, `Ferdinand`, `Stalwart`, `Bautista`, `Saccathian`, `Nullian`, `Brain Boiler`, `Grimaldi`.

- Useful local checks:

```sh
grep idx:orth build/dictionary.xhtml | perl -nE'/value="([^"]+)"/; say $1'
python3 -m dcdict.build_kindle_dictionary --link-entries --compile
python3 -m dcdict.release --version 0.5.0 --link-entries --format all --overwrite
```
