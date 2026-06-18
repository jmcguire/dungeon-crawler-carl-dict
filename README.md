# Dungeon Crawler Carl E-reader Dictionaries

[![Release](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2Fjmcguire%2Fdungeon-crawler-carl-dict%2Fmain%2Fbadges%2Frelease.json)](https://github.com/jmcguire/dungeon-crawler-carl-dict/releases)
[![Coverage](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2Fjmcguire%2Fdungeon-crawler-carl-dict%2Fmain%2Fbadges%2Fcoverage.json)](#tests)
[![Python](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2Fjmcguire%2Fdungeon-crawler-carl-dict%2Fmain%2Fbadges%2Fpython.json)](#requirements)
[![Formats](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2Fjmcguire%2Fdungeon-crawler-carl-dict%2Fmain%2Fbadges%2Fformats.json)](#create-a-release)
[![Licenses](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2Fjmcguire%2Fdungeon-crawler-carl-dict%2Fmain%2Fbadges%2Flicenses.json)](#licensing)
[![Output](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2Fjmcguire%2Fdungeon-crawler-carl-dict%2Fmain%2Fbadges%2Foutput.json)](#create-a-release)

This project builds Kindle, StarDict/KOReader, and Kobo lookup dictionaries from pages on a MediaWiki/Fandom wiki.

The default target is the Dungeon Crawler Carl Fandom character category. The crawler and converter are intentionally generic enough to point at another Fandom wiki and category later.

## Requirements

- macOS for Kindle MOBI compilation; StarDict and Kobo source builds work anywhere Python does
- Python 3.11 or newer recommended
- No Python package dependencies

Optional:

- Kindle Previewer 3 if you want the converter to compile a `.mobi` file automatically.
- `dictgen` from [pgaskin/dictutil](https://github.com/pgaskin/dictutil) if you want to compile a Kobo `dicthtml` dictionary.

## Licensing

This repository uses a split-license model:

- Code and documentation are licensed under the MIT License. See `LICENSE`.
- Generated dictionary content that incorporates Fandom wiki text is licensed under CC BY-SA 3.0. See `CONTENT_LICENSE`.
- Fan-project and rights-holder notices are in `NOTICE`.

Generated dictionary entries are derived from community-contributed Dungeon Crawler Carl Wiki text on Fandom, with source page links included where practical.

## Install Kindle Previewer

Amazon no longer offers `kindlegen` as a separate supported download. Install Kindle Previewer instead:

1. Go to Amazon's Kindle Previewer page:
   <https://kdp.amazon.com/en_US/help/topic/G202131170>
2. Download the macOS installer.
3. Open `KindlePreviewerInstaller.pkg`.
4. Follow the installer prompts.

On macOS, Kindle Previewer 3 installs here:

```text
/Applications/Kindle Previewer 3.app
```

The app bundle includes a legacy `kindlegen` compiler here:

```text
/Applications/Kindle Previewer 3.app/Contents/lib/fc/bin/kindlegen
```

The build script checks that location automatically when you pass `--compile`.

## Workflow

Fetch raw character page data into SQLite:

```sh
python3 -m dcdict.fetch_entries --ignore-robots
```

But if you want more categories, use:

```sh
» python3 -m dcdict.fetch_entries --category Characters --category Groups --category Spells --category Achievements --category Races --category Items --ignore-robots
```

For this DCC Fandom wiki, `robots.txt` disallows `/api.php` for crawlers. The script respects that by default and will stop unless you pass `--ignore-robots`. Use that override only for a user-triggered, polite fetch like this project: low rate, small scope, no indexing, no training, and no repeated hammering.

Build Kindle dictionary source files:

```sh
python3 -m dcdict.build_kindle_dictionary
```

Definitions preserve safe inline emphasis from the wiki where possible: `<b>`/`<strong>` become bold text, and `<i>`/`<em>` become italic text. Other HTML is stripped or escaped during extraction/building.

Lookup aliases are discovered conservatively from the entry data: generic ` Box` and ` Spell` suffix stripping, selected wiki sidebar aliases, recognized intro parentheticals such as `(aka Borant)` or `(actually named "Gravy Boat")`, first bold intro names that differ from the page title, and first/last names for likely human characters. For example, `1914 Box` is also indexed as `1914`, and `Saccathian (or Sacs)` is also indexed as `Sacs`. Kindle aliases are emitted as direct `idx:orth` headwords that share the canonical entry's displayed title and definition. StarDict uses `.syn` aliases, and Kobo uses variants. Ambiguous aliases are omitted rather than routed to an arbitrary entry.

For local testing, pass `--no-sidebar-aliases` to disable aliases derived from wiki sidebars.

Definitions are rendered as a short bullet list. If the source wiki page has a page-level spoiler warning banner, the generated entry places a spoiler note above the bullet so a reader has a chance to stop before reading the summary.  The warning is page-level; the wiki generally does not mark smaller spoiler phrases inside otherwise normal sentences.

When present in the page sidebar's BIOGRAPHICAL INFO section, the dictionary adds a few conservative detail bullets below the summary: aliases, origin, race, and first scene. Noisy or more spoilery sidebar fields such as class, crawler number, crawler ID, and occupation are intentionally omitted.

Optionally add internal cross-links between known dictionary entries:

```sh
python3 -m dcdict.build_kindle_dictionary --link-entries
```

For example, if the `Carl` definition mentions `Donut`, the generated XHTML links `Donut` to Donut's dictionary entry with an internal anchor. The linker only touches text nodes, preserves bold/italic markup, skips self-links, and avoids very short single-word titles to reduce noisy false positives.

Kindle caveat: these links work when opening the dictionary directly as a book, but may not work inside the Kindle lookup popup/card UI. That appears to be a Kindle interface limitation rather than a dictionary build error.

Build the StarDict dictionary used by KOReader:

```sh
python3 -m dcdict.build_stardict_dictionary --link-entries
```

StarDict is a small group of files rather than one native dictionary file. The builder writes the `.ifo`, `.idx`, `.dict`, `.syn`, and `.css` files together under `build/stardict/`. Its `.syn` file provides the same aliases as the Kindle edition. With `--link-entries`, recognized entry names use KOReader's supported `bword://` links.

Build the Kobo dictionary:

```sh
python3 -m dcdict.build_kobo_dictionary
```

Kobo dictionaries are compiled with the external `dictgen` tool. The builder writes `build/kobo/dictionary.df` as an intermediate file and `build/kobo/dicthtml-dc.zip` as the Kobo dictionary file. The same shared aliases are emitted as Kobo variants.

Try to compile with `kindlegen` if it is installed:

```sh
python3 -m dcdict.build_kindle_dictionary --compile
```

On macOS, the build script also checks for the `kindlegen` binary bundled inside Kindle Previewer 3:

```text
/Applications/Kindle Previewer 3.app/Contents/lib/fc/bin/kindlegen
```

Outputs:

- `data/characters.sqlite`: raw crawl output
- `build/dictionary.xhtml`: Kindle dictionary content source
- `build/dictionary.opf`: Kindle package metadata
- `build/dictionary.mobi`: compiled Kindle file, only when `kindlegen` is available
- `build/stardict/`: StarDict files for KOReader
- `build/kobo/dicthtml-dc.zip`: compiled Kobo dictionary file, only when `dictgen` is available

## Tests

Run the focused standard-library test suite:

```sh
python3 -m unittest discover -s tests
```

The tests cover extraction, SQLite loading, Kindle XHTML/OPF generation, StarDict binary generation and inspection, aliases, release packaging, and the Kindle Previewer/`kindlegen` compile wrapper.

Update the tracked README badge metadata before a release-prep commit:

```sh
python3 -m dcdict.badges --version 0.5.0 --input data/characters.sqlite
```

The badge command runs the test suite with Python's standard-library `trace` tool, computes line coverage for `dcdict/`, counts usable dictionary entries, and writes Shields endpoint JSON files under `badges/`. Badge updates are committed with normal project changes; there is no badge-only GitHub Actions commit.

## Create A Release

The release command builds a complete, tested bundle from the current stored database:

```sh
python3 -m dcdict.release --version 0.5.0 --link-entries
```

By default the command builds Kindle, StarDict, and Kobo editions. It requires a clean Git worktree and `data/characters.sqlite`; Kindle builds additionally require the `kindlegen` binary included with Kindle Previewer, and Kobo builds require Patrick Gaskin's `dictgen` tool from [dictutil](https://github.com/pgaskin/dictutil) on your `PATH`. It makes a SQLite snapshot, re-extracts descriptions from stored HTML without crawling, runs the complete test suite and entry audit, and performs binary smoke tests on all finished dictionaries.

For a faster local format-specific build, use `--format kindle`, `--format stardict`, or `--format kobo`. StarDict-only builds do not require Kindle Previewer or `dictgen`; Kobo-only builds do not require Kindle Previewer. Published releases must use the default `--format all` so every tagged release remains complete.

Successful output is written atomically to `dist/v1.0.0/`:

- `Dungeon-Crawler-Carl-Dictionary.mobi` (Kindle)
- `Dungeon-Crawler-Carl-Dictionary.zip` (Kindle bundle)
- `Dungeon-Crawler-Carl-Dictionary-StarDict.zip` (KOReader bundle)
- `dicthtml-dc.zip` (Kobo)
- `Dungeon-Crawler-Carl-Dictionary-Kobo.zip` (Kobo bundle)
- `SHA256SUMS.txt`
- `release-manifest.json`

Each ZIP includes format-specific installation instructions and the project's license and attribution files. The schema 2 manifest records shared provenance plus separate Kindle, StarDict, and Kobo build and smoke-test results. Existing version directories are protected; pass `--overwrite` only when intentionally rebuilding the same local version.

To publish the same assets as a tagged GitHub Release, install and authenticate the [GitHub CLI](https://cli.github.com/):

```sh
brew install gh
gh auth login
python3 -m dcdict.release --version 0.5.0 --link-entries --publish
```

Publishing additionally requires `HEAD` to match `origin/main`, and refuses to replace an existing tag or release. After upload, the command downloads every asset again and verifies its SHA-256 hash before fetching the new tag locally.

These permanent URLs always point to the assets from the newest GitHub Release:

- <https://github.com/jmcguire/dungeon-crawler-carl-dict/releases/latest/download/Dungeon-Crawler-Carl-Dictionary.mobi>
- <https://github.com/jmcguire/dungeon-crawler-carl-dict/releases/latest/download/Dungeon-Crawler-Carl-Dictionary.zip>
- <https://github.com/jmcguire/dungeon-crawler-carl-dict/releases/latest/download/Dungeon-Crawler-Carl-Dictionary-StarDict.zip>
- <https://github.com/jmcguire/dungeon-crawler-carl-dict/releases/latest/download/dicthtml-dc.zip>
- <https://github.com/jmcguire/dungeon-crawler-carl-dict/releases/latest/download/Dungeon-Crawler-Carl-Dictionary-Kobo.zip>
- <https://github.com/jmcguire/dungeon-crawler-carl-dict/releases/latest/download/SHA256SUMS.txt>
- <https://github.com/jmcguire/dungeon-crawler-carl-dict/releases/latest/download/release-manifest.json>

## Sideload To Kindle

After building `build/dictionary.mobi`, connect the Kindle to the Mac with USB. It should mount under `/Volumes`, usually as:

```text
/Volumes/Kindle
```

Copy the dictionary into the Kindle dictionaries folder:

```sh
cp build/dictionary.mobi "/Volumes/Kindle/documents/dictionaries/Dungeon_Crawler_Carl_Dictionary.mobi"
```

Verify the copy:

```sh
cmp -s build/dictionary.mobi "/Volumes/Kindle/documents/dictionaries/Dungeon_Crawler_Carl_Dictionary.mobi"
```

Safely eject the Kindle:

```sh
diskutil eject /Volumes/Kindle
```

Unplug the Kindle and give it a moment to index the new file. Then check:

```text
Settings -> Language & Dictionaries -> Dictionaries
```

Look for `Dungeon Crawler Carl Dictionary` under English. Select it as the English dictionary, then try looking up names such as `Carl`, `Donut`, or `Mordecai` inside a book.

## Install In KOReader

Download and extract `Dungeon-Crawler-Carl-Dictionary-StarDict.zip`. Keep the extracted `Dungeon-Crawler-Carl-Dictionary` folder and all of its files together, then copy that folder into:

```text
koreader/data/dict/
```

Restart KOReader. If the dictionary is not enabled automatically, open **Dictionary settings -> Manage dictionaries** and enable `Dungeon Crawler Carl Dictionary`.

To make it the default lookup result globally, use **Dictionary settings -> Manage dictionaries** to move `Dungeon Crawler Carl Dictionary` above your other dictionaries, then accept/save the order. KOReader uses that order as dictionary priority.

To make it the priority dictionary for one book only, open that book and use **Dictionary settings -> Set dictionary priority for this book**. Select `Dungeon Crawler Carl Dictionary` so it appears first in the preferred list.

With the linked release build, tapping a referenced dictionary entry should open that entry inside KOReader.

## Install On Kobo

Download `dicthtml-dc.zip`. Connect the Kobo to the Mac with USB and copy the file into:

```text
KOBOeReader/.kobo/custom-dict/
```

If `custom-dict` does not exist, create it. Safely eject and restart the Kobo.

Open a book, select a word, and open the dictionary panel. Use the dictionary selector in the lookup panel to choose the custom dictionary named for the `dc` locale.

On older Kobo firmware, custom dictionaries may require ExtraLocales or a custom dictionary patch before they can be selected. Current Kobo firmware supports `.kobo/custom-dict` for custom dictionaries.

## Crawler Defaults

The default crawler target is:

- API: `https://dungeon-crawler-carl.fandom.com/api.php`
- Categories: `Category:Characters`, `Category:Groups`, `Category:Spells`, `Category:Achievements`, `Category:Races`, `Category:Items`
- User-Agent: `KindleDictionaryCreationCrawler/0.1`

The crawler:

- Uses the MediaWiki API instead of scraping rendered category pages.
- Accepts a Fandom wiki slug with `--fandom`, such as `dungeon-crawler-carl`.
- Accepts one or more categories with repeated `--category` flags.
- Restricts category members to namespace `0`, which skips subcategory pages.
- Checks `robots.txt` before crawling unless `--ignore-robots` is passed.
- Sleeps between requests with jitter.
- Retries temporary HTTP failures with exponential backoff.
- Records errors and keeps going.
- Resumes previous successful fetches unless `--refresh` is passed.

Re-run paragraph extraction from already stored raw HTML, without touching the network:

```sh
python3 -m dcdict.fetch_entries --output data/characters.sqlite --reextract-only
```

Example for a different Fandom wiki:

```sh
python3 -m dcdict.fetch_entries \
  --fandom example \
  --category Characters \
  --category Groups \
  --category Items \
  --output data/example.sqlite
```

## Kindle Notes

Kindle dictionaries are built from XHTML content with Amazon-specific `idx:*` tags plus an OPF package file. The converter writes those source files. Amazon's older documented compiler command is:

```sh
kindlegen dictionary.opf -c2 -verbose -dont_append_source
```

For this project, the bundled Kindle Previewer compiler successfully produces a classic MOBI v7 dictionary when run without `-c2`. The Python build script handles that detail for you. Kindle tooling has changed over the years, so the source files are the stable artifact this project controls, and `build/dictionary.mobi` is the sideloadable artifact to try on the device.

## Attribution

The generated entries include source links back to the Dungeon Crawler Carl Wiki pages. Fandom community content is generally licensed under CC BY-SA unless otherwise noted on the source wiki.
