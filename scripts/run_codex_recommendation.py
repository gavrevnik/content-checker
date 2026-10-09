"""Compatibility CLI for the shared isolated Codex runner."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.runtime import ensure_connectors
if __name__ == "__main__":
    ensure_connectors()
from personal_radar_connectors.codex.runner import main as run

def main():
    run("Выполни заданный в системных инструкциях контракт рекомендаций.")

if __name__ == "__main__":
    main()
