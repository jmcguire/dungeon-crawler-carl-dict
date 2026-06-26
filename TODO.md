# TODO

This file is prioritized by what is most likely to improve real reader lookup behavior.

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

## Unsorted Stuff

- we should look deeper for more sentences if the intro isn't enough.
  - "Cut! Achievement" has just the one sentence. we can easily add the second.

- when building the different dictionaries, why does kindle and kobo have 1205 but stardict has 1212. they have the same number of aliases

- get more aliases from Special:ListRedirects .

- get rid of badge tests, add more tests elsewhere, on code that matters

- if i want to start releasing dictionaries for other fandoms, how do i organize that? can i still do it on a github.io page, or should i start with my own domain? how would i reorganize the processes and the builds and the releases? what's the information architecture? at what point to a run afoul of copywrite? i want to make sure the original authors are respected on each fandom page. does that mean individual buy-in? probably? maybe a contact page for an admin to request a fandom be added, then a separate page for an individual to ask for a one-off build, just for them (that i won't publish).

## P3 - Verbosity options

- "outputs" will just print output location(s) and nothing.
- "smaller" for basic steps, things like "number words found", plus warnings.
- "full" for a word-by-word log of what happened and which aliases were produced.

all levels should include big errors.

i kinda want colors. is that bad? do they downgrade nicely?

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
