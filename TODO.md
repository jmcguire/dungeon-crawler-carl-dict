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

- Test the crawler/build pipeline against a different Fandom wiki.
  - Goal: produce a good-but-not-refined dictionary without DCC-specific assumptions.
  - Identify which extraction or alias rules need fandom-specific configuration.
  - see iceandfire for an example. There are some hardcoded values for DCC.
    - it also has a lot of parenthetical values, like "Baelon Targaryen (son of Aerys)". we should automaticall get rid of those, it'll be useful in all dictionaries.
    - there is a big feature here to let people program in some of their own title munging methods. like a fandom-specific plugin that's a piece of code.
    - why is "Ermesande Hayford" so long and everything else is too short?
    - should look into history if the entry is too short
    - it has custom prefixes ("House AAAA")
  - add configurable sidebar attributes to look at
  - these values are becoming unweildy to type on the command line. we might want to build a config file that can be used in both commands
  - write up a guide for doing this with another fandom
    - mention that Special:Category page has some good starting points.
    - mention categories don't recursively descend into other categories by default, but here's how to do it, but note that many fandoms just have pages in multiple categories (characters and kings and deceased)
  - change all outputs to have the fandomn ame built in. fandom.sqlite, build/fandom/, etc
  - maybe make aliases to split FirstName LastName to also be searchable on FirstName?

- Research other dictionary tooling when useful.
  - Review `kindling`: <https://github.com/ciscoriordan/kindling>
  - Look for Kindle XHTML/indexing tricks that could improve lookup reliability.
  - Future format candidates should be justified by reader demand and side-loading practicality.

- Should rename dcdict.build_kindle_dictionary to dcdict.build_dictionaries

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
