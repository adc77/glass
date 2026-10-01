"""Put the sibling seam repo on the path. Discover does not."""

import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SEAM = os.path.join(os.path.dirname(_REPO), "sim-sdk")
for _path in (_SEAM, _REPO):
    if _path not in sys.path:
        sys.path.insert(0, _path)
