"""
Minimal stand-in for the WaSphere dashboard-ui auth flow.

It reproduces exactly what packages/dashboard-ui does, and nothing else:

  POST /api/auth/login       -> app/api/auth/login/route.ts
                                sets wa_access / wa_refresh with
                                httpOnly, sameSite=lax, path=/, and
                                secure = (NODE_ENV === "production")

  GET  /dashboard/overview   -> app/dashboard/layout.tsx
                                server-side guard: cookies().has("wa_access")
                                false -> redirect("/login?reason=expired")

  GET  /login                -> app/login/page.tsx
                                ?reason=expired renders
                                "Your session has expired. Please log in again."

SECURE=1 -> current production behaviour (the bug).
SECURE=0 -> the same flow with the Secure attribute dropped (the fix).
"""

import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from http.cookies import SimpleCookie
from urllib.parse import urlparse, parse_qs

SECURE = os.environ.get("SECURE", "1") == "1"
PORT = int(os.environ.get("PORT", "3104"))

LOGIN_PAGE = """<!doctype html><meta charset="utf-8"><title>WaSphere login</title>
<body>
<div id="notice">%NOTICE%</div>
<form id="f">
  <input id="email" value="user@example.com">
  <input id="password" type="password" value="correct-horse">
  <button type="submit" id="submit">Sign in</button>
</form>
<script>
document.getElementById('f').addEventListener('submit', async function (e) {
  e.preventDefault();
  const res = await fetch('/api/auth/login', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      email: document.getElementById('email').value,
      password: document.getElementById('password').value
    })
  });
  if (!res.ok) { document.getElementById('notice').textContent = 'Invalid email or password.'; return; }
  window.location.href = '/dashboard/overview';
});
</script>
</body>"""

EXPIRED = "Your session has expired. Please log in again."


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send(self, code, body=b"", headers=()):
        self.send_response(code)
        for k, v in headers:
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _cookies(self):
        raw = self.headers.get("Cookie")
        return SimpleCookie(raw) if raw else SimpleCookie()

    def do_GET(self):
        u = urlparse(self.path)

        if u.path == "/login":
            reason = parse_qs(u.query).get("reason", [""])[0]
            notice = EXPIRED if reason == "expired" else ""
            body = LOGIN_PAGE.replace("%NOTICE%", notice).encode()
            self._send(200, body)
            return

        if u.path.startswith("/dashboard"):
            # DashboardLayout server-side guard
            if not self._cookies().get("wa_access"):
                self._send(302, b"", [("Location", "/login?reason=expired")])
                return
            self._send(200, b"<title>Overview</title><h1>DASHBOARD OK</h1>")
            return

        self._send(404, b"not found")

    def do_POST(self):
        u = urlparse(self.path)
        if u.path != "/api/auth/login":
            self._send(404, b"not found")
            return

        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)

        # dashboard-api validated the credentials and returned tokens.
        attrs = "HttpOnly; SameSite=Lax; Path=/"
        if SECURE:
            attrs += "; Secure"

        headers = [
            ("Set-Cookie", "wa_access=access-token-value; Max-Age=900; " + attrs),
            ("Set-Cookie", "wa_refresh=refresh-token-value; Max-Age=604800; " + attrs),
        ]
        self._send(200, b'{"user":{"email":"user@example.com"}}', headers)


if __name__ == "__main__":
    mode = "SECURE=1 (NODE_ENV=production, current)" if SECURE else "SECURE=0 (fix)"
    print("mini-wasphere on port %d  -  %s" % (PORT, mode), flush=True)
    HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
