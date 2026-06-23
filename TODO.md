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

  - Candidate next steps: possessives, lowercase forms, plural forms such as `Brain Boilers`.

## P3 - Release And User Experience Polish

- Research Kobo internal links.
  - Kobo output does not currently include internal dictionary links.
  - Determine whether Kobo dicthtml supports reliable in-dictionary links before adding them.

## P3 - Portability And Future Formats

- Consider fandom-specific code hooks if JSON config stops being enough.
  - Current config covers categories, sidebar fields, source labels, title alias rules, output paths, smoke headwords, and overlong-summary trimming.
  - Executable plugins are intentionally deferred until a second real fandom needs behavior that cannot be expressed as data.

- Review whether generic first-name/last-name aliases should be configurable by fandom.
  - This may help wikis with many human names.
  - It may also create noisy collisions, so keep it collision-protected and opt-in if expanded.

- Research other dictionary tooling when useful.
  - Review `kindling`: <https://github.com/ciscoriordan/kindling>
  - Look for Kindle XHTML/indexing tricks that could improve lookup reliability.
  - Future format candidates should be justified by reader demand and side-loading practicality.
  - look at dictionaries in https://www.thefictionary.net/home , see how they look and if we can copy their best ideas

## Unsorted Stuff

- i think we've gone far enough that we should use folders in the dcdict.
  - and also, the dcdict should be renamed to something more generic, like fandom-dictionary-creation, or something like that.

- when building the different dictionaries, why does kindle and kobo have 1205 but stardict has 1212. they have the same number of aliases

- i want to know how the code files relate to each other. which one uses which?

- the names of the dictionaries, the ones that get dragged into the ebook folder, should be more descriptive than "dictionary.mobi" it should have the fandom name or a title with - separators.

The following are not even triggering a dictionary:
 - Gwendolyn Duet
 - Desperado Club
 - dirigible gnomes

if i want to start releasing dictionaries for other fandoms, how do i organize that? can i still do it on a github.io page, or should i start with my own domain? how would i reorganize the processes and the builds and the releases? what's the information architecture? at what point to a run afoul of copywrite? i want to make sure the original authors are respected on each fandom page. does that mean individual buy-in? probably? maybe a contact page for an admin to request a fandom be added, then a separate page for an individual to ask for a one-off build, just for them (that i won't publish).


## Reference Notes

- Current normalized-entry scan results:
  - Present as entries: `Lucia Mar`, `Miss Quill`, `Skull Empire`, `King Rust`, `Katia Grim`, `Kua-Tin`, `Sheol Glass Reaper Case`, `Heal Spell`.
  - Present through existing suffix alias: `Suppurating Eye` -> `Suppurating Eye Spell`.
  - Not present: `Shambling Berserker`, `The Final War`, `The Final War Spell`, `street urchin`, `Kua-Tin Company`.
  - Present but missing desired aliases: `Valtay Corporation`, `Borant Corporation`, `Ferdinand`, `Stalwart`, `Bautista`, `Saccathian`, `Nullian`, `Brain Boiler`, `Grimaldi`.

- Useful local checks:

```sh
grep idx:orth build/dungeon-crawler-carl/kindle/dictionary.xhtml | perl -nE'/value="([^"]+)"/; say $1'
python3 -m dcdict.build_kindle_dictionary --link-entries --compile
python3 -m dcdict.release --version 0.5.0 --link-entries --format all --overwrite
```
