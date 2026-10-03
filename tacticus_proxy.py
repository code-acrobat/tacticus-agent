#!/usr/bin/env python3
"""Local host for the Tacticus Swagger UI + a thin CORS proxy to the real API.

- Serves ./swagger-ui.html at http://127.0.0.1:8124/
- Forwards everything else to https://api.tacticusgame.com, passing the body
  through untouched, and adds the Access-Control-* headers the browser needs.
- Answers CORS preflights (OPTIONS) itself: api.tacticusgame.com returns 403.

The UI calls this proxy as its base URL, so browser requests are either
same-origin (page loaded from here) or CORS-approved (page loaded elsewhere).
"""
import http.server
import pathlib
import sys
import urllib.error
import urllib.request

UPSTREAM = "https://api.tacticusgame.com"
HOST, PORT = "127.0.0.1", 8124
ROOT = pathlib.Path(__file__).resolve().parent

# Dropped from forwarded requests/responses: hop-by-hop + values we recompute.
HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade",
    "host", "origin", "referer", "content-length",
}

CORS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, PUT, PATCH, DELETE, HEAD, OPTIONS",
    "Access-Control-Allow-Headers": "X-API-KEY, Content-Type, Accept, Authorization",
    "Access-Control-Expose-Headers": "*",
    "Access-Control-Max-Age": "86400",
}


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _cors(self):
        for key, value in CORS.items():
            self.send_header(key, value)

    def _send(self, status, body, content_type="application/json; charset=utf-8", extra=()):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        for key, value in extra:
            self.send_header(key, value)
        self._cors()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body and self.command != "HEAD":
            self.wfile.write(body)

    # -- preflight ---------------------------------------------------------
    def do_OPTIONS(self):
        self._send(204, b"")

    # -- page --------------------------------------------------------------
    def _serve_page(self):
        page = ROOT / "swagger-ui.html"
        if page.exists():
            self._send(200, page.read_bytes(), "text/html; charset=utf-8")
        else:
            self._send(503, b'{"type":"PAGE_MISSING"}')

    # -- forwarding --------------------------------------------------------
    def _forward(self, method):
        length = int(self.headers.get("Content-Length") or 0)
        data = self.rfile.read(length) if length else None

        request = urllib.request.Request(UPSTREAM + self.path, data=data, method=method)
        for key, value in self.headers.items():
            if key.lower() in HOP_BY_HOP or key.lower() == "accept-encoding":
                continue  # let the body pass through exactly as received
            request.add_header(key, value)
        if data is not None:
            request.add_header("Content-Length", str(len(data)))

        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                status, headers, body = response.status, response.headers.items(), response.read()
        except urllib.error.HTTPError as error:
            status, headers, body = error.code, error.headers.items(), error.read()
        except Exception as error:  # upstream unreachable
            self._send(502, ('{"type":"PROXY_ERROR","detail":%s}' %
                             _json_str(str(error))).encode())
            return

        self.send_response(status)
        for key, value in headers:
            if key.lower() in HOP_BY_HOP:
                continue
            self.send_header(key, value)
        self._cors()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    # -- verbs -------------------------------------------------------------
    def do_GET(self):
        if self.path == "/" or self.path.startswith("/?"):
            self._serve_page()
        else:
            self._forward("GET")

    def do_HEAD(self):
        self._forward("HEAD")

    def do_POST(self):
        self._forward("POST")

    def do_PUT(self):
        self._forward("PUT")

    def do_PATCH(self):
        self._forward("PATCH")

    def do_DELETE(self):
        self._forward("DELETE")


def _json_str(value):
    import json
    return json.dumps(value)


class Server(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


if __name__ == "__main__":
    print(f"Tacticus proxy: http://{HOST}:{PORT}/  ->  {UPSTREAM}", file=sys.stderr)
    Server((HOST, PORT), Handler).serve_forever()
