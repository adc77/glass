"""Simulation-only persona entry point using Glass's unchanged handlers and isolated lab adapter."""

import sys

from seam import main

from glass.actors import system
from glass.lab import lab_world
from glass.pipeline import build


def build_actors():
    rt = build(report_backend=None)
    rt.port("inspect", lambda: None)
    rt.sim_world("lab", lab_world, ports=("bench", "qc", "report", "inspect"))
    system().install(rt)
    return rt


if __name__ == "__main__":
    sys.exit(main(build_actors()))
