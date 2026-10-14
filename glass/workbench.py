"""A private loopback workbench over the existing durable Glass service, not a production server."""

import argparse
import hashlib
from http.server import BaseHTTPRequestHandler
from importlib.resources import files
from pathlib import Path
import secrets
import sys

from seam.canon import dumps, loads
from seam.errors import Fault, Refuse

from glass.capture import read_bundle, run_bundle, validate_event
from glass.capture_http import (
    LoopbackServer,
    LOOPBACK,
    MAX_REQUEST,
)
from glass.lab import initial_lab
from glass.live_capture import Service

ASSETS = {
    "": ("index.html", "text/html"),
    "app.js": ("app.js", "text/javascript"),
    "style.css": ("style.css", "text/css"),
}
CSP = "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"


def snapshot(service):
    with service.lock:
        value = service.snapshot()
        rows = [
            {
                key: row[key]
                for key in ("public", "kind", "status", "retested", "report_attempts")
            }
            for _, row in sorted(value["product"]["samples"].items())
        ]
        return {
            "samples": rows,
            "stored_reports": len(value["lab"]["filed"]),
            "capture": "invalidated"
            if service.failed_capture
            else "active"
            if service.capture is not None
            else "idle",
            "capture_available": service.completed is not None,
            "mode": "real-time-local-synthetic-product",
        }


def make_server(service, port=0):
    prefix = "/" + secrets.token_urlsafe(32) + "/"

    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(2)

        def log_message(self, fmt, *args):
            return

        def respond(
            self, status, body, content_type="application/json", *, attachment=False
        ):
            data = body if type(body) is bytes else (dumps(body) + "\n").encode("ascii")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", CSP)
            if attachment:
                self.send_header(
                    "Content-Disposition", 'attachment; filename="glass-capture.json"'
                )
            self.end_headers()
            self.wfile.write(data)

        def route(self, mutation=False):
            if self.headers.get_all("Host") != [self.server.authority]:
                self.respond(403, {"error": "host_refused"})
                return None
            if not self.path.startswith(prefix) or "?" in self.path:
                self.respond(404, {"error": "not_found"})
                return None
            if self.headers.get("Sec-Fetch-Site") not in (None, "same-origin", "none"):
                self.respond(403, {"error": "cross_site_refused"})
                return None
            if mutation and self.headers.get_all("Origin") != [self.server.origin]:
                self.respond(403, {"error": "origin_refused"})
                return None
            return self.path[len(prefix) :]

        def do_GET(self):
            route = self.route()
            if route is None:
                return
            try:
                if route in ASSETS:
                    name, mime = ASSETS[route]
                    self.respond(
                        200,
                        files("glass").joinpath("workbench_static", name).read_bytes(),
                        mime,
                    )
                elif route == "api/state":
                    self.respond(200, snapshot(service))
                elif route == "api/capture/latest":
                    self.respond(200, service.latest_capture(), attachment=True)
                else:
                    self.respond(404, {"error": "not_found"})
            except (ValueError, Refuse, Fault) as error:
                self.respond(
                    400,
                    {"error": error.code if isinstance(error, Fault) else str(error)},
                )

        def do_POST(self):
            route = self.route(mutation=True)
            if route is None:
                return
            if route not in {
                "api/events",
                "api/capture/start",
                "api/capture/finish",
                "api/capture/abort",
            }:
                self.respond(404, {"error": "not_found"})
                return
            try:
                lengths = self.headers.get_all("Content-Length")
                if (
                    lengths is None
                    or len(lengths) != 1
                    or "Transfer-Encoding" in self.headers
                ):
                    raise ValueError("one bounded Content-Length is required")
                length = int(lengths[0])
                if (
                    not 0 < length <= MAX_REQUEST
                    or self.headers.get("Content-Type") != "application/json"
                ):
                    raise ValueError("a bounded application/json body is required")
                body = loads(self.rfile.read(length))
                if route == "api/events":
                    if type(body) is not dict or set(body) != {"handler", "body"}:
                        raise ValueError("events require handler and body")
                    validate_event(body["handler"], body["body"])
                    if (
                        body["handler"] == "reading"
                        and not -1000000 <= body["body"]["value"] <= 1000000
                    ):
                        raise ValueError(
                            "workbench readings must be between -1000000 and 1000000"
                        )
                    with service.lock:
                        current = service.snapshot()["product"]["samples"]
                        if body["handler"] == "reading" and (
                            body["body"]["sample"] not in current
                            or current[body["body"]["sample"]]["status"] != "running"
                        ):
                            raise ValueError(
                                "reading requires an observed running sample"
                            )
                        service.submit(body["handler"], body["body"])
                    self.respond(200, snapshot(service))
                else:
                    if type(body) is not dict or body:
                        raise ValueError("capture commands require an empty object")
                    operation = {
                        "api/capture/start": service.begin_capture,
                        "api/capture/finish": service.finish_capture,
                        "api/capture/abort": service.abort_capture,
                    }[route]
                    operation()
                    self.respond(200, snapshot(service))
            except (ValueError, TypeError, Refuse, Fault) as error:
                self.respond(
                    400,
                    {"error": error.code if isinstance(error, Fault) else str(error)},
                )

    server = LoopbackServer((LOOPBACK, port), Handler)
    server.timeout = 0.05
    server.authority = "localhost:" + str(server.server_port)
    server.origin = "http://" + server.authority
    server.url = server.origin + prefix
    return server


def serve(service, port):
    server = make_server(service, port)
    try:
        print("Glass workbench: " + server.url, flush=True)
        while True:
            server.handle_request()
            service.pump()
    finally:
        server.server_close()


def verify(bundle_path, directory):
    document = read_bundle(bundle_path)
    root = Path(directory).expanduser().resolve()
    root.mkdir(mode=0o700, exist_ok=False)
    results = [run_bundle(document, root / name) for name in ("replay-a", "replay-b")]
    for result in results:
        if result.returncode != 0 or result.artifact["status"] != "passed":
            raise ValueError("capture replay failed; evidence retained in " + str(root))
    if (
        Path(results[0].artifact_path).read_bytes()
        != Path(results[1].artifact_path).read_bytes()
    ):
        raise ValueError(
            "recorded product replays differ; evidence retained in " + str(root)
        )
    expected = document["payload"]["assertions"]
    if any(
        results[0].artifact["final_state"][row["path"]] != row["value"]
        for row in expected
    ):
        raise ValueError("replay does not match captured product/lab outcomes")
    report = {
        "status": "passed",
        "scope": "product capture replay, not deterministic browser or realistic-persona certification",
        "bundle_sha256": hashlib.sha256(Path(bundle_path).read_bytes()).hexdigest(),
        "arrivals": document["payload"]["arrivals"],
        "byte_identical_replays": 2,
        "model_calls": 0,
        "view": results[0].artifact["final_state"]["view"],
        "lab": results[0].artifact["final_state"]["lab"],
        "artifacts": [str(result.artifact_path) for result in results],
    }
    with (root / "verification.json").open("x", encoding="ascii") as handle:
        handle.write(dumps(report) + "\n")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init")
    init.add_argument("--db", required=True, type=Path)
    init.add_argument("--scenario", choices=("healthy", "adverse"), required=True)
    live = commands.add_parser("serve")
    live.add_argument("--db", required=True, type=Path)
    live.add_argument("--port", type=int, default=0)
    proof = commands.add_parser("verify")
    proof.add_argument("--bundle", required=True, type=Path)
    proof.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            service = Service(
                args.db,
                initial_lab=initial_lab(
                    busy_remaining=0 if args.scenario == "healthy" else 1,
                    lose_ack=args.scenario == "adverse",
                ),
            )
            service.close()
        elif args.command == "serve":
            service = Service(args.db)
            try:
                serve(service, args.port)
            finally:
                service.close()
        else:
            print(dumps(verify(args.bundle, args.out)))
        return 0
    except (OSError, ValueError, Refuse) as error:
        print(f"glass-workbench: {error}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
