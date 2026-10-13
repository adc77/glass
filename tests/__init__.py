"""Use the installed SDK unless its source path is explicitly selected."""

import sys

from tests.support import REPO, SEAM

for _path in (p for p in (SEAM, REPO) if p):
    if _path not in sys.path:
        sys.path.insert(0, _path)
