"""Serialized Glass operation exports product state, dependency data, IDs, and active timers."""

import fcntl
import heapq
import os
from pathlib import Path
from threading import RLock
import time

from seam import Runtime
from seam.canon import deep_copy

from glass.capture import bundle, provenance, validate_event, view
from glass.lab import Lab
from glass.pipeline import build

MAX_EVENTS = 1000


class DeliveryRuntime(Runtime):
    def __init__(self, cancel):
        super().__init__(namespace="glass", initial_state={"samples": {}})
        self.delivery_ns = 0
        self.cancel_callback = cancel

    def now(self):
        if self.mode != "live":
            raise RuntimeError("live clock is unavailable")
        return self.delivery_ns

    def cancel(self, token):
        super().cancel(token)
        self.cancel_callback(token)


class Service:
    def __init__(self, db_path, *, initial_lab=None, clock=time.time_ns):
        self.clock, self.lock = clock, RLock()
        self.capture = None
        self.failed_capture = False
        self.timers, self.active, self.seq = [], {}, 0
        self.last_ns = 0
        self.last_ns = self._now()
        db_path = Path(db_path).resolve()
        self.lease = os.open(str(db_path) + ".lock", os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(self.lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.lab = Lab(db_path, snapshot=initial_lab)
            try:
                self.runtime = build(
                    runtime=DeliveryRuntime(self._cancel),
                    report_backend=None,
                    bench_factory=lambda: lambda request: self.lab.call(
                        "bench", request
                    ),
                    qc_factory=lambda: lambda request: self.lab.call("qc", request),
                    report_factory=lambda: lambda request: self.lab.call(
                        "report", request
                    ),
                )
                self.runtime.set_timer_backend(self._arm)
                self.runtime.start_live()
            except BaseException:
                self.lab.close()
                raise
        except BaseException:
            os.close(self.lease)
            raise

    def _now(self):
        now = self.clock()
        if type(now) is not int or not self.last_ns <= now <= 2**63 - 1:
            raise ValueError("invalid or backwards live clock")
        return now

    def _arm(self, token, at_ns, handler, body, name):
        self.seq += 1
        self.active[token] = {
            "token": token,
            "at_ns": at_ns,
            "handler": handler,
            "body": deep_copy(body),
            "name": name,
        }
        heapq.heappush(self.timers, (at_ns, self.seq, token))

    def _cancel(self, token):
        del self.active[token]

    def _pump(self, at):
        while self.timers and self.timers[0][0] <= at:
            due, _, token = heapq.heappop(self.timers)
            if token not in self.active:
                continue
            del self.active[token]
            self.runtime.delivery_ns = due
            try:
                self.runtime.fire_timer(token)
            except BaseException:
                self.failed_capture = True
                raise
            self.last_ns = due
        self.last_ns = at

    def pump(self):
        with self.lock:
            self._pump(self._now())

    def submit(self, handler, body):
        validate_event(handler, body)
        body = deep_copy(body)
        with self.lock:
            if self.capture is not None and len(self.capture["arrivals"]) >= MAX_EVENTS:
                raise ValueError(
                    "capture is full; finish it before accepting more events"
                )
            now = self._now()
            self._pump(now)
            self.runtime.delivery_ns = now
            try:
                self.runtime.deliver(handler, body)
            except BaseException:
                self.failed_capture = True
                raise
            self.last_ns = now
            if self.capture is not None:
                self.capture["arrivals"].append(
                    {"at_ns": now, "handler": handler, "body": body}
                )
            return view(self.runtime.state_copy())

    def snapshot(self):
        with self.lock:
            self._pump(self._now())
            return {
                "product": view(self.runtime.state_copy()),
                "lab": self.lab.snapshot(),
            }

    def begin_capture(self):
        with self.lock:
            if self.capture is not None:
                raise ValueError("capture already active")
            now = self._now()
            self._pump(now)
            self.failed_capture = False
            self.capture = {
                "as_of_ns": now,
                "initial_state": self.runtime.state_copy(),
                "config": {},
                "datasets": {"lab": {"as_of_ns": now, "data": self.lab.snapshot()}},
                "arrivals": [],
                "product_data": {
                    "timers": [deep_copy(row) for row in self.active.values()]
                },
            }
            self.source = provenance()
            return {"as_of_ns": now}

    def finish_capture(self):
        with self.lock:
            if self.capture is None:
                raise ValueError("no active capture")
            if self.failed_capture:
                raise ValueError("failed delivery invalidated this capture")
            now = self._now()
            self._pump(now)
            if not self.source == provenance():
                raise ValueError("product or SDK changed during capture")
            state = self.runtime.state_copy()
            payload = deep_copy(self.capture)
            payload["product_data"]["ids"] = {
                public: row["id"] for public, row in state["samples"].items()
            }
            document = bundle(
                {
                    **payload,
                    "until_ns": now,
                    "assertions": [
                        {"op": "state_is", "path": "view", "value": view(state)},
                        {"op": "state_is", "path": "lab", "value": self.lab.snapshot()},
                    ],
                }
            )
            self.capture = None
            return document

    def close(self):
        with self.lock:
            try:
                self.runtime.close()
            finally:
                self.lab.close()
                os.close(self.lease)
