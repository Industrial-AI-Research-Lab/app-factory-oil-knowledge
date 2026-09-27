"""Single source of truth for the selected Volve snapshot."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
VERSION_FILE = ROOT / "DATA_VERSION.json"


@lru_cache(maxsize=1)
def data_version() -> dict[str, Any]:
    value = json.loads(VERSION_FILE.read_text(encoding="utf-8"))
    required = {"dataset", "project", "commit", "slice_root", "roots", "expected_files"}
    missing = required - value.keys()
    if missing:
        raise ValueError(f"DATA_VERSION.json is missing: {', '.join(sorted(missing))}")
    if not isinstance(value["roots"], dict) or not isinstance(
        value["expected_files"], dict
    ):
        raise ValueError("DATA_VERSION.json roots and expected_files must be objects")
    return value
