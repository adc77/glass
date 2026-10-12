"""Synthetic sample submitters react to the existing Glass pipeline without knowing its private state."""

from collections import Counter
from pathlib import Path

from seam import Fault, capture_bundle, run_product, write_capture_case
from seam.actors import ActorSystem, population
from seam.ctx import assert_int

from glass.lab import initial_lab
from glass.pipeline import DISPOSITIONS

SECOND = 1_000_000_000


def validate_profile(profile):
    if type(profile) is not dict or set(profile) != {
        "kind",
        "value",
        "reading_delay_ns",
        "poll_ns",
    }:
        raise ValueError("invalid sample persona")
    if type(profile["kind"]) is not str or not profile["kind"]:
        raise ValueError("invalid sample kind")
    assert_int(profile["value"], minimum=0)
    assert_int(profile["reading_delay_ns"], minimum=0)
    assert_int(profile["poll_ns"], minimum=1)


def sample_policy(request, rng):
    profile, memory = request["profile"], request["memory"]
    validate_profile(profile)
    if type(memory) is not dict or set(memory) != {"phase", "readings"}:
        raise ValueError("invalid sample memory")
    phase = memory["phase"]
    status = request["observation"]["status"]
    sample = f"{request['actor_id']}_sample"
    action, delay = None, None
    if phase == "submit":
        action = {
            "type": "receive",
            "body": {"sample": sample, "kind": profile["kind"]},
        }
        memory["phase"], delay = "wait", 0
    elif status in DISPOSITIONS or status == "report_failed":
        memory["phase"] = "done"
    elif phase == "reading" and status == "running":
        memory["readings"] += 1
        if memory["readings"] > 2:
            raise Fault("actor_budget")
        action = {
            "type": "reading",
            "body": {"sample": sample, "value": profile["value"]},
        }
        memory["phase"], delay = "wait", 0
    elif phase == "wait" and status == "running":
        memory["phase"], delay = "reading", profile["reading_delay_ns"]
    elif phase in ("wait", "reading"):
        memory["phase"], delay = "wait", profile["poll_ns"]
    else:
        raise Fault("bad_actor")
    return {"action": action, "memory": memory, "wake_after_ns": delay}


def observe(actor, state, at_ns):
    row = state["samples"].get(f"{actor['id']}_sample")
    return {
        "status": None if row is None else row["status"],
        "retested": False if row is None else row["retested"],
    }


def translate(actor, action, decision_id):
    body = action["body"]
    if type(body) is not dict or body.get("sample") != f"{actor['id']}_sample":
        raise Fault("actor_ownership")
    fields = {"receive": {"sample", "kind"}, "reading": {"sample", "value"}}
    kind = action["type"]
    if kind not in fields or set(body) != fields[kind]:
        raise Fault("bad_actor")
    if kind == "receive" and body["kind"] != actor["profile"]["kind"]:
        raise Fault("actor_ownership")
    if kind == "reading":
        assert_int(body["value"], minimum=0)
    return {"handler": kind, "body": body}


def system():
    return ActorSystem({"sample_v1": sample_policy}, observe, translate)


def person(actor_id="alice", *, start_ns=0, value=42, reading_delay_ns=SECOND):
    return {
        "id": actor_id,
        "policy": "sample_v1",
        "profile": {
            "kind": "blood",
            "value": value,
            "reading_delay_ns": reading_delay_ns,
            "poll_ns": SECOND,
        },
        "memory": {"phase": "submit", "readings": 0},
        "actions": ["receive", "reading"],
        "start_ns": start_ns,
        "max_decisions": 50,
    }


def write_scenario(
    directory,
    *,
    actors=None,
    tape=None,
    until_ns=20 * SECOND,
    busy_remaining=1,
    lose_ack=True,
    seed=None,
):
    if type(until_ns) is not int:
        raise ValueError("invalid sample horizon")
    people = [person(), person("bob", start_ns=SECOND)] if actors is None else actors
    for actor in people:
        validate_profile(actor["profile"])
    document = capture_bundle(
        "glass.actor_runtime",
        as_of_ns=0,
        until_ns=until_ns,
        config={},
        initial_state={"samples": {}},
        datasets={
            "lab": {
                "as_of_ns": 0,
                "data": initial_lab(busy_remaining=busy_remaining, lose_ack=lose_ack),
            },
            "actors": {
                "as_of_ns": 0,
                "data": population(people, seed=77 if seed is None else seed, tape=tape),
            },
        },
        arrivals=[{"at_ns": 0, "handler": "actors_start", "body": {}}],
        assertions=[
            {
                "op": "event_count",
                "handler": "receive",
                "times": sum(actor["start_ns"] <= until_ns for actor in people),
            }
        ],
        product_data={"synthetic": True},
    )
    return write_capture_case(
        document,
        directory,
        module="glass.actor_runtime",
        name="sample-actors",
        namespace="sim-sample-actors",
        ports={
            **{
                port: {"mode": "world", "world": "lab"}
                for port in ("bench", "qc", "report", "inspect")
            },
            "actors": {"mode": "world", "world": "actors"},
        },
        worlds={"lab": {"dataset": "lab"}, "actors": {"dataset": "actors"}},
        finalize=({"handler": "actors_finish", "body": {}},),
        same_time_order="timers_first",
        seed=1842 if seed is None else seed,
    )


def run_scenario(directory, **kwargs):
    path = write_scenario(directory, **kwargs)
    return run_product(
        "glass.actor_runtime",
        str(path),
        "sim-sample-actors",
        str(Path(path).parent / "artifact.json"),
    )


def metrics(artifact):
    if (
        artifact["status"] != "passed"
        or not artifact["world_states"]["actors"]["sealed"]
    ):
        raise ValueError("metrics require a sealed completed actor experiment")
    statuses = Counter(
        row["status"] for row in artifact["final_state"]["samples"].values()
    )
    return {
        "released": statuses["released"],
        "scrapped": statuses["scrapped"],
        "overdue": statuses["overdue"],
        "report_failed": statuses["report_failed"],
        "report_pending": statuses["report_pending"],
        "filed_reports": len(artifact["world_states"]["lab"]["filed"]),
        "unfinished_actors": sum(
            row["next_ns"] is not None
            for row in artifact["world_states"]["actors"]["actors"].values()
        ),
    }
