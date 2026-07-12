import unittest
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit


REPO_ROOT = Path(__file__).resolve().parents[1]
DOCS_ROOT = REPO_ROOT / "docs"
PAGES_BASE_PATH = "/dungeon-crawler-carl-dict/"


class AssetParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.references: list[str] = []

    def handle_starttag(self, tag, attrs) -> None:
        attributes = dict(attrs)
        for name in ("href", "src"):
            if value := attributes.get(name):
                self.references.append(value)


class DocsTests(unittest.TestCase):
    def test_all_local_html_links_and_assets_exist(self) -> None:
        missing: list[str] = []
        for page in sorted(DOCS_ROOT.rglob("*.html")):
            parser = AssetParser()
            parser.feed(page.read_text(encoding="utf-8"))
            parser.close()
            for reference in parser.references:
                parsed = urlsplit(reference)
                if parsed.scheme or parsed.netloc or reference.startswith("#"):
                    continue
                path = unquote(parsed.path)
                if path.startswith(PAGES_BASE_PATH):
                    target = (DOCS_ROOT / path.removeprefix(PAGES_BASE_PATH)).resolve()
                else:
                    target = (page.parent / path).resolve()
                if target.is_dir():
                    target = target / "index.html"
                if not target.exists():
                    missing.append(f"{page.relative_to(REPO_ROOT)} -> {reference}")
        self.assertEqual(missing, [])

    def test_every_public_page_declares_the_shared_favicon(self) -> None:
        for page in sorted(DOCS_ROOT.rglob("*.html")):
            with self.subTest(page=page.relative_to(DOCS_ROOT)):
                self.assertIn('rel="icon"', page.read_text(encoding="utf-8"))

    def test_homepage_has_reader_downloads_and_dynamic_release_status(self) -> None:
        text = (DOCS_ROOT / "index.html").read_text(encoding="utf-8")

        self.assertIn("Dungeon-Crawler-Carl-Dictionary.mobi", text)
        self.assertIn("Dungeon-Crawler-Carl-Dictionary-StarDict.zip", text)
        self.assertIn("dicthtml-dc.zip", text)
        self.assertIn("api.github.com/repos/jmcguire/dungeon-crawler-carl-dict/releases/latest", text)


if __name__ == "__main__":
    unittest.main()
