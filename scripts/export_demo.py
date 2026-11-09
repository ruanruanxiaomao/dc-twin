"""export_demo.py - pipeline wrapper so `dc run --scope full` also emits demo/."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dc_twin.export import export  # noqa: E402

if __name__ == "__main__":
    sys.exit(export("demo"))
