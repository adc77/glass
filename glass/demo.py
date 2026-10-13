"""Free, explicitly scripted end-to-end persona experiment and strict replay proof."""

import argparse
from pathlib import Path
import subprocess
import sys

from seam.canon import dumps
from seam.experiments import read_experiment
from seam.inference import settings
from seam.inference_experiments import InferenceExperiment, plan, replay_experiment
from seam_viewer.evidence import open_experiments

from glass.goal_experiments import variants
from glass.personas import PROMPT


def scripted_provider(envelope):
    require(
        envelope["provider"] == "scripted"
        and envelope["model"] == "first-valid-choice-v1",
        "not a scripted demo request",
    )
    return {
        "request_id": envelope["request_id"],
        "model": envelope["model"],
        "returned_model": envelope["model"],
        "decision": {"choice": envelope["request"]["choices"][0]["id"]},
        "usage": {"input_tokens": 1, "output_tokens": 1, "cost_microusd": 0},
    }


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def verify(root):
    summary = read_experiment(root / "experiment")
    require(summary["status"] == "passed", "experiment did not pass")
    require(len(summary["runs"]) == 4, "expected four paired runs")
    require(
        summary["comparisons"]["faults"]["paired_seeds"] == [7, 19], "unpaired seeds"
    )
    viewer = open_experiments([("Glass", root / "experiment")])[0]
    require(viewer.overview(0)["available"], "viewer evidence unavailable")
    outcomes = []
    for index, row in enumerate(summary["runs"]):
        require(row["status"] == "passed", "cell did not pass")
        metrics = row["metrics"]
        expected = 2 if row["variant"] == "healthy" else 0
        require(metrics["goal_achieved"] == expected, "unexpected goal outcome")
        require(metrics["scrapped"] == 2 - expected, "unexpected disposition")
        require(metrics["filed_reports"] == 2, "missing or duplicate report")
        require(metrics["unfinished_actors"] == 0, "unfinished personas")
        artifact = viewer.run(index)["artifact"]
        require(artifact["actors"][0]["mode"] == "inference", "wrong decision handoff")
        left = replay_experiment(
            root / "experiment",
            row["variant"],
            row["seed"],
            root / f"replay-{index}-a.json",
        )
        right = replay_experiment(
            root / "experiment",
            row["variant"],
            row["seed"],
            root / f"replay-{index}-b.json",
        )
        require(
            Path(left.artifact_path).read_bytes()
            == Path(right.artifact_path).read_bytes(),
            "replay mismatch",
        )
        outcomes.append(
            {"variant": row["variant"], "seed": row["seed"], "metrics": metrics}
        )
    return {
        "status": "passed",
        "controller": "scripted-not-an-LLM",
        "paid_calls": 0,
        "fresh_process_continuation": True,
        "byte_identical_replays": 8,
        "viewer_verified": True,
        "runs": outcomes,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out", type=Path, required=True, help="new private output directory"
    )
    parser.add_argument(
        "--scripted",
        action="store_true",
        required=True,
        help="use a free deterministic controller, not an LLM",
    )
    parser.add_argument("--resume", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    root = args.out.resolve()
    if args.resume:
        with InferenceExperiment(root / "experiment") as session:
            summary = session.run(scripted_provider)
        require(summary["status"] == "passed", "continued experiment did not pass")
        return 0
    root.mkdir(mode=0o700, exist_ok=False)
    spec = plan(
        "glass.goal_experiments",
        variants(),
        [7, 19],
        baseline="healthy",
        config=settings("scripted", "first-valid-choice-v1", PROMPT, max_calls=64),
        max_calls=128,
    )
    with InferenceExperiment.create(root / "experiment", spec) as session:
        session.step(scripted_provider)
    subprocess.run(
        [
            sys.executable,
            "-m",
            "glass.demo",
            "--out",
            str(root),
            "--scripted",
            "--resume",
        ],
        check=True,
        timeout=180,
    )
    print(dumps(verify(root)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
