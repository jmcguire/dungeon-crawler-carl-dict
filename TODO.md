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

## P2 - Improve Kindle Indexing

- Add inflection support for selected possessives and other true grammatical forms.
  - Example shape:

```xml
<idx:orth>Carl</idx:orth>
<idx:infl>
  <idx:iform value="carl's" />
</idx:infl>
```

  - Kindle lookup aliases now use `idx:iform` inflections instead of duplicated visible entries.
  - Candidate next steps: possessives, lowercase forms, plural forms such as `Brain Boilers`.

- Support multiple entries for one lookup word.
  - Example: `Earth` and `Earth Box`.
  - Today ambiguous aliases are omitted; that is safe but can hide useful lookup results.
  - Research how Kindle, StarDict, and Kobo should represent multiple definitions under one lookup.

- Revisit additional suffix/prefix alias rules.
  - Possible suffixes: ` Achievement`, ` Potion`.
  - Possible prefix: `Potion of ...`.
  - Only add these after collision tests and manual spot checks; previous broad aliasing created bad one-word aliases.

## P3 - Release And User Experience Polish

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
