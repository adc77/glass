"""Mixed goal populations with paired bench/quality faults and honest outcome metrics."""

from glass.actors import SECOND, metrics, write_scenario
from glass.personas import mixed_population

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
    "dropped": "samples",
    "goal_achieved": "actors",
    "decisions": "count",
    "completion_time_ns": "ns",
}


def prepare(directory, parameters, seed):
    if type(parameters) is not dict or set(parameters) != {
        "busy_remaining",
        "qc_max",
        "lose_ack",
        "until_ns",
    }:
        raise ValueError("bad_goal_parameters")
    for key, low, high in (
        ("busy_remaining", 0, 8),
        ("qc_max", 0, 200),
        ("until_ns", 1, 200 * SECOND),
    ):
        if type(parameters[key]) is not int or not low <= parameters[key] <= high:
            raise ValueError("bad_goal_parameters")
    if type(parameters["lose_ack"]) is not bool:
        raise ValueError("bad_goal_parameters")
    return write_scenario(
        directory, actors=mixed_population(seed), seed=seed, **parameters
    )


def measure(artifact):
    product = metrics(artifact)
    decisions = artifact["world_states"]["actors"]["decisions"]
    finished = [
        row["request"]["at_ns"]
        for row in decisions
        if row["resolution"]["wake_after_ns"] is None
    ]
    return {
        **product,
        "dropped": sum(
            row["status"] == "dropped"
            for row in artifact["final_state"]["samples"].values()
        ),
        "goal_achieved": product["released"],
        "decisions": len(decisions),
        "completion_time_ns": max(finished) if finished else 0,
    }


def variants():
    return [
        {
            "name": "healthy",
            "parameters": {
                "busy_remaining": 0,
                "qc_max": 100,
                "lose_ack": False,
                "until_ns": 40 * SECOND,
            },
        },
        {
            "name": "faults",
            "parameters": {
                "busy_remaining": 2,
                "qc_max": 50,
                "lose_ack": True,
                "until_ns": 40 * SECOND,
            },
        },
    ]
