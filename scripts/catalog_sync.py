"""Manual CLI delegates to Personal Radar's reviewed catalog engine."""

import os
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parents[2]
project = Path(__file__).resolve().parents[1]
configured = os.environ.get("CONTENT_CHECKER_DATA_DIR", "").strip()
data = Path(configured).expanduser() if configured else root / "data/content-checker"
if not data.is_absolute():
    data = project / data
data = data.resolve()
python = os.environ.get(
    "CATALOG_SYNC_PYTHON", str(root / "personal-radar/.venv/bin/python")
)
args = sys.argv[1:]
raise SystemExit(
    subprocess.call(
        [
            python,
            str(root / "personal-radar/scripts/catalog_sync.py"),
            args.pop(0) if args else "status",
            "--app",
            "content-checker",
            "--database",
            str(data / "library.sqlite3"),
            *args,
        ]
    )
)
