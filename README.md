# glass

A lab-sample pipeline, and the first product to adopt [seam](../sim-sdk). It is a separate repository. The checkout proof inside seam stays the library test. This is a different pipeline: a sample is offered to an instrument, retried once if the bench is busy, judged when a reading arrives, and reported exactly once. A due timer files the sample overdue if the reading never comes.

Handlers are synchronous. They take time and ids from the seam context, and the only way out is `emit` on three ports: `bench`, `qc`, and `report`.

```shell
python3 -m unittest discover -s tests -t .
```

Run that from this repository. The suite puts `../sim-sdk` on `PYTHONPATH`. Nothing here needs a network, a GPU, or another machine. The repository is local. It is not published. The license is not chosen.

Simulate one case:

```shell
SEAM_SIM=1 \
SEAM_NAMESPACE=sim-glass-release \
SEAM_CASE=glass/cases/release.json \
PYTHONPATH="../sim-sdk:." \
python3 -m glass
```

Stdout is the artifact path.
