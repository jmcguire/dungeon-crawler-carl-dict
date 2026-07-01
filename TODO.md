# TODO

This file is prioritized by what is most likely to improve real reader lookup behavior.

## P3 - Release And User Experience Polish

- Run the Kobo internal-link physical-device experiment.
  - Build experiment dictionaries with `./bin/build_kobo_link_experiments`.
  - Test one ZIP at a time on a real Kobo.
  - Use the generated `TESTING_CHECKLIST.md` to record whether fragment, shard-relative, or `dict:///` links work.
  - Do not add Kobo links to release output unless one variant reliably opens the intended dictionary entry without blank views, browser handoff, crashes, or firmware-specific setup.

## P3 - Portability And Future Formats

- Review whether generic first-name/last-name aliases should be configurable by fandom.
  - This may help wikis with many human names.
  - It may also create noisy collisions, so keep it collision-protected and opt-in if expanded.

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
