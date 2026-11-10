#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DC Twin Studio — desktop cockpit entry point (pythonw desktop_app.pyw)."""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from dc_studio.app import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
