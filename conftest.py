"""Make the src-layout package importable during tests.

Without this, `pytest` from the repo root fails with `ModuleNotFoundError: No
module named 'ff_tools'` unless the package happens to be installed into the
interpreter running the tests. Prepending `src/` to sys.path means a plain
`pytest` works from a fresh clone, with or without `pip install -e .`.
"""

from __future__ import annotations

import sys
from pathlib import Path

SRC = Path(__file__).parent / "src"
if SRC.is_dir():
    sys.path.insert(0, str(SRC))
