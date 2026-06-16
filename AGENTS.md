# Project Instructions

- Treat GitHub Release tags as the only public project version.
- Do not add or maintain a separate Python package version such as `__version__`.
- When changing tests, release packaging, dictionary output, supported formats, licenses, or release version examples, run `python3 -m dcdict.badges --version <version> --input data/characters.sqlite` and include the badge JSON changes in the same commit.
- Do not create badge-only commits unless the user explicitly asks.
- Use `python3 -m dcdict.release` only after badge files are current and committed.
