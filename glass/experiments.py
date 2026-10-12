"""Product-owned synthetic sample populations for repeated offline scenario comparisons."""

import argparse
from pathlib import Path
import sys

from seam.actors import ActorRandom
from seam.canon import dumps
from seam.experiments import plan, run_experiment

from glass.actors import SECOND, metrics, person, write_scenario

MODULE = "glass.actor_runtime"
ACTOR_WORLDS = ("actors",)
METRICS = {
    "released": "samples",
    "scrapped": "samples",
    "overdue": "samples",
    "report_failed": "samples",
    "report_pending": "samples",
    "filed_reports": "reports",
    "unfinished_actors": "actors",
}


def prepare(directory, parameters, seed):
    if type(parameters) is not dict or set(parameters) != {
        "population_size",
        "reading_delay_ns",
        "until_ns",
    }:
        raise ValueError("invalid sample experiment parameters")
    for key, low, high in (
        ("population_size", 1, 8),
        ("reading_delay_ns", 0, 100 * SECOND),
        ("until_ns", 1, 200 * SECOND),
    ):
        if type(parameters[key]) is not int or not low <= parameters[key] <= high:
            raise ValueError("invalid sample experiment parameter")
    rng = ActorRandom(seed, "population", 0)
    people = [
        person(
            f"user_{index}",
            start_ns=rng.rand_below(3) * SECOND,
            value=80 + rng.rand_below(41),
            reading_delay_ns=parameters["reading_delay_ns"]
            + rng.rand_below(3) * SECOND,
        )
        for index in range(parameters["population_size"])
    ]
    return write_scenario(
        directory, actors=people, until_ns=parameters["until_ns"], seed=seed
    )


def measure(artifact):
    return metrics(artifact)


def demo_plan():
    return plan(
        "glass.experiments",
        [
            {
                "name": "baseline",
                "parameters": {
                    "population_size": 4,
                    "reading_delay_ns": SECOND,
                    "until_ns": 30 * SECOND,
                },
            },
            {
                "name": "slow_readings",
                "parameters": {
                    "population_size": 4,
                    "reading_delay_ns": 15 * SECOND,
                    "until_ns": 30 * SECOND,
                },
            },
        ],
        [7, 19, 31],
        baseline="baseline",
    )


def main():
    parser = argparse.ArgumentParser(
        description="Compare synthetic Glass populations locally."
    )
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    summary = run_experiment(args.out, demo_plan())
    print(
        dumps(
            {
                "status": summary["status"],
                "runs": len(summary["runs"]),
                "summary": str(args.out / "summary.json"),
            }
        )
    )
    return 0 if summary["status"] == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
