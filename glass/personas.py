"""Goal-driven synthetic sample actors over the unchanged Glass pipeline."""

from seam.personas import choice_prompt, persona

from glass.actors import SECOND, person, validate_profile

CONTRACT = (
    "Choose from the current valid sample intentions. Product code owns measurements, "
    "submission ownership, at most one retest, reading-delay gates, and follow-up wakes. "
    "Only terminal dispositions allow finish. Read supplies the actual measurement now; "
    "wait defers it. Bench, quality verdicts, and report delivery remain product outcomes."
)
PROMPT = choice_prompt(CONTRACT)


def sample_persona(actor_id, *, start_ns, value, reading_delay_ns, name):
    """Attach immutable goals without changing the handler or granting extra actions."""
    actor = person(
        actor_id, start_ns=start_ns, value=value, reading_delay_ns=reading_delay_ns
    )
    actor["profile"]["poll_ns"] = 5 * SECOND
    actor["policy"] = "sample_v2"
    actor["memory"]["ready_at_ns"] = None
    actor["profile"]["persona"] = persona(
        name,
        ["Obtain a final quality disposition for my sample; prefer release."],
        constraints=[
            "Respect my declared reading delay and provide only my actual profile.value.",
            "If retesting is requested, provide at most one additional reading.",
            "Accept terminal failure; never claim release for a dropped or scrapped sample.",
        ],
    )
    validate_profile(actor["profile"])
    return actor


def mixed_population(seed):
    """Fixed two-cohort assumptions; seed affects measurements, not hidden user claims."""
    from seam.actors import ActorRandom

    rng = ActorRandom(seed, "population", 0)
    return [
        sample_persona(
            "responsive",
            start_ns=0,
            value=80 + rng.rand_below(10),
            reading_delay_ns=SECOND,
            name="Responsive submitter",
        ),
        sample_persona(
            "deliberate",
            start_ns=SECOND,
            value=80 + rng.rand_below(10),
            reading_delay_ns=3 * SECOND,
            name="Deliberate submitter",
        ),
    ]
