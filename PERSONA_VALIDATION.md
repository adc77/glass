# Persona validation: 2026-10-13

## Product-owned choices and timing gates

Verdict: runtime fix verified; real-model goal behavior remains partial (3/4 scenarios).

Seam 0.10.0 at `89388917628431e571f02afbd1fd9d121be8b1f1` adds opt-in valid-choice
policies. Glass 0.6.0 uses `sample_v2` for goal personas; the independent unpublished
Reservations fixture uses `booking_v2`. Models return only an offered choice ID. Product
code owns action parameters, private memory, timing anchors, follow-up wakes, attempt
limits, factual measurements, and one-time fault progress. Invalid choices fail before
product delivery. Existing low-level rule policies and normal product handlers are unchanged.

Verification: 260 Seam tests on each of Python 3.11/3.13/3.14; 80 Glass tests and
53 Reservations tests on each interpreter; 99 local bridge/controller tests on Python
3.14. Fresh normal installs resolve the published SDK pin and pass both consumer suites.
The SDK's eight-job GitHub CI is green. Negative tests cover early and forged actions,
memory/delay overrides, premature finish, invalid menus, and checkpoint tampering.
Pending-wake recovery, horizon truncation, meaningful deferral choices, and legacy
regressions are covered by real executor tests.

Fresh live trial: pinned Qwen2.5-32B-Instruct on a personal remote A100-80GB, greedy
BF16 with decoder `schema-greedy-bf16-task-v7-valid-choices`, seed 227. All 54 decisions
were real model calls, with no rule fallback. All four scenarios passed runtime,
timing/capability audits, and two byte-identical provider-free replays. Of those decisions,
21 had multiple valid options and eight selected deferral instead of the rule control's
eligible action. This is not a decoder that secretly chooses the expected transition.

| Scenario | Decisions | Observed result | Goal gate |
| --- | ---: | --- | --- |
| Bookings: ample capacity | 11 | Both goals achieved; one payment failure and one refund | Passed |
| Bookings: scarce capacity | 18 | Buyer confirmed; canceller deferred payment until its hold expired | Failed |
| Glass: healthy | 8 | Both samples released, both reports filed | Passed |
| Glass: faults | 17 | Both samples correctly scrapped after retest, both reports filed | Passed |

The scarce-capacity failure is preserved: the model selected valid waits, not an early
payment, repeated fault, forged measurement, or unauthorized finish. Constraint enforcement
is fixed; valid choices alone do not guarantee effective pursuit of a persona's goals.
Do not remove legitimate choices or substitute a rule result to manufacture success.

The owned app stopped, its temporary proxy token was deleted, and no task-owned containers
remain. Unrelated personal apps were untouched. Latest cumulative task-filtered Modal
usage: $3.90939522 before credits, subject to reporting lag, under the unchanged $5 actual
cloud cap. Conservative request-estimate accounting remains separate from the invoice;
the earlier uncertain calls remain held without retry. Raw evidence is private in
`trusted-choices-20261013.lPmIvB`; no credentials or raw model data were published.
No model ran on the laptop, no organizational resources were used, and nothing was
uploaded to Hugging Face.

Next behavioral work: improve goal reasoning under contention and test broader paired
populations. This small synthetic trial does not certify calibrated humans, population
forecasts, other models, or universal provider reliability.

## Historical low-level goal-policy trial

Verdict: software verified; live goal-persona behavior is not certified.

Seam `823d58cf432b7b5768530d7860f21bff085bbd50` adds provider-neutral synthetic goal
contracts. Glass `52c6dbf871927dd54b53438a13f3188442ecbdca` adds mixed sample personas,
paired healthy/fault scenarios, and the matching published SDK pin. Neither changes normal
product handlers. The independent Reservations product remains local and unpublished.

Verification: 248 Seam tests on each of Python 3.11/3.13/3.14; 76 Glass tests on each
interpreter, plus a fresh normal dependency-resolving release install; 49 Reservations tests
on Python 3.14; 97 local bridge/controller tests on Python 3.14. Both code commits have
green GitHub CI. Explicit fake-provider mixed studies verify fresh controller continuation,
viewer evidence, goal metrics, timing checks, and duplicate exact offline replay. Fake
results are not live-model results.

Actual LLM: pinned `Qwen/Qwen2.5-32B-Instruct@5ede1c97bbab6ce5cda5812749b4c0bdf79b18dd`,
greedy BF16 on a personal remote A100-80GB. No model weights ran on the laptop, no paid
API keys were acquired, and no organizational resources or production data were used.
The goal-policy trials made 124 transport attempts: 123 normalized decisions and one
interrupted request conservatively held without retry. They did not pass the goal gate.

Observed failures included stopping after reservation/submission, acting before declared
delays, repeating a one-time payment-fault fixture, and cancelling a booking whose goal
was confirmation. This is distinct from the earlier procedural 32B smoke's success.
The local study controller initially queried the wrong journal-state label in its prefix
audit; that defect is corrected and regression-tested against the actual SDK journal,
not only mocked rows. Final system-instruction trials stopped after two booking calls
and one Glass call upon behavior violations. No rule-policy fallback or success
certificate was substituted for these model failures.

All task-owned apps stopped; zero owned containers. Latest cumulative task-filtered
Modal usage was $3.68821203 before credits, subject to reporting lag, under the unchanged
$5 actual-cloud cap. Request-estimate allowances are separate from the cloud invoice;
uncertainty and earlier accepted costs were retained. Private raw records and credentials
were not published. No Hugging Face upload occurred.

Next: expose product-owned valid action menus and enforce requested timing in the scheduler.
Let the model select meaningful intentions rather than depending on prose to implement
low-level scheduling transitions. Then rerun fresh mixed-population/fault cases with a real
model. Do not claim calibrated humans, population forecasts, or universal provider reliability.
