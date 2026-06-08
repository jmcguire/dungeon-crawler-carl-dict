# Dungeon Crawler Carl Kindle Dictionary

This project builds a Kindle lookup dictionary from character pages on a MediaWiki/Fandom wiki.

The default target is the Dungeon Crawler Carl Fandom character category. The crawler and converter are intentionally generic enough to point at another Fandom wiki and category later.

## Requirements

- macOS
- Python 3.11 or newer recommended
- No Python package dependencies

Optional:

- Kindle Previewer 3 if you want the converter to compile a `.mobi` file automatically.

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
python3 -m dcdict.fetch_characters --ignore-robots
```

For this DCC Fandom wiki, `robots.txt` disallows `/api.php` for crawlers. The script respects that by default and will stop unless you pass `--ignore-robots`. Use that override only for a user-triggered, polite fetch like this project: low rate, small scope, no indexing, no training, and no repeated hammering.

Build Kindle dictionary source files:

```sh
python3 -m dcdict.build_kindle_dictionary
```

Definitions preserve safe inline emphasis from the wiki where possible:
`<b>`/`<strong>` become bold text, and `<i>`/`<em>` become italic text. Other
HTML is stripped or escaped during extraction/building.

Definitions are rendered as a short bullet list. If the source wiki page has a
page-level spoiler warning banner, the generated entry places a spoiler note
above the bullet so a reader has a chance to stop before reading the summary.
The warning is page-level; the wiki generally does not mark smaller spoiler
phrases inside otherwise normal sentences.

When present in the page sidebar's BIOGRAPHICAL INFO section, the dictionary
adds a few conservative detail bullets below the summary: aliases, origin, race,
and first scene. Noisy or more spoilery sidebar fields such as class, crawler
number, crawler ID, and occupation are intentionally omitted.

Optionally add internal cross-links between known dictionary entries:

```sh
python3 -m dcdict.build_kindle_dictionary --link-entries
```

For example, if the `Carl` definition mentions `Donut`, the generated XHTML
links `Donut` to Donut's dictionary entry with an internal anchor. The linker
only touches text nodes, preserves bold/italic markup, skips self-links, and
avoids very short single-word titles to reduce noisy false positives.

Kindle caveat: these links work when opening the dictionary directly as a book,
but may not work inside the Kindle lookup popup/card UI. That appears to be a
Kindle interface limitation rather than a dictionary build error.

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

## Tests

Run the focused standard-library test suite:

```sh
python3 -m unittest discover -s tests
```

The tests cover the HTML summary extraction rules, infobox fallback summaries,
SQLite entry loading, Kindle XHTML/OPF generation, alias generation, and the
Kindle Previewer/`kindlegen` compile wrapper.

## Sideload To Kindle

After building `build/dictionary.mobi`, connect the Kindle to the Mac with USB. It should mount under `/Volumes`, usually as:

```text
/Volumes/Kindle
```

Copy the dictionary into the Kindle dictionaries folder:

```sh
cp build/dictionary.mobi "/Volumes/Kindle/documents/dictionaries/Dungeon_Crawler_Carl_Character_Dictionary.mobi"
```

Verify the copy:

```sh
cmp -s build/dictionary.mobi "/Volumes/Kindle/documents/dictionaries/Dungeon_Crawler_Carl_Character_Dictionary.mobi"
```

Safely eject the Kindle:

```sh
diskutil eject /Volumes/Kindle
```

Unplug the Kindle and give it a moment to index the new file. Then check:

```text
Settings -> Language & Dictionaries -> Dictionaries
```

Look for `Dungeon Crawler Carl Character Dictionary` under English. Select it as the English dictionary, then try looking up names such as `Carl`, `Donut`, or `Mordecai` inside a book.

## Crawler Defaults

The default crawler target is:

- API: `https://dungeon-crawler-carl.fandom.com/api.php`
- Category: `Category:Characters`
- User-Agent: `KindleDictionaryCreationCrawler/0.1`

The crawler:

- Uses the MediaWiki API instead of scraping rendered category pages.
- Restricts category members to namespace `0`, which skips subcategory pages.
- Checks `robots.txt` before crawling unless `--ignore-robots` is passed.
- Sleeps between requests with jitter.
- Retries temporary HTTP failures with exponential backoff.
- Records errors and keeps going.
- Resumes previous successful fetches unless `--refresh` is passed.

Re-run paragraph extraction from already stored raw HTML, without touching the network:

```sh
python3 -m dcdict.fetch_characters --output data/characters.sqlite --reextract-only
```

Example for a different Fandom wiki:

```sh
python3 -m dcdict.fetch_characters \
  --api-url "https://example.fandom.com/api.php" \
  --category "Category:Characters" \
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
