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

## P1 - Bugs

When building the different dictionaries, why does kindle and kobo have 1205 but stardict has 1212. they have the same number of aliases

## P3 - Release And User Experience Polish

- Research Kobo internal links.
  - Kobo output does not currently include internal dictionary links.
  - Determine whether Kobo dicthtml supports reliable in-dictionary links before adding them.

## P3 - Portability And Future Formats

- Review whether generic first-name/last-name aliases should be configurable by fandom.
  - This may help wikis with many human names.
  - It may also create noisy collisions, so keep it collision-protected and opt-in if expanded.

## P3 - clean up tests

- get rid of badge tests, add more tests elsewhere, on code that matters

## P3 - Next steps for working with other fandoms?

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
./bin/build_kindle_dictionary --link-entries --compile
./bin/release --version 0.5.0 --link-entries --format all --overwrite
```
