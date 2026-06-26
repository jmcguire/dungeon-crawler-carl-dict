# Dungeon Crawler Carl E-reader Dictionaries

<img width="100%" alt="Dungeon Crawler Carl dictionary for e-readers. Automatically generated from fandom wiki. Create your own with this codebase." src="https://raw.githubusercontent.com/jmcguire/dungeon-crawler-carl-dict/main/banner.jpg">

A free custom e-reader dictionary for *Dungeon Crawler Carl*.

[![Python](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2Fjmcguire%2Fdungeon-crawler-carl-dict%2Fmain%2Fbadges%2Fpython.json)](#developer-tools)
[![Release](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2Fjmcguire%2Fdungeon-crawler-carl-dict%2Fmain%2Fbadges%2Frelease.json)](https://github.com/jmcguire/dungeon-crawler-carl-dict/releases)
[![Licenses](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2Fjmcguire%2Fdungeon-crawler-carl-dict%2Fmain%2Fbadges%2Flicenses.json)](#license-and-attribution)
[![Coverage](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2Fjmcguire%2Fdungeon-crawler-carl-dict%2Fmain%2Fbadges%2Fcoverage.json)](#tests-and-badges)
[![Output](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2Fjmcguire%2Fdungeon-crawler-carl-dict%2Fmain%2Fbadges%2Foutput.json)](#release-workflow)

This project lets you look up Dungeon Crawler Carl characters, factions, races, spells, achievements, items, and other terms directly from an e-reader dictionary panel. It gets the entries, with permission, from the [Dungeon Crawler Carl Wiki](https://dungeon-crawler-carl.fandom.com/wiki/Dungeon_Crawler_Carl_Wiki).

## Just Want The Dictionary?

Go here for a download and installation guide: <https://jmcguire.github.io/dungeon-crawler-carl-dict/>

That site has the reader-facing instructions for Kindle, Kobo, KOReader, and BOOX.

This README is for developers, with notes on crawling, building, testing, and releasing the dictionary.

## Supported Formats

This repository builds:

- Kindle `.mobi`
- StarDict for KOReader and BOOX
- Kobo `dicthtml`
- Release ZIPs with install notes, license, attribution, checksums, and build metadata

## Developer Tools

Required:

- Python 3.11 or newer recommended
- No Python package dependencies for the core tooling
- Git

Optional, depending on what you are building:

- Kindle Previewer 3 for Kindle `.mobi` compilation. Amazon no longer distributes `kindlegen` as a separate supported download; Kindle Previewer includes it at:

```text
/Applications/Kindle Previewer 3.app/Contents/lib/fc/bin/kindlegen
```

- `dictgen` from [pgaskin/dictutil](https://github.com/pgaskin/dictutil) for Kobo `dicthtml` compilation.
- GitHub CLI `gh` for publishing tagged GitHub Releases.

## How it basically works

 - **fetch_entries** crawls specific categories of a specific fandom page, and it stores the results in a SQLite database. It stores the entire page and some meta information. This way you don't have to recrawl it everytime you update some rules in the builder.
 - optional **build_kindle** builds a kindle dictionary from the entries in the SQLite database. Now you can test it in your local e-reader.
 - **release** builds the dictionaries (you don't have to do the build commands, this does it for you) and creates a release. You do have to manage the version numbers on your own. Since this is a personal project, I'm the only one who'll be doing a release for now. But feel free to fork this and do your own thing.

That's the core of it. The SQLite database is in data/. Build artifacts are in build/.

## Workflow

The default workflow builds the official Dungeon Crawler Carl dictionary from `configs/dungeon-crawler-carl.json`.
The `bin/` commands are small repo-local wrappers around the Python modules, so no package installation step is required.

Fetch DCC wiki pages into SQLite:

```sh
./bin/fetch_entries --ignore-robots
```

Build local outputs to test on your own devices:

```sh
./bin/build_kindle_dictionary --link-entries --compile
./bin/build_stardict_dictionary --link-entries
./bin/build_kobo_dictionary
```

Run tests and update badges:

```sh
python3 -m unittest discover -s tests
./bin/badges --version 0.7.0
```

Create a local release bundle:

```sh
./bin/release --version 0.7.0 --link-entries
```

Create a release bundle, tag it, and publish it on GitHub (requires **gh**):

```sh
./bin/release --version 0.7.0 --link-entries --publish
```

Notes:

- The release command is currently DCC-specific.
- The fetch/build commands work with other Fandom configs via `--config`.
- `--publish` requires `gh`, an authenticated GitHub session, and `HEAD == origin/main`.
- Release tags are the public dictionary version. The Python package itself does not have a separate version.

## Using Another Fandom

This project has been tested with the Ice and Fire fandom, and the config is included in this repo. You can see how it works with these commands:

```sh
./bin/fetch_entries --config examples/iceandfire.json --ignore-robots
./bin/build_kindle_dictionary --config examples/iceandfire.json --link-entries
./bin/build_stardict_dictionary --config examples/iceandfire.json --link-entries
./bin/build_kobo_dictionary --config examples/iceandfire.json
```

To use it any fandom, I recommend copying a config file, editing it to your needs, and passing that in to the commands. You can use command-line options for almost all of it, but those get unweildy, so I recommend building the config.

The most important step will be finding good categories to crawl. Start from a Fandom wiki's `Special:Categories` page and choose broad direct page categories first, such as `Characters`, `Items`, `Locations`. The code does not do not recursively descend categories, however many Fandom pages already appear in several categories, so recursive traversal is often unnecessary. Do a bit of research on your own.

The generic path should produce a good-but-not-refined dictionary. Fandom-specific executable code, via plugins, is not supported yet.

## Config Basics

Configs are standard JSON files. The DCC config lives at `configs/dungeon-crawler-carl.json`; `examples/iceandfire.json` is a working alternate-fandom example.

Important fields:

- `fandom`: Fandom slug, such as `dungeon-crawler-carl` or `iceandfire`.
- `title`, `author`, `source_name`: output metadata and source labels.
- `categories`: MediaWiki category names to fetch.
- `database_path`: SQLite crawl output.
- `build_dir`: root build directory for Kindle, StarDict, and Kobo outputs.
- `sidebar_fields`: infobox fields to extract, with alias fields marked by `"alias": true`.
- `title_aliases`: safe suffix/prefix lookup rules, parenthetical stripping, and noisy words to ignore for component aliases.
- `max_summary_length`: optional summary trim length.
- `smoke_headwords`: representative lookups for format inspectors.
- `kobo_output_name`: Kobo dictionary ZIP filename.

Minimal shape:

```json
{
  "fandom": "dungeon-crawler-carl",
  "title": "Dungeon Crawler Carl Dictionary",
  "author": "Generated from Dungeon Crawler Carl Wiki contributors",
  "source_name": "Dungeon Crawler Carl Wiki",
  "categories": ["Characters", "Groups", "Spells"],
  "database_path": "data/dungeon-crawler-carl.sqlite",
  "build_dir": "build/dungeon-crawler-carl",
  "sidebar_fields": [
    {"source": "aliases", "label": "Aliases", "alias": true},
    {"source": "race", "label": "Race"}
  ],
  "title_aliases": {
    "suffixes": [" Spell", " Box"],
    "prefixes": ["Potion of "],
    "strip_parenthetical": true,
    "component_ignore_words": ["Corporation", "Club", "Achievement", "Spell", "Box"]
  },
  "max_summary_length": 600,
  "smoke_headwords": ["Carl", "Donut", "Mordecai"],
  "kobo_output_name": "dicthtml-dc.zip"
}
```

## Build Behavior Notes, for curious developers

The crawler uses the MediaWiki API, records pages in SQLite, respects `robots.txt` unless `--ignore-robots` is passed, sleeps between requests with jitter, retries temporary HTTP failures with exponential backoff, records errors, and resumes previous successful fetches unless `--refresh` is passed.

Re-extract definitions from stored HTML without touching the network:

```sh
./bin/fetch_entries --reextract-only
```

The entry pipeline extracts the first useful summary text, strips wiki maintenance boxes and citation markers, preserves safe bold/italic inline formatting, adds conservative sidebar details, repairs forwarding-only entries, trims overlong summaries, and skips low-quality final definitions while preserving raw crawled data.

Aliases are discovered from safe title rules, selected sidebar aliases, recognized intro parentheticals, first bold intro names, first names for simple two-word `Characters` entries, possessive forms for `Characters` lookups, conservative plural forms for obvious race/mob/item/group nouns, conservative component-word fallbacks for multi-word titles, and a small older human-name heuristic. Single-target Kindle aliases are emitted as `idx:iform` inflections. StarDict uses `.syn` aliases, and Kobo uses variants. If a lookup collides with a real entry, Kindle emits multiple lookup entries while StarDict and Kobo use one combined result.

With `--link-entries`, known entry names inside definitions become internal links. Kindle links work when opening the dictionary directly as a book, but may not work inside Kindle's lookup popup UI.

Default DCC outputs:

- `data/dungeon-crawler-carl.sqlite`
- `build/dungeon-crawler-carl/kindle/Dungeon-Crawler-Carl-Dictionary.xhtml`
- `build/dungeon-crawler-carl/kindle/Dungeon-Crawler-Carl-Dictionary.opf`
- `build/dungeon-crawler-carl/kindle/Dungeon-Crawler-Carl-Dictionary.mobi`
- `build/dungeon-crawler-carl/stardict/`
- `build/dungeon-crawler-carl/kobo/dicthtml-dc.zip`

## Release Workflow

The release command snapshots the current SQLite database, re-extracts descriptions from stored HTML, runs tests and entry audit, builds the selected formats, smoke-tests finished artifacts, writes checksums, and packages assets atomically into `dist/vX.Y.Z/`.

By default it builds all formats.

For faster local checks:

```sh
./bin/release --version 0.7.0 --link-entries --format kindle
./bin/release --version 0.7.0 --link-entries --format stardict
./bin/release --version 0.7.0 --link-entries --format kobo
```

But a published release must use all formats, so the latest-download links stay complete.

Release assets:

- `Dungeon-Crawler-Carl-Dictionary.mobi`
- `Dungeon-Crawler-Carl-Dictionary.zip`
- `Dungeon-Crawler-Carl-Dictionary-StarDict.zip`
- `dicthtml-dc.zip`
- `Dungeon-Crawler-Carl-Dictionary-Kobo.zip`
- `SHA256SUMS.txt`
- `release-manifest.json`

Each bundle includes format-specific installation notes, `LICENSE`, and `ATTRIBUTION.md`.

## Fan-Project Notes

This is an unofficial fan project. It is not affiliated with, authorized by, endorsed by, or sponsored by Matt Dinniman, the Dungeon Crawler Carl rights holders, Amazon, Kindle, Kobo, KOReader, BOOX, Fandom, or any related publisher or platform.

This project *has* been approved by the Dungeon Crawler Carl fandom admins.

## License And Attribution

This repository uses a split-license model:

- Code and documentation are licensed under the MIT License. See `LICENSE`.
- Generated dictionary content that incorporates Fandom wiki text is licensed under CC BY-SA 3.0. See `ATTRIBUTION.md`.

Generated entries include source links back to the configured Fandom wiki pages where practical.
