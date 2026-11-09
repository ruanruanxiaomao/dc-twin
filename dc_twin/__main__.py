"""Allow `python -m dc_twin ...`."""
import sys

from .cli import main

sys.exit(main())
