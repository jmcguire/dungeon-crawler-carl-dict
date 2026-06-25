# TODO

This file is prioritized by what is most likely to improve real reader lookup behavior.

## P0 - Understand Kindle Lookup Failures

- Build a Kindle lookup diagnostic workflow.
  - Goal: separate dictionary-index problems from Kindle UI selection behavior.
  - Create a tiny controlled test book and tiny controlled dictionary with known entries, aliases, inflections, duplicate lookup entries, punctuation cases, lowercase cases, possessives, and multi-word phrases.
  - Test on the physical Kindle, not just Kindle Previewer.
  - Record which selections show Dictionary, X-Ray, Wikipedia, Translate, Search, or no lookup tab.
  - Known confusing case: selecting multi-word proper nouns can suppress the Dictionary tab entirely. Kindle may show X-Ray, Wikipedia, Translate, or Search instead because it decided the selection is a phrase/entity, not a dictionary lookup candidate.
  - Examples to test manually: `The Valtay Corporation`, `Valtay Corporation`, `Valtay`, `Gwendolyn Duet`, `Desperado Club`, `dirigible gnomes`, `Heal spell`, `Heal Spell`, `Carl's`.
  - Also test lowercase, punctuation, apostrophes, periods attached to selections, text inside italics, and single quotes encoded as `&#x27;` in Kindle XHTML.
  - Output: a short markdown report plus any fixture files needed to repeat the test.

- Decide whether lowercase, punctuation, possessives, or other true grammatical forms need additional Kindle indexing.
  - Use the diagnostic workflow results before changing output.
  - Possible output strategies: lowercase `idx:iform` values, possessive `idx:iform` values, or no change if Kindle already normalizes the selection.

## P1 - Kindle XHTML And Tooling

- Add a Kindle XHTML/index validation command.
  - Goal: catch Kindle lookup-shape mistakes before building or releasing.
  - Validate that every `idx:orth` and `idx:iform` value is stripped, nonempty, and free of control characters.
  - Validate that single-target aliases use `idx:iform`.
  - Validate that multi-target lookups use duplicate visible `idx:entry` blocks.
  - Validate that stale direct duplicate alias entries are not emitted for normal aliases.
  - Validate that unsupported alias constructs such as `idx:orth type="silent"` do not appear.
  - Validate that expected representative headwords and aliases exist.
  - Integrate this validation into build or release smoke tests.
  - Output clear failure messages pointing to the bad entry or alias.

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

- Improve multi-word lookup aliases.
  - Goal: make common partial selections work better without creating noisy aliases.
  - Use the Fictionary-style idea that a multi-word fictional term may need lookup help for meaningful component words.
  - Keep this conservative and collision-safe.
  - Prioritize `Characters` entries.
  - Avoid stopwords, honorifics, articles, and generic type words.
  - Preserve multi-target lookup behavior when one lookup word maps to multiple entries.
  - Keep exact canonical titles ranked first.
  - Candidate examples: `Gwendolyn Duet`, `Valtay Corporation`, `Borant Corporation`, `Desperado Club`.
  - Do not create broad first-word aliases for every title.

## P3 - Release And User Experience Polish

- Study Fictionary presentation and spoiler UX.
  - Goal: borrow good reader-facing ideas without copying proprietary dictionary content.
  - Review public-facing Fictionary material at <https://www.thefictionary.net/home>.
  - Look specifically for spoiler-level organization, entry phrasing, multi-word term behavior, source/series/book labeling, and install/help-page language.
  - Turn useful findings into separate implementation TODOs only if they clearly improve this project.
  - Avoid copying paid/proprietary entry text, styling, or private dictionary files.

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

## Unsorted Stuff

- we should look deeper for more sentences if the intro isn't enough.
  - "Cut! Achievement" has just the one sentence. we can easily add the second.

- when building the different dictionaries, why does kindle and kobo have 1205 but stardict has 1212. they have the same number of aliases

- get more aliases from Special:ListRedirects .

- get rid of badge tests, add more tests elsewhere, on code that matters

- if i want to start releasing dictionaries for other fandoms, how do i organize that? can i still do it on a github.io page, or should i start with my own domain? how would i reorganize the processes and the builds and the releases? what's the information architecture? at what point to a run afoul of copywrite? i want to make sure the original authors are respected on each fandom page. does that mean individual buy-in? probably? maybe a contact page for an admin to request a fandom be added, then a separate page for an individual to ask for a one-off build, just for them (that i won't publish).


## Reference Notes

- Current normalized-entry scan results:
  - Present as entries: `Lucia Mar`, `Miss Quill`, `Skull Empire`, `King Rust`, `Katia Grim`, `Kua-Tin`, `Sheol Glass Reaper Case`, `Heal Spell`.
  - Present through existing suffix alias: `Suppurating Eye` -> `Suppurating Eye Spell`.
  - Not present: `Shambling Berserker`, `The Final War`, `The Final War Spell`, `street urchin`, `Kua-Tin Company`.
  - Present but missing desired aliases: `Valtay Corporation`, `Borant Corporation`, `Ferdinand`, `Stalwart`, `Bautista`, `Saccathian`, `Nullian`, `Brain Boiler`, `Grimaldi`.

- Useful local checks:

```sh
grep idx:orth build/dungeon-crawler-carl/kindle/dictionary.xhtml | perl -nE'/value="([^"]+)"/; say $1'
./bin/build_kindle_dictionary --link-entries --compile
./bin/release --version 0.5.0 --link-entries --format all --overwrite
```
