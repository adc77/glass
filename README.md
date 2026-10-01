# glass

A lab-sample pipeline, and the first product to adopt [seam](https://github.com/adc77/seam). It is a separate repository. The checkout proof inside seam stays the library test. This is a different pipeline: a sample is offered to an instrument, retried once if the bench is busy, judged when a reading arrives, and reported exactly once. A due timer files the sample overdue if the reading never comes.

Handlers are synchronous. They take time and ids from the seam context, and the only way out is `emit` on three ports: `bench`, `qc`, and `report`.

## Setup

Seam is not vendored here. Clone it as a sibling directory, which is where the test suite looks first:

```shell
git clone https://github.com/adc77/seam.git
git clone https://github.com/adc77/glass.git
cd glass   # with ../seam present
```

If you keep the checkouts somewhere else, point the suite at it:

```shell
SEAM_SDK_PATH=/path/to/seam python3 -m unittest discover -s tests -t .
```

## Test

```shell
python3 -m unittest discover -s tests -t .
```

Nothing here needs a network, a GPU, or another machine.

## Simulate one case

```shell
SEAM_SIM=1 \
SEAM_NAMESPACE=sim-glass-release \
SEAM_CASE=glass/cases/release.json \
PYTHONPATH="../seam:." \
python3 -m glass
```

Stdout is the artifact path.

Apache-2.0.