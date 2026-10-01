import os
import sys

from seam import in_sim, main
from seam.canon import loads

from glass.pipeline import build


class _Held:
    """Wall-clock timers are the product's job. This test service only records them."""

    def __init__(self):
        self.rows = []

    def arm(self, token, at_ns, handler, body, name):
        self.rows.append(
            {"token": token, "at_ns": at_ns, "handler": handler, "body": body, "name": name}
        )


def entry():
    rt = build()
    if in_sim():
        return main(rt)
    held = _Held()
    rt.set_timer_backend(held.arm)
    rt.start_live()
    raw = os.environ.get("GLASS_BODY")
    if raw:
        rt.deliver("receive", loads(raw))
    return 0


if __name__ == "__main__":
    sys.exit(entry())
