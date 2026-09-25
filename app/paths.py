from __future__ import annotations

import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_DIR = ROOT.parent / "data" / "content-checker"
_configured_data_dir = os.environ.get("CONTENT_CHECKER_DATA_DIR", "").strip()
DATA_DIR = (
    Path(_configured_data_dir).expanduser()
    if _configured_data_dir
    else DEFAULT_DATA_DIR
).resolve()
