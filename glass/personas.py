"""Goal-driven synthetic sample actors over the unchanged Glass pipeline."""

from seam.personas import goal_prompt, persona

from glass.actors import SECOND, person, validate_profile

CONTRACT = (
    "You submit your own laboratory sample and supply readings when it is running. "
    "Capabilities: receive({sample,kind}) submits actor_id+'_sample' exactly once with "
    "profile.kind; reading({sample,value}) supplies profile.value for that same sample. "
    "You observe only your sample status and whether it was retested. Running means "
    "readings may be supplied; queued means the bench is not ready. A first reading "
    "may trigger one retest, which requires one more reading. Terminal statuses are "
    "released, scrapped, dropped, overdue, and report_failed. You cannot control the "
    "bench, quality verdict, or report delivery. Memory has phase (submit, wait, reading, "
    "done) and readings (integer 0..2); these track your plan, not required transitions. "
    "Wait profile.reading_delay_ns before each reading, polling queued state at "
    "profile.poll_ns. There is no authorization to invent measurements or resubmit."
)
PROMPT = goal_prompt(CONTRACT)


def sample_persona(actor_id, *, start_ns, value, reading_delay_ns, name):
    """Attach immutable goals without changing the handler or granting extra actions."""
    actor = person(
        actor_id, start_ns=start_ns, value=value, reading_delay_ns=reading_delay_ns
    )
    actor["profile"]["poll_ns"] = 5 * SECOND
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
