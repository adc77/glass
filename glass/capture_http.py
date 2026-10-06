"""Loopback-only capture demonstration; this is not a production HTTP server."""

from http.server import BaseHTTPRequestHandler, HTTPServer

from seam.canon import dumps, loads
from seam.errors import Fault, Refuse

MAX_REQUEST = 8192


def make_server(service, port):
    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(2)

        def log_message(self, fmt, *args):
            return

        def respond(self, status, value):
            data = (dumps(value) + "\n").encode("ascii")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path not in {"/state", "/capture/latest"}:
                self.respond(404, {"error": "not_found"})
                return
            try:
                result = (
                    service.snapshot()
                    if self.path == "/state"
                    else service.latest_capture()
                )
            except (ValueError, TypeError, Refuse) as err:
                self.respond(400, {"error": str(err)})
                return
            except Fault as err:
                self.respond(400, {"error": err.code})
                return
            self.respond(200, result)

        def do_POST(self):
            if self.path not in {
                "/events",
                "/capture/start",
                "/capture/finish",
                "/capture/abort",
            }:
                self.respond(404, {"error": "not_found"})
                return
            try:
                length = int(self.headers["Content-Length"])
                if not 0 < length <= MAX_REQUEST or "Transfer-Encoding" in self.headers:
                    raise ValueError("invalid request size or transfer encoding")
                body = loads(self.rfile.read(length))
                if self.path == "/events":
                    if type(body) is not dict or set(body) != {"handler", "body"}:
                        raise ValueError("events require handler and body")
                    result = service.submit(body["handler"], body["body"])
                else:
                    if type(body) is not dict or body:
                        raise ValueError("capture requests require an empty object")
                    operation = {
                        "/capture/start": service.begin_capture,
                        "/capture/finish": service.finish_capture,
                        "/capture/abort": service.abort_capture,
                    }[self.path]
                    result = operation()
            except (ValueError, TypeError, Refuse) as err:
                self.respond(400, {"error": str(err)})
                return
            except Fault as err:
                self.respond(400, {"error": err.code})
                return
            self.respond(200, result)

    server = HTTPServer(("localhost", port), Handler)
    server.timeout = 0.05
    return server


def serve(service, port):
    server = make_server(service, port)
    try:
        print(f"glass listening on localhost:{server.server_port}", flush=True)
        while True:
            server.handle_request()
            service.pump()
    finally:
        server.server_close()
