"""Real HTTP operations, closed write boundaries, hidden IDs, durable captures, and offline replay."""

from contextlib import redirect_stderr
import hashlib
import http.client
import io
from pathlib import Path
import tempfile
from threading import Thread
import unittest

from seam.canon import dumps, loads

from glass.capture import write_new
from glass.lab import initial_lab
from glass.live_capture import Service
from glass.pipeline import DUE_NS, RETRY_NS
from glass.workbench import main, make_server, verify


class HttpFixture:
    lab_parameters = {"busy_remaining": 0, "lose_ack": False}

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.at = 0
        self.service = Service(
            self.root / "live.sqlite",
            initial_lab=initial_lab(**self.lab_parameters),
            clock=lambda: self.at,
        )
        self.addCleanup(self.service.close)
        self.server = make_server(self.service)
        self.thread = Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.01}
        )
        self.thread.start()
        self.addCleanup(self.stop)
        self.prefix = self.server.url.removeprefix(self.server.origin)

    def stop(self):
        self.server.shutdown()
        self.thread.join(timeout=3)
        self.assertFalse(self.thread.is_alive())
        self.server.server_close()

    def request(self, method, route, body=None, *, headers=None, raw=False):
        connection = http.client.HTTPConnection(
            "localhost", self.server.server_port, timeout=3
        )
        supplied = {"Origin": self.server.origin, "Content-Type": "application/json"}
        if headers is not None:
            supplied.update(headers)
        try:
            connection.request(method, self.prefix + route, body=body, headers=supplied)
            response = connection.getresponse()
            data = response.read()
            return (
                response.status,
                data if raw else loads(data),
                dict(response.getheaders()),
            )
        finally:
            connection.close()

    def post(self, route, value):
        status, value, _ = self.request("POST", route, dumps(value))
        self.assertEqual(status, 200, value)
        return value

    def receive(self):
        return self.post(
            "api/events",
            {"handler": "receive", "body": {"sample": "s1", "kind": "blood"}},
        )

    def reading(self, value):
        return self.post(
            "api/events",
            {"handler": "reading", "body": {"sample": "s1", "value": value}},
        )


class WorkbenchTests(HttpFixture, unittest.TestCase):
    def test_expired_work_cannot_be_resurrected_by_a_forged_reading(self):
        self.receive()
        self.at = DUE_NS
        body = {"handler": "reading", "body": {"sample": "s1", "value": 10}}
        self.assertEqual(self.request("POST", "api/events", dumps(body))[0], 400)
        state = self.request("GET", "api/state")[1]
        self.assertEqual(state["samples"][0]["status"], "overdue")
        self.assertEqual(state["stored_reports"], 1)

    def test_failed_delivery_invalidates_capture_and_requires_explicit_abort(self):
        self.post("api/capture/start", {})
        self.receive()
        body = {"handler": "receive", "body": {"sample": "s1", "kind": "other"}}
        self.assertEqual(self.request("POST", "api/events", dumps(body))[0], 400)
        self.assertEqual(self.request("GET", "api/state")[1]["capture"], "invalidated")
        self.assertEqual(self.request("POST", "api/capture/finish", "{}")[0], 400)
        self.post("api/capture/abort", {})
        self.assertEqual(self.request("GET", "api/state")[1]["capture"], "idle")

    def test_changed_capture_refused_before_creating_verification_outputs(self):
        self.post("api/capture/start", {})
        self.receive()
        self.post("api/capture/finish", {})
        document = self.request("GET", "api/capture/latest")[1]
        document["payload"]["arrivals"][0]["body"]["kind"] = "changed"
        path = self.root / "changed.json"
        write_new(path, document)
        out = self.root / "never-created"
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            self.assertEqual(
                main(["verify", "--bundle", str(path), "--out", str(out)]), 2
            )
        self.assertFalse(out.exists())

    def test_duplicate_length_and_transfer_encoding_headers_are_refused(self):
        for duplicate in (True, False):
            connection = http.client.HTTPConnection(
                "localhost", self.server.server_port, timeout=3
            )
            try:
                connection.putrequest("POST", self.prefix + "api/capture/start")
                connection.putheader("Origin", self.server.origin)
                connection.putheader("Content-Type", "application/json")
                connection.putheader("Content-Length", "2")
                connection.putheader(
                    "Content-Length" if duplicate else "Transfer-Encoding",
                    "2" if duplicate else "chunked",
                )
                connection.endheaders(b"{}")
                self.assertEqual(connection.getresponse().status, 400)
            finally:
                connection.close()
        self.assertIsNone(self.service.capture)

    def test_assets_and_headers_are_served_from_the_package(self):
        for route in ("", "app.js", "style.css"):
            status, body, headers = self.request("GET", route, raw=True)
            self.assertEqual(status, 200)
            self.assertTrue(body)
            self.assertEqual(headers["Cache-Control"], "no-store")
            self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
            self.assertEqual(headers["Referrer-Policy"], "no-referrer")

    def test_healthy_operations_capture_and_replay_without_live_database_changes(self):
        self.post("api/capture/start", {})
        received = self.receive()
        self.assertEqual(received["samples"][0]["status"], "running")
        released = self.reading(10)
        self.assertEqual(released["samples"][0]["status"], "released")
        self.assertEqual(released["stored_reports"], 1)
        self.post("api/capture/finish", {})
        code, document, headers = self.request("GET", "api/capture/latest")
        self.assertEqual(code, 200)
        self.assertIn("attachment", headers["Content-Disposition"])
        path = self.root / "capture.json"
        write_new(path, document)
        before = hashlib.sha256((self.root / "live.sqlite").read_bytes()).hexdigest()
        report = verify(path, self.root / "verification")
        self.assertEqual(report["byte_identical_replays"], 2)
        self.assertEqual(report["view"]["samples"]["s1"]["status"], "released")
        self.assertEqual(len(report["arrivals"]), 2)
        self.assertEqual(
            hashlib.sha256((self.root / "live.sqlite").read_bytes()).hexdigest(), before
        )

    def test_operator_state_omits_internal_ids_timer_tokens_and_report_keys(self):
        self.receive()
        _, state, _ = self.request("GET", "api/state")
        self.assertEqual(
            set(state["samples"][0]),
            {"public", "kind", "status", "retested", "report_attempts"},
        )
        self.assertNotIn("smp_", dumps(state))
        self.assertNotIn("due", dumps(state))

    def test_origin_host_and_cross_site_requests_cannot_write(self):
        body = dumps({"handler": "receive", "body": {"sample": "s1", "kind": "blood"}})
        for headers in (
            {"Origin": "https://untrusted.invalid"},
            {"Origin": "null"},
            {"Host": "untrusted.invalid"},
            {"Sec-Fetch-Site": "cross-site"},
        ):
            with self.subTest(headers=headers):
                self.assertEqual(
                    self.request("POST", "api/events", body, headers=headers)[0], 403
                )
        connection = http.client.HTTPConnection(
            "localhost", self.server.server_port, timeout=3
        )
        try:
            connection.request(
                "POST",
                self.prefix + "api/events",
                body=body,
                headers={"Content-Type": "application/json"},
            )
            self.assertEqual(connection.getresponse().status, 403)
        finally:
            connection.close()
        self.assertEqual(self.service.snapshot()["product"]["samples"], {})

    def test_private_prefix_unknown_routes_and_traversals_are_closed(self):
        for route in (
            "../style.css",
            "api/state?extra=1",
            "%2e%2e/style.css",
            "unknown",
        ):
            self.assertEqual(self.request("GET", route, raw=True)[0], 404)
        connection = http.client.HTTPConnection(
            "localhost", self.server.server_port, timeout=3
        )
        try:
            connection.request("GET", "/")
            self.assertEqual(connection.getresponse().status, 404)
        finally:
            connection.close()

    def test_invalid_bodies_content_types_and_internal_handlers_are_refused(self):
        for body in (
            "[]",
            '{"handler":1,"handler":2}',
            '{"handler":"retry","body":{"sample":"s1"}}',
            "x" * 8193,
            '{"handler":"receive","body":{"sample":"s1","kind":"blood","extra":1}}',
        ):
            with self.subTest(body=body[:40]):
                self.assertEqual(self.request("POST", "api/events", body)[0], 400)
        self.assertEqual(
            self.request(
                "POST",
                "api/capture/start",
                "{}",
                headers={"Content-Type": "text/plain"},
            )[0],
            400,
        )
        self.assertEqual(self.service.snapshot()["product"]["samples"], {})

    def test_reading_requires_observed_running_work_and_an_integer(self):
        body = {"handler": "reading", "body": {"sample": "s1", "value": 10}}
        self.assertEqual(self.request("POST", "api/events", dumps(body))[0], 400)
        self.receive()
        body["body"]["value"] = 1000001
        self.assertEqual(self.request("POST", "api/events", dumps(body))[0], 400)
        body["body"]["value"] = True
        self.assertEqual(self.request("POST", "api/events", dumps(body))[0], 400)
        self.reading(10)
        body["body"]["value"] = 10
        self.assertEqual(self.request("POST", "api/events", dumps(body))[0], 400)
        self.assertEqual(len(self.service.snapshot()["lab"]["filed"]), 1)

    def test_capture_commands_are_closed_and_repeated_finish_is_idempotent(self):
        self.assertEqual(
            self.request("POST", "api/capture/start", '{"extra":1}')[0], 400
        )
        self.post("api/capture/start", {})
        self.assertEqual(self.request("POST", "api/capture/start", "{}")[0], 400)
        self.receive()
        self.post("api/capture/finish", {})
        first = self.request("GET", "api/capture/latest")[1]
        self.post("api/capture/finish", {})
        self.assertEqual(self.request("GET", "api/capture/latest")[1], first)

    def test_cli_preserves_existing_database_and_verification_evidence(self):
        stderr = io.StringIO()
        before = (self.root / "live.sqlite").read_bytes()
        with redirect_stderr(stderr):
            self.assertEqual(
                main(
                    [
                        "init",
                        "--db",
                        str(self.root / "live.sqlite"),
                        "--scenario",
                        "healthy",
                    ]
                ),
                2,
            )
        self.assertEqual((self.root / "live.sqlite").read_bytes(), before)
        self.post("api/capture/start", {})
        self.receive()
        self.post("api/capture/finish", {})
        path = self.root / "capture.json"
        write_new(path, self.request("GET", "api/capture/latest")[1])
        marker = self.root / "existing"
        marker.mkdir()
        (marker / "keep").write_text("original")
        with redirect_stderr(stderr):
            self.assertEqual(
                main(["verify", "--bundle", str(path), "--out", str(marker)]), 2
            )
        self.assertEqual((marker / "keep").read_text(), "original")
        self.assertNotIn("Traceback", stderr.getvalue())


class AdverseWorkbenchTests(HttpFixture, unittest.TestCase):
    lab_parameters = {"busy_remaining": 1, "lose_ack": True}

    def test_adverse_real_timers_retest_lost_ack_and_single_report_replay(self):
        self.post("api/capture/start", {})
        self.assertEqual(self.receive()["samples"][0]["status"], "queued")
        self.at += RETRY_NS
        self.assertEqual(
            self.request("GET", "api/state")[1]["samples"][0]["status"], "running"
        )
        self.assertEqual(self.reading(150)["samples"][0]["status"], "retest")
        self.at += RETRY_NS
        self.assertTrue(self.request("GET", "api/state")[1]["samples"][0]["retested"])
        self.assertEqual(self.reading(150)["samples"][0]["status"], "report_pending")
        self.at += RETRY_NS
        state = self.request("GET", "api/state")[1]
        self.assertEqual(state["samples"][0]["status"], "scrapped")
        self.assertEqual(state["samples"][0]["report_attempts"], 2)
        self.assertEqual(state["stored_reports"], 1)
        self.post("api/capture/finish", {})
        path = self.root / "capture.json"
        write_new(path, self.request("GET", "api/capture/latest")[1])
        report = verify(path, self.root / "verification")
        self.assertEqual(report["view"]["samples"]["s1"]["status"], "scrapped")
        self.assertEqual(len(report["lab"]["filed"]), 1)
        self.assertEqual(report["byte_identical_replays"], 2)


if __name__ == "__main__":
    unittest.main()
