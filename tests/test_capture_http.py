"""The real loopback adapter refuses malformed input and exports in-flight product work."""

import http.client
from pathlib import Path
import tempfile
from threading import Thread
import unittest
from unittest.mock import patch

from seam.canon import dumps, loads

from glass.capture import run_bundle
from glass.capture_http import make_server
from glass.lab import initial_lab
from glass.live_capture import Service


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.service = Service(self.root / "lab.sqlite", initial_lab=initial_lab())
        self.addCleanup(self.service.close)
        self.server = make_server(self.service, 0)
        self.thread = Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.01}
        )
        self.thread.start()
        self.addCleanup(self.stop_server)

    def stop_server(self):
        self.server.shutdown()
        self.thread.join(timeout=3)
        self.assertFalse(self.thread.is_alive())
        self.server.server_close()

    def request(self, method, path, body=None):
        connection = http.client.HTTPConnection(
            self.server.server_address[0], self.server.server_port, timeout=3
        )
        try:
            connection.request(method, path, body=body)
            response = connection.getresponse()
            return response.status, loads(response.read())
        finally:
            connection.close()

    def receive(self, kind="blood"):
        return self.request(
            "POST",
            "/events",
            dumps({"handler": "receive", "body": {"sample": "s1", "kind": kind}}),
        )

    def test_http_capture_replays_through_shared_sdk_runner(self):
        status, state = self.receive()
        self.assertEqual(status, 200)
        self.assertEqual(state["samples"]["s1"]["status"], "queued")
        self.assertEqual(self.request("POST", "/capture/start", "{}")[0], 200)
        status, captured = self.request("POST", "/capture/finish", "{}")
        self.assertEqual(status, 200)
        self.assertEqual(captured["format"], "seam-capture")
        result = run_bundle(captured, self.root / "replay")
        self.assertEqual(result.returncode, 0, (result.stderr, result.artifact))

    def test_malformed_or_oversized_bodies_do_not_change_state(self):
        for body in ('{"handler":1,"handler":2}', "[]", '{"unknown":true}', "x" * 8193):
            with self.subTest(body=body[:40]):
                self.assertEqual(self.request("POST", "/events", body)[0], 400)
        self.assertEqual(self.request("GET", "/state")[1]["product"]["samples"], {})

    def test_domain_fault_invalidates_capture_without_crashing_http(self):
        self.assertEqual(self.receive()[0], 200)
        self.assertEqual(self.request("POST", "/capture/start", "{}")[0], 200)
        self.assertEqual(self.receive("changed"), (400, {"error": "bad_value"}))
        status, body = self.request("POST", "/capture/finish", "{}")
        self.assertEqual(status, 400)
        self.assertIn("invalidated", body["error"])
        self.assertEqual(self.request("GET", "/state")[0], 200)

    def test_capture_routes_are_closed_and_unknown_routes_are_not_events(self):
        self.assertEqual(self.request("POST", "/capture/start", '{"extra":1}')[0], 400)
        self.assertEqual(self.request("POST", "/capture/finish", "{}")[0], 400)
        self.assertEqual(self.request("POST", "/unknown", "{}")[0], 404)
        self.assertEqual(self.request("GET", "/unknown")[0], 404)

    def test_latest_capture_can_be_retrieved_and_finish_retried(self):
        self.assertEqual(self.request("GET", "/capture/latest")[0], 400)
        self.assertEqual(self.request("POST", "/capture/start", "{}")[0], 200)
        self.assertEqual(self.receive()[0], 200)
        status, captured = self.request("POST", "/capture/finish", "{}")
        self.assertEqual(status, 200)
        self.assertEqual(self.request("GET", "/capture/latest"), (200, captured))
        self.assertEqual(self.request("POST", "/capture/finish", "{}"), (200, captured))

    def test_failed_capture_can_be_explicitly_aborted(self):
        self.assertEqual(self.receive()[0], 200)
        self.assertEqual(self.request("POST", "/capture/start", "{}")[0], 200)
        self.assertEqual(self.receive("changed")[0], 400)
        self.assertEqual(
            self.request("POST", "/capture/abort", '{"extra":true}')[0], 400
        )
        self.assertEqual(
            self.request("POST", "/capture/abort", "{}"), (200, {"aborted": True})
        )
        self.assertEqual(self.request("POST", "/capture/start", "{}")[0], 200)
        self.assertEqual(self.request("POST", "/capture/finish", "{}")[0], 200)

    def test_loopback_server_does_not_need_reverse_dns_to_start(self):
        with patch(
            "socket.getfqdn", side_effect=AssertionError("reverse DNS is unavailable")
        ):
            server = make_server(self.service, 0)
            try:
                self.assertEqual(server.server_address[0], "127.0.0.1")
                self.assertEqual(server.server_name, "localhost")
                self.assertEqual(server.server_port, server.server_address[1])
            finally:
                server.server_close()
