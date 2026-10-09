"""Keep historical python3 CLIs using the installed project environment."""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path


def ensure_connectors() -> None:
    if importlib.util.find_spec("personal_radar_connectors") is not None:
        return
    python = Path(__file__).resolve().parents[1] / ".venv/bin/python"
    if python.is_file() and Path(sys.prefix).resolve() != python.parent.parent.resolve():
        os.execv(str(python), [str(python), *sys.argv])
    raise SystemExit("Установите зависимости: .venv/bin/python -m pip install -r requirements.txt")
