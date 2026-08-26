"""
Does setting NODE_ENV in docker-compose / .env change anything?

The generated standalone entrypoint starts with:

    process.env.NODE_ENV = 'production'      (server.js, line 5)

so whatever the container is handed is overwritten before a single line of app
code runs. This proves it: NODE_ENV=development is passed in, COOKIE_SECURE is
left unset, and the login is driven exactly as before.

Expected: still broken. That is why a week of editing .env and
docker-compose.yml changed nothing.
"""

import os
import subprocess
import sys
import time
import urllib.request

from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
UI_ROOT = os.path.join(HERE, "..", "src", "packages", "dashboard-ui", ".next", "standalone")
SERVER_JS = os.path.join(UI_ROOT, "packages", "dashboard-ui", "server.js")
HOST = "wasphere.test"
API_PORT = 3200
UI_PORT = 3307


def wait_for(url, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=2).read()
            return True
        except Exception:
            time.sleep(0.3)
    return False


api_log = open(os.path.join(HERE, "fake-api-nodeenv.log"), "wb")
api = subprocess.Popen([sys.executable, os.path.join(HERE, "fake_dashboard_api.py")],
                       env=dict(os.environ, PORT=str(API_PORT)),
                       stdout=api_log, stderr=subprocess.STDOUT)

env = dict(os.environ)
env.pop("COOKIE_SECURE", None)          # deliberately unset - no patch in play
env.update(
    NODE_ENV="development",             # the "obvious" fix
    PORT=str(UI_PORT),
    HOSTNAME="127.0.0.1",
    DASHBOARD_API_URL="http://127.0.0.1:%d" % API_PORT,
    NEXT_TELEMETRY_DISABLED="1",
)
ui_log = open(os.path.join(HERE, "ui-nodeenv-development.log"), "wb")
ui = subprocess.Popen(["node", os.path.abspath(SERVER_JS)], env=env,
                      stdout=ui_log, stderr=subprocess.STDOUT,
                      cwd=os.path.abspath(UI_ROOT))

try:
    if not wait_for("http://127.0.0.1:%d/health" % API_PORT):
        sys.exit("stub api did not start")
    if not wait_for("http://127.0.0.1:%d/login" % UI_PORT):
        sys.exit("dashboard-ui did not start")

    base = "http://%s:%d" % (HOST, UI_PORT)
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--host-resolver-rules=MAP %s 127.0.0.1" % HOST])
        ctx = browser.new_context(viewport={"width": 1280, "height": 720})
        page = ctx.new_page()
        page.goto(base + "/login", wait_until="load")
        page.fill('input[type="email"], input[name="email"], #email', "user@example.com")
        page.fill('input[type="password"]', "correct-horse-battery")
        page.click('form button[type="submit"]')
        page.wait_for_load_state("networkidle")
        time.sleep(1.0)

        cookies = sorted(c["name"] for c in ctx.cookies())
        url = page.url
        text = page.inner_text("body").strip()
        page.screenshot(path=os.path.join(HERE, "C-nodeenv-development.png"))
        browser.close()
finally:
    ui.terminate(); ui.wait(timeout=15); ui_log.close()
    api.terminate(); api.wait(timeout=10); api_log.close()

print("=" * 74)
print("NODE_ENV=development passed to the container, COOKIE_SECURE unset")
print("=" * 74)
print("  cookies stored by browser : %s" % (cookies or "NONE"))
print("  URL after login           : %s" % url)
print("  page after login          : %s" % text.replace("\n", " / ")[:120])
print()
still_broken = cookies == [] and "reason=expired" in url
print("  NODE_ENV had no effect (still broken): %s" % still_broken)
print("=" * 74)
sys.exit(0 if still_broken else 1)
