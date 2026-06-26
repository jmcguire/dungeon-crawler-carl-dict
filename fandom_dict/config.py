"""Project configuration for building dictionaries from Fandom wikis."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = Path("configs/dungeon-crawler-carl.json")


@dataclass(frozen=True)
class SidebarField:
    """One sidebar field selected from a Fandom portable infobox."""

    source: str
    label: str
    alias: bool = False


@dataclass(frozen=True)
class TitleAliasRules:
    """Configurable title-derived lookup rules."""

    suffixes: tuple[str, ...] = ()
    prefixes: tuple[str, ...] = ()
    strip_parenthetical: bool = True
    component_ignore_words: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProjectConfig:
    """Configuration shared by crawl and build commands."""

    fandom: str
    title: str
    author: str
    source_name: str
    categories: tuple[str, ...]
    database_path: Path
    build_dir: Path
    sidebar_fields: tuple[SidebarField, ...]
    title_aliases: TitleAliasRules
    smoke_headwords: tuple[str, ...]
    kobo_output_name: str
    max_summary_length: int | None = None

    @property
    def kindle_dir(self) -> Path:
        """Return the default Kindle build directory."""

        return self.build_dir / "kindle"

    @property
    def stardict_dir(self) -> Path:
        """Return the default StarDict build directory."""

        return self.build_dir / "stardict"

    @property
    def kobo_dir(self) -> Path:
        """Return the default Kobo build directory."""

        return self.build_dir / "kobo"

    @property
    def file_base_name(self) -> str:
        """Return a filesystem-friendly base name derived from the dictionary title."""

        return slugify_title(self.title)

    @property
    def sidebar_alias_labels(self) -> tuple[str, ...]:
        """Return display labels that should be parsed as sidebar aliases."""

        return tuple(field.label for field in self.sidebar_fields if field.alias)


def slugify_title(value: str) -> str:
    """Return a conservative filename stem for generated dictionary files."""

    stem = re.sub(r"[^A-Za-z0-9]+", "-", value.strip()).strip("-")
    return stem or "Dictionary"


def load_project_config(path: Path | str = DEFAULT_CONFIG_PATH) -> ProjectConfig:
    """Load and validate a project configuration JSON file."""

    config_path = Path(path)
    data = json.loads(config_path.read_text(encoding="utf-8"))
    return project_config_from_mapping(data)


def load_default_project_config() -> ProjectConfig:
    """Load the checked-in Dungeon Crawler Carl configuration."""

    return load_project_config(DEFAULT_CONFIG_PATH)


def project_config_from_mapping(data: dict[str, Any]) -> ProjectConfig:
    """Build a typed project config from decoded JSON data."""

    title_aliases = data.get("title_aliases", {})
    sidebar_fields = tuple(
        SidebarField(
            source=require_string(field, "source"),
            label=require_string(field, "label"),
            alias=bool(field.get("alias", False)),
        )
        for field in require_list(data, "sidebar_fields")
    )
    max_summary_length = data.get("max_summary_length")
    if max_summary_length is not None:
        max_summary_length = int(max_summary_length)
        if max_summary_length <= 0:
            raise ValueError("max_summary_length must be positive")
    config = ProjectConfig(
        fandom=require_string(data, "fandom"),
        title=require_string(data, "title"),
        author=require_string(data, "author"),
        source_name=require_string(data, "source_name"),
        categories=tuple(str(value) for value in require_list(data, "categories")),
        database_path=Path(require_string(data, "database_path")),
        build_dir=Path(require_string(data, "build_dir")),
        sidebar_fields=sidebar_fields,
        title_aliases=TitleAliasRules(
            suffixes=tuple(str(value) for value in title_aliases.get("suffixes", ())),
            prefixes=tuple(str(value) for value in title_aliases.get("prefixes", ())),
            strip_parenthetical=bool(title_aliases.get("strip_parenthetical", True)),
            component_ignore_words=tuple(str(value) for value in title_aliases.get("component_ignore_words", ())),
        ),
        smoke_headwords=tuple(str(value) for value in require_list(data, "smoke_headwords")),
        kobo_output_name=require_string(data, "kobo_output_name"),
        max_summary_length=max_summary_length,
    )
    validate_project_config(config)
    return config


def require_string(data: dict[str, Any], key: str) -> str:
    """Return a required non-empty string field."""

    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"missing or invalid config string: {key}")
    return value


def require_list(data: dict[str, Any], key: str) -> list[Any]:
    """Return a required list field."""

    value = data.get(key)
    if not isinstance(value, list):
        raise ValueError(f"missing or invalid config list: {key}")
    return value


def validate_project_config(config: ProjectConfig) -> None:
    """Validate cross-field constraints for a project config."""

    if not config.categories:
        raise ValueError("config must include at least one category")
    if not config.sidebar_fields:
        raise ValueError("config must include at least one sidebar field")
    if not config.smoke_headwords:
        raise ValueError("config must include at least one smoke headword")
    sources = [field.source for field in config.sidebar_fields]
    if len(set(sources)) != len(sources):
        raise ValueError("sidebar field sources must be unique")
