#!/usr/bin/env python3
"""Compatibility wrapper for the renamed crawler module."""

from __future__ import annotations

from dcdict.fetch_entries import *  # noqa: F403 - preserve compatibility import surface.
from dcdict.fetch_entries import main


if __name__ == "__main__":
    raise SystemExit(main())
