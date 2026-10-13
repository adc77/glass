"""Product-owned sample affordances and virtual-time gates for model-selected intentions."""

from seam import Fault
from seam.actors import choice
from seam.canon import deep_copy
from seam.ctx import assert_int

from glass.actors import validate_profile
from glass.pipeline import DISPOSITIONS


def sample_choices(request):
    profile, memory, now = (
        request["profile"],
        deep_copy(request["memory"]),
        request["at_ns"],
    )
    validate_profile(profile)
    if (
        type(memory) is not dict
        or set(memory) != {"phase", "readings", "ready_at_ns"}
        or memory["phase"] not in {"submit", "wait", "done"}
    ):
        raise Fault("bad_actor")
    assert_int(memory["readings"], minimum=0, maximum=2)
    if memory["ready_at_ns"] is not None:
        assert_int(memory["ready_at_ns"], minimum=0)
    status = request["observation"]["status"]
    sample = f"{request['actor_id']}_sample"

    def offer(key, description, action=None, delay=None, **updates):
        return choice(
            key,
            description,
            {
                "action": action,
                "memory": {**memory, **updates},
                "wake_after_ns": delay,
            },
        )

    if memory["phase"] == "submit":
        if status is not None or memory["readings"] != 0:
            raise Fault("actor_state")
        return [
            offer(
                "submit",
                "Submit my sample for processing.",
                {
                    "type": "receive",
                    "body": {"sample": sample, "kind": profile["kind"]},
                },
                0,
                phase="wait",
            )
        ]
    if status in DISPOSITIONS or status == "report_failed":
        return [
            offer(
                "finish",
                "Accept the observed terminal disposition.",
                phase="done",
                ready_at_ns=None,
            )
        ]
    if memory["phase"] == "done" or status is None:
        raise Fault("actor_state")
    if status != "running":
        return [
            offer(
                "wait",
                "Wait for processing or reporting, then observe again.",
                delay=profile["poll_ns"],
                ready_at_ns=None,
            )
        ]
    if memory["readings"] >= 2 or (
        memory["readings"] == 1 and not request["observation"]["retested"]
    ):
        raise Fault("actor_state")
    if memory["ready_at_ns"] is None:
        memory["ready_at_ns"] = now + profile["reading_delay_ns"]
    if now < memory["ready_at_ns"]:
        return [
            offer(
                "wait",
                "Wait until my declared reading delay has elapsed.",
                delay=memory["ready_at_ns"] - now,
            )
        ]
    return [
        offer(
            "read",
            "Supply my actual measurement now to pursue a final disposition.",
            {
                "type": "reading",
                "body": {"sample": sample, "value": profile["value"]},
            },
            0,
            readings=memory["readings"] + 1,
            ready_at_ns=None,
        ),
        offer(
            "wait",
            "Defer this eligible reading and observe again later.",
            delay=profile["poll_ns"],
        ),
    ]


def sample_choice_policy(request, rng):
    return {"choice": request["choices"][0]["id"]}
