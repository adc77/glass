"""Put seam on the path for the parent process.

`unittest discover` imports the parent without the PYTHONPATH that the child
helper in support.py sets, so the path has to be fixed here too. This keeps the
same lookup order. It is duplicated rather than imported from support, because
support imports seam-dependent modules and this file must stay cheap and
import-order-safe.
"""

import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _find_seam():
    """SEAM_SDK_PATH, then a sibling clone, then an installed seam."""
    override = os.environ.get("SEAM_SDK_PATH")
    if override:
        if not os.path.isdir(os.path.join(override, "seam")):
            raise AssertionError(f"SEAM_SDK_PATH={override!r} has no seam/ package")
        return override
    parent = os.path.dirname(_REPO)
    for name in ("seam", "sim-sdk"):
        candidate = os.path.join(parent, name)
        if os.path.isdir(os.path.join(candidate, "seam")):
            return candidate
    return None


_SEAM = _find_seam()
for _path in (p for p in (_SEAM, _REPO) if p):
    if _path not in sys.path:
        sys.path.insert(0, _path)
