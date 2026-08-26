"""
Stand-in for the WaSphere dashboard-api (NestJS + Postgres).

The bug has nothing to do with the API — it validates the credentials and hands
back tokens, which it does here too. Faking it lets the REAL dashboard-ui
standalone build run without a database, so the cookie behaviour under test is
the actual shipped code, not a re-implementation.

Endpoints the UI touches on the login -> overview path:
  GET  /auth/register-available   middleware.ts, for "/"
  POST /auth/login                app/api/auth/login/route.ts
  POST /auth/refresh              app/api/auth/refresh/route.ts
  GET  /auth/me                   app/api/auth/me/route.ts (AuthProvider)
  GET  /workspaces                app/dashboard/overview/page.tsx
"""

import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse

PORT = int(os.environ.get("PORT", "3200"))

USER = {"id": "u_1", "email": "user@example.com", "name": "Test User"}
TOKENS = {
    "accessToken": "fake.access.token",
    "refreshToken": "fake.refresh.token",
    "user": USER,
}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _json(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/auth/register-available":
            return self._json(200, {"available": False})
        if path == "/auth/me":
            return self._json(200, USER)
        if path == "/workspaces":
            # Empty list -> overview renders "No workspace configured".
            # That page only renders at all if the session cookie survived.
            return self._json(200, [])
        if path == "/health":
            return self._json(200, {"status": "ok"})
        return self._json(404, {"message": "not found"})

    def do_POST(self):
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"

        if path == "/auth/login":
            body = json.loads(raw or b"{}")
            # Credentials are deliberately accepted: the client's credentials
            # are fine too, that was never the failure.
            if body.get("email") and body.get("password"):
                return self._json(200, TOKENS)
            return self._json(401, {"message": "Invalid email or password."})

        if path == "/auth/refresh":
            return self._json(200, TOKENS)

        if path == "/auth/logout":
            return self._json(200, {})

        return self._json(404, {"message": "not found"})


if __name__ == "__main__":
    print("fake dashboard-api on %d" % PORT, flush=True)
    HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
