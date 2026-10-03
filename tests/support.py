"""Test helpers. The parent process must never call install_guards or seam.main."""

import json
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _find_seam():
    """Locate the seam checkout.

    Order: SEAM_SDK_PATH, then a sibling clone, then an installed seam.
    Returns the path to put on PYTHONPATH, or None if seam is already
    importable from the environment.
    """
    override = os.environ.get("SEAM_SDK_PATH")
    if override:
        if not os.path.isdir(os.path.join(override, "seam")):
            raise AssertionError(f"SEAM_SDK_PATH={override!r} has no seam/ package")
        return override
    sibling = os.path.join(os.path.dirname(REPO), "seam")
    if os.path.isdir(os.path.join(sibling, "seam")):
        return sibling
    legacy = os.path.join(os.path.dirname(REPO), "sim-sdk")
    if os.path.isdir(os.path.join(legacy, "seam")):
        return legacy
    return None


SEAM = _find_seam()
CASE_DIR = os.path.join(REPO, "glass", "cases")
RELEASE = os.path.join(CASE_DIR, "release.json")
RELEASE_DIGEST = "7864660de112c64315df7ac8519f8ce9a97df6428a7653059b8a67c2d2ea7cd7"

CASES = {
    "release": ("sim-glass-release", os.path.join(CASE_DIR, "release.json")),
    "busy-then-release": ("sim-glass-busy", os.path.join(CASE_DIR, "busy_then_release.json")),
    "fail-then-release": ("sim-glass-fail", os.path.join(CASE_DIR, "fail_then_release.json")),
    "overdue": ("sim-glass-overdue", os.path.join(CASE_DIR, "overdue.json")),
    "dropped": ("sim-glass-dropped", os.path.join(CASE_DIR, "dropped.json")),
    # The qc-fail branches. A failed qc does not end the sample: it goes back to
    # the bench once, and only a second failure scraps it.
    "qc-fail-scrapped": ("sim-glass-scrapped", os.path.join(CASE_DIR, "qc_fail_scrapped.json")),
    "qc-fail-retest-busy": (
        "sim-glass-retest-busy",
        os.path.join(CASE_DIR, "qc_fail_retest_busy.json"),
    ),
}


def child_env(**values):
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("SEAM_") and not key.startswith("GLASS_")
    }
    env["PYTHONPATH"] = os.pathsep.join(p for p in (SEAM, REPO) if p)
    env["PYTHONUNBUFFERED"] = "1"
    # Pinned, so set and dict iteration order cannot vary between the two runs
    # the byte-identity assertions compare. seam's `sim_env` does the same, but
    # glass builds its child environments here, and without it those seven
    # comparisons would pass or fail depending on the host's hash seed rather
    # than on anything about glass.
    env.setdefault("PYTHONHASHSEED", "0")
    for key, value in values.items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    return env


def run_proc(args, env, timeout=10):
    try:
        return subprocess.run(
            args,
            cwd=REPO,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", "replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", "replace")
        raise AssertionError(f"timed out after {timeout}s\nstdout={stdout}\nstderr={stderr}") from None


def run_glass(case, namespace, artifact, extra=None, timeout=10):
    env = child_env(
        SEAM_SIM="1",
        SEAM_NAMESPACE=namespace,
        SEAM_CASE=case,
        SEAM_ARTIFACT=artifact,
    )
    if extra:
        env.update(extra)
    return run_proc([sys.executable, "-m", "glass"], env, timeout=timeout)


def write_json(path, obj):
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(obj, handle)
        handle.write("\n")
