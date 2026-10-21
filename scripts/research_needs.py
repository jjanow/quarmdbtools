#!/usr/bin/env python3
"""Old entry point for the research needs tool; same as ./quarm research (quarmtools/tools/research.py)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from quarmtools.tools.research import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
