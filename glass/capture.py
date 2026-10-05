"""Glass-owned ID preservation, timer reconstruction, and outcome validation."""

import re

from seam import (
    capture_bundle,
    read_capture,
    validate_capture,
    write_capture_case,
    write_capture_json,
    capture_source,
    run_capture,
)
from seam.canon import INT64_MAX, deep_copy
from seam.errors import Fault

from glass.lab import validate_lab
from glass.pipeline import validate_samples

MODULE = "glass.replay"
IDENT = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")
PRIVATE_ID = re.compile(r"smp_[0-9a-f]{16}\Z")
TOKEN = re.compile(r"t[1-9][0-9]*\Z")
TIMER_FIELDS = frozenset({"due", "retry", "retest", "report_retry"})
PENDING = {
    "running": ("due", "due"),
    "queued": ("retry", "retry"),
    "retest": ("retest", "retest"),
    "report_pending": ("report_retry", "report_retry"),
}


def validate_event(handler, body):
    if handler not in ("receive", "reading") or type(body) is not dict:
        raise ValueError("capture accepts receive or reading events")
    keys = {"sample", "kind"} if handler == "receive" else {"sample", "value"}
    if (
        set(body) != keys
        or type(body["sample"]) is not str
        or not IDENT.fullmatch(body["sample"])
    ):
        raise ValueError("invalid captured event")
    if handler == "receive":
        if type(body["kind"]) is not str or not IDENT.fullmatch(body["kind"]):
            raise ValueError("invalid captured sample kind")
    elif (
        type(body["value"]) is not int
        or not -INT64_MAX - 1 <= body["value"] <= INT64_MAX
    ):
        raise ValueError("invalid captured reading")


def validate_state(state, *, timer_tokens=True):
    if type(state) is not dict or set(state) != {"samples"}:
        raise ValueError("invalid captured product state")
    try:
        samples = validate_samples(state, timer_tokens=timer_tokens)
    except Fault:
        raise ValueError("invalid captured sample state") from None
    ids = set()
    for public, row in samples.items():
        if (
            type(public) is not str
            or not IDENT.fullmatch(public)
            or not PRIVATE_ID.fullmatch(row["id"])
            or row["id"] in ids
            or not IDENT.fullmatch(row["kind"])
        ):
            raise ValueError("invalid captured sample identity")
        ids.add(row["id"])
        if (
            set(row)
            - {
                "public",
                "id",
                "kind",
                "status",
                "retested",
                "report_attempts",
                "machine",
                "disposition",
            }
            - TIMER_FIELDS
        ):
            raise ValueError("unknown captured sample field")
        for field in TIMER_FIELDS & set(row):
            if not timer_tokens:
                raise ValueError(
                    "captured outcome must not contain private timer tokens"
                )
            if type(row[field]) is not str or not TOKEN.fullmatch(row[field]):
                raise ValueError("invalid captured timer token")


def view(state):
    return {
        "samples": {
            public: {
                key: value for key, value in row.items() if key not in TIMER_FIELDS
            }
            for public, row in state["samples"].items()
        }
    }


def validate_metadata(state, metadata, start):
    if type(metadata) is not dict or set(metadata) != {"timers", "ids"}:
        raise ValueError("invalid captured timer or ID metadata")
    ids = metadata["ids"]
    if type(ids) is not dict:
        raise ValueError("invalid captured ID map")
    for public, ident in ids.items():
        if (
            not IDENT.fullmatch(public)
            or type(ident) is not str
            or not PRIVATE_ID.fullmatch(ident)
        ):
            raise ValueError("invalid captured private ID")
    if len(set(ids.values())) != len(ids):
        raise ValueError("duplicate captured private ID")
    for public, row in state["samples"].items():
        if ids.get(public) != row["id"]:
            raise ValueError("captured private ID differs from snapshot")
    timers = metadata["timers"]
    if type(timers) is not list:
        raise ValueError("invalid captured timers")
    tokens, names = {}, set()
    for timer in timers:
        if type(timer) is not dict or set(timer) != {
            "token",
            "at_ns",
            "handler",
            "body",
            "name",
        }:
            raise ValueError("invalid captured timer schema")
        token, name, handler = timer["token"], timer["name"], timer["handler"]
        if (
            type(token) is not str
            or not TOKEN.fullmatch(token)
            or token in tokens
            or type(name) is not str
            or name in names
            or type(handler) is not str
            or handler not in {item[1] for item in PENDING.values()}
            or type(timer["at_ns"]) is not int
            or not start <= timer["at_ns"] <= INT64_MAX
            or type(timer["body"]) is not dict
            or set(timer["body"]) != {"sample"}
        ):
            raise ValueError("invalid captured timer values")
        public = timer["body"]["sample"]
        if type(public) is not str or public not in state["samples"]:
            raise ValueError("timer references an unknown sample")
        row = state["samples"][public]
        pending = PENDING.get(row["status"])
        if pending is None or pending[1] != handler or row.get(pending[0]) != token:
            raise ValueError("timer contradicts captured sample state")
        purpose = {
            "retry": "retry-once",
            "due": "due-retest" if row["retested"] else "due-once",
            "retest": "retest-once",
            "report_retry": "report-once",
        }[handler]
        if name != f"{purpose}-{row['id']}":
            raise ValueError("timer name contradicts sample identity")
        tokens[token] = timer
        names.add(name)
    for row in state["samples"].values():
        pending = PENDING.get(row["status"])
        if pending is not None and row.get(pending[0]) not in tokens:
            raise ValueError("pending sample has no captured timer")


def bundle(payload):
    document = capture_bundle(MODULE, **payload)
    validate_bundle(document)
    return document


def validate_bundle(document):
    validate_capture(document, module=MODULE)
    payload = document["payload"]
    if payload["config"] != {} or set(payload["datasets"]) != {"lab"}:
        raise ValueError("invalid Glass capture dependencies")
    validate_state(payload["initial_state"])
    validate_lab(payload["datasets"]["lab"]["data"])
    validate_metadata(
        payload["initial_state"], payload["product_data"], payload["as_of_ns"]
    )
    for event in payload["arrivals"]:
        validate_event(event["handler"], event["body"])
        if (
            event["handler"] == "receive"
            and event["body"]["sample"] not in payload["product_data"]["ids"]
        ):
            raise ValueError("captured receipt has no preserved private ID")
    assertions = payload["assertions"]
    if len(assertions) != 2 or [row.get("path") for row in assertions] != [
        "view",
        "lab",
    ]:
        raise ValueError("Glass capture requires product and dependency outcomes")
    for row in assertions:
        if row["op"] != "state_is":
            raise ValueError("invalid Glass capture outcome assertion")
    validate_state(assertions[0]["value"], timer_tokens=False)
    validate_lab(assertions[1]["value"])
    for public, row in assertions[0]["value"]["samples"].items():
        if payload["product_data"]["ids"].get(public) != row["id"]:
            raise ValueError("captured outcome has an unknown private ID")


def read_bundle(path):
    document = read_capture(path, module=MODULE)
    validate_bundle(document)
    return document


def replay_plan(document, *, reading_delay_ns=0):
    validate_bundle(document)
    if type(reading_delay_ns) is not int or not 0 <= reading_delay_ns <= INT64_MAX:
        raise ValueError("invalid reading delay")
    payload = document["payload"]
    arrivals = deep_copy(payload["arrivals"])
    for row in arrivals:
        if row["handler"] == "reading":
            row["at_ns"] += reading_delay_ns
    arrivals.sort(key=lambda row: row["at_ns"])
    config = {"capture_ids": payload["product_data"]["ids"]}
    return dict(
        name="glass-capture",
        namespace="sim-glass-capture",
        ports={
            port: {"mode": "world", "world": "lab"}
            for port in ("bench", "qc", "report", "inspect")
        },
        worlds={"lab": {"dataset": "lab"}},
        bootstrap=(
            {"handler": "bootstrap", "body": {"metadata": payload["product_data"]}},
        ),
        finalize=({"handler": "observe", "body": {}},),
        arrivals=arrivals,
        until_ns=payload["until_ns"] + reading_delay_ns,
        config=config,
        same_time_order="timers_first",
    )


def write_case(document, directory, *, reading_delay_ns=0):
    return write_capture_case(
        document,
        directory,
        module=MODULE,
        **replay_plan(document, reading_delay_ns=reading_delay_ns),
    )


def run_bundle(document, directory, *, reading_delay_ns=0):
    return run_capture(
        document,
        directory,
        module=MODULE,
        **replay_plan(document, reading_delay_ns=reading_delay_ns),
    )


write_new = write_capture_json


def provenance():
    return capture_source(MODULE)
