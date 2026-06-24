# Kindling Investigation

These notes capture the June 2026 evaluation of
[`kindling`](https://github.com/ciscoriordan/kindling) as possible optional
Kindle tooling for this project.

## Summary

`kindling` is promising, but it is not release-ready for this project yet. The
current recommendation is to keep KindleGen as the official Kindle release
compiler until a `kindling`-built MOBI passes physical Kindle lookup testing.

The most useful near-term role for `kindling` is as a research validator and
inspection tool. It has better diagnostics than KindleGen, a structural MOBI
dump command, and a StarDict exporter, but the current generated Kindle XHTML
does not appear to match `kindling`'s entry-position expectations.

## Commands Tested

From the generated Kindle build directory:

```sh
kindling-cli validate build/dungeon-crawler-carl/kindle/Dungeon-Crawler-Carl-Dictionary.opf
```

Result: validation failed with one error and two warnings:

- Error: no internal content cover image declared.
- Warning: no NCX file found in the manifest.
- Warning: logical TOC recommended because the generated dictionary is roughly
  309 pages.

```sh
kindling-cli build \
  build/dungeon-crawler-carl/kindle/Dungeon-Crawler-Carl-Dictionary.opf \
  -o /tmp/dcc-kindling-no-validate.mobi \
  --no-validate
```

Result: a MOBI was written, but the build printed the blocker warning:

```text
Warning: 1236 / 1236 entries not found in text blob
```

The same build also printed record-level HTML self-check warnings for unbalanced
`<p>`, `<b>`, and `<i>` tags across MOBI text records. Those may be a secondary
effect of record splitting or a compatibility issue worth checking later, but
the missing-entry-position warning is the release blocker.

```sh
kindling-cli dump /tmp/dcc-kindling-no-validate.mobi
```

Result: the generated MOBI had an orthographic index with lookup terms, but no
separate inflection or naming index. That is consistent with `kindling`'s stated
approach of flattening headwords and inflections into the orthographic index,
but it needs physical Kindle testing before release use.

```sh
kindling-cli stardict \
  build/dungeon-crawler-carl/kindle/Dungeon-Crawler-Carl-Dictionary.opf \
  -o /tmp/dcc-kindling-stardict
```

Result: StarDict export worked structurally and produced `.ifo`, `.idx`,
`.dict`, and `.syn` files. The observed output reported 1236 headwords and 902
inflection redirects.

## Findings

### Validation Strictness

`kindling` validates against Kindle Publishing Guidelines v2026.1 by default.
That is stricter than KindleGen. Our OPF currently has a simple text cover page
in the spine, but no image cover declared with `properties="coverimage"` or
`<meta name="cover" ...>`. KindleGen accepts this, but `kindling` treats it as a
validation error.

Our OPF also has no NCX logical TOC. KindleGen accepts this too, while
`kindling` warns because the publishing guidelines expect NCX or nav metadata.

These OPF issues are fixable, but they are not the main blocker.

### Entry Position Blocker

The important blocker is:

```text
Warning: 1236 / 1236 entries not found in text blob
```

Upstream source inspection showed that `kindling` strips Kindle-only `idx:*`
markup before finding entry text positions. It then expects the stripped entry
text to begin at or near a visible headword, commonly `<b>Headword</b>`.

Our current generated XHTML starts each entry like this:

```html
<idx:entry name="default" scriptable="yes" spell="yes" id="entry-1">
  <a id="entry-1"></a>
  <idx:orth value="1914 Box"><b>1914 Box</b>
    <idx:infl>
      <idx:iform value="1914" />
    </idx:infl>
  </idx:orth>
  ...
</idx:entry>
```

The likely compatibility problem is the anchor before `<idx:orth>`. After
`kindling` strips `idx:*` tags, the entry boundary may no longer match its
heuristic for locating the visible headword. If it cannot locate entry starts,
the lookup index may point to bad or zero-like positions even though a MOBI file
is produced.

This should be tested with a tiny fixture before touching production output.

### Alias Model

The current alias model should not be changed just for `kindling`:

- Canonical headword stays in `idx:orth value`.
- Single-target aliases stay in `idx:iform`.
- Multi-target lookup words stay as duplicate visible `idx:entry` blocks.

The risky part is the surrounding entry-boundary XHTML shape, not the
`idx:orth` / `idx:infl` / `idx:iform` decision.

### StarDict Export

`kindling-cli stardict` works structurally, but it should not replace the native
StarDict builder right now. The project-native StarDict output is built from
normalized shared `Entry` data and intentionally handles multi-target lookup
policy. `kindling` exports from Kindle XHTML, so it inherits Kindle-specific
entry shape and count differences.

## Options

1. Do nothing now.
   - Keep KindleGen as the release compiler.
   - Revisit only if KindleGen becomes painful or unavailable.

2. Use `kindling` as a manual research validator.
   - Run `kindling-cli validate` when improving OPF hygiene.
   - Treat failures as useful feedback, not release blockers yet.

3. Make the OPF more Kindle Publishing Guidelines clean.
   - Add a real image cover declaration.
   - Add a minimal NCX.
   - This may clear validation, but it does not address the entry-position
     warning by itself.

4. Create a small `kindling`-compatible XHTML experiment.
   - Build a tiny dictionary fixture with two or three entries.
   - Keep aliases as `idx:iform`.
   - Experiment only with anchor placement and entry-boundary shape.
   - Try moving the `<a id="entry-N"></a>` anchor after `<idx:orth>`, replacing
     it with an `id` on an element that does not precede the headword, or relying
     on `idx:entry id` for internal links if KindleGen and Kindle hardware allow
     it.
   - Compare KindleGen and `kindling` output.
   - Test on a physical Kindle before changing production output.

5. Add `--kindle-compiler kindlegen|kindling|auto`.
   - Only after the entry-position warning is eliminated.
   - Only after a `kindling`-built MOBI passes physical Kindle lookup tests.
   - Keep KindleGen as the default official compiler until then.

## Safe Experiment Rules

- Do not rewrite the alias model.
- Do not switch official release builds to `kindling` before physical Kindle
  testing.
- Start with a tiny fixture rather than the full dictionary.
- Verify exact headword lookups, `idx:iform` alias lookups, duplicate lookup
  entries, internal links, and dictionary UI behavior.
- Treat `Warning: entries not found in text blob` as release-blocking for any
  `kindling`-built MOBI.

## Current Recommendation

Keep KindleGen as the official Kindle release compiler. Track `kindling` as a
promising optional future path, especially for validation and inspection, but do
not add it to release packaging until the XHTML entry-position issue is
understood and tested on real Kindle hardware.
