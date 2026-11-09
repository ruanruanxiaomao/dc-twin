#!/usr/bin/env python3
"""Cross-platform entry point: python run.py --scope data|scenarios|full.

This is the canonical pipeline driver. run_all.ps1 / run.sh are thin wrappers.
See dc_twin/runner.py for the step list.
"""
import sys

from dc_twin.runner import main

if __name__ == "__main__":
    sys.exit(main())
