# Goal persona validation: 2026-10-13

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
