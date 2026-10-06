"""The live product frame shares the lab connection and its transaction boundary."""

from seam.canon import INT64_MAX, dumps, loads

from glass.capture import (
    validate_bundle,
    validate_event,
    validate_metadata,
    validate_state,
)
from glass.lab import validate_lab

MAX_FRAME_BYTES = 16 * 1024 * 1024
MAX_EVENTS = 1000


def metadata(state, timers):
    return {
        "timers": timers,
        "ids": {public: row["id"] for public, row in state["samples"].items()},
    }


def validate_frame(frame, source, *, completed=True):
    if type(frame) is not dict or set(frame) != {
        "format",
        "version",
        "source",
        "last_ns",
        "state",
        "timers",
        "capture",
        "failed_capture",
        "completed",
    }:
        raise ValueError("invalid live journal schema")
    if (
        frame["format"] != "glass-live"
        or type(frame["version"]) is not int
        or frame["version"] != 1
        or frame["source"] != source
        or type(frame["last_ns"]) is not int
        or not 0 <= frame["last_ns"] <= INT64_MAX
        or type(frame["failed_capture"]) is not bool
    ):
        raise ValueError("incompatible live journal; use its original product and SDK")
    validate_state(frame["state"])
    validate_metadata(
        frame["state"], metadata(frame["state"], frame["timers"]), frame["last_ns"]
    )
    capture = frame["capture"]
    if capture is None:
        if frame["failed_capture"]:
            raise ValueError("invalid failed capture marker")
    else:
        if type(capture) is not dict or set(capture) != {
            "as_of_ns",
            "initial_state",
            "config",
            "datasets",
            "arrivals",
            "product_data",
        }:
            raise ValueError("invalid active capture journal")
        start = capture["as_of_ns"]
        if type(start) is not int or not 0 <= start <= frame["last_ns"]:
            raise ValueError("invalid active capture cutoff")
        validate_state(capture["initial_state"])
        if capture["config"] != {} or type(capture["datasets"]) is not dict:
            raise ValueError("invalid active capture configuration")
        if set(capture["datasets"]) != {"lab"}:
            raise ValueError("invalid active capture datasets")
        dataset = capture["datasets"]["lab"]
        if type(dataset) is not dict or set(dataset) != {"as_of_ns", "data"}:
            raise ValueError("invalid active capture lab schema")
        if type(dataset["as_of_ns"]) is not int or dataset["as_of_ns"] != start:
            raise ValueError("invalid active capture lab cutoff")
        validate_lab(dataset["data"])
        product_data = capture["product_data"]
        if type(product_data) is not dict or set(product_data) != {"timers"}:
            raise ValueError("invalid active capture timer metadata")
        validate_metadata(
            capture["initial_state"],
            metadata(capture["initial_state"], product_data["timers"]),
            start,
        )
        arrivals = capture["arrivals"]
        if type(arrivals) is not list or len(arrivals) > MAX_EVENTS:
            raise ValueError("invalid active capture arrivals")
        previous = start
        for event in arrivals:
            if type(event) is not dict or set(event) != {"at_ns", "handler", "body"}:
                raise ValueError("invalid active capture event schema")
            if (
                type(event["at_ns"]) is not int
                or not previous <= event["at_ns"] <= frame["last_ns"]
            ):
                raise ValueError("invalid active capture event time")
            validate_event(event["handler"], event["body"])
            if (
                event["handler"] == "receive"
                and event["body"]["sample"] not in frame["state"]["samples"]
            ):
                raise ValueError("active capture event references an unknown sample")
            previous = event["at_ns"]
        for public, row in capture["initial_state"]["samples"].items():
            current = frame["state"]["samples"].get(public)
            if current is None or current["id"] != row["id"]:
                raise ValueError("active capture sample identity changed")
    if completed and frame["completed"] is not None:
        validate_bundle(frame["completed"])
        if frame["completed"]["payload"]["until_ns"] > frame["last_ns"]:
            raise ValueError("completed capture is ahead of the live journal")


class Journal:
    def __init__(self, db, source, *, initial=None):
        self.db, self.source = db, source
        db.execute("PRAGMA synchronous=FULL")
        if initial is not None:
            db.execute("BEGIN IMMEDIATE")
            try:
                db.execute(
                    "CREATE TABLE live (id INTEGER PRIMARY KEY CHECK(id=1), body TEXT NOT NULL)"
                )
                db.execute("INSERT INTO live VALUES (1, ?)", (self.encode(initial),))
                db.execute("COMMIT")
            except BaseException:
                if db.in_transaction:
                    db.execute("ROLLBACK")
                raise
        elif not db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='live'"
        ).fetchone():
            raise ValueError(
                "database has no durable product frame; old volatile state cannot be recovered"
            )

    def encode(self, frame):
        validate_frame(frame, self.source, completed=False)
        body = dumps(frame)
        if len(body.encode("ascii")) > MAX_FRAME_BYTES:
            raise ValueError("live journal size limit exceeded")
        return body

    def load(self):
        row = self.db.execute("SELECT body FROM live WHERE id=1").fetchone()
        if (
            row is None
            or type(row[0]) is not str
            or len(row[0].encode("utf-8")) > MAX_FRAME_BYTES
        ):
            raise ValueError("missing or oversized live journal")
        frame = loads(row[0])
        validate_frame(frame, self.source)
        return frame

    def save(self, frame):
        if not self.db.in_transaction:
            raise RuntimeError("live journal requires a transaction")
        if (
            self.db.execute(
                "UPDATE live SET body=? WHERE id=1", (self.encode(frame),)
            ).rowcount
            != 1
        ):
            raise ValueError("missing live journal")
