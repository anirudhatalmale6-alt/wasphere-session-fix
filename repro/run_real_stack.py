"""
Runs the REAL WaSphere dashboard-ui (patched, production standalone build)
against a stubbed dashboard-api, and drives a real Chromium through the login
flow twice:

  RUN A  COOKIE_SECURE=true   - what the client's server does today
  RUN B  COOKIE_SECURE=false  - the fix

Both runs use the origin http://wasphere.test:<port>, mapped to 127.0.0.1 by
Chromium's own host resolver. A hostname that is neither "localhost" nor a
loopback IP literal is an INSECURE origin - the same class of origin as
http://<public-ip>:3004. Nothing leaves this machine.

RUN B is the control. If B failed too, the harness would be broken and RUN A
would prove nothing.
"""

import os
import subprocess
import sys
import time
import urllib.request

from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
UI_ROOT = os.path.join(
    HERE, "..", "src", "packages", "dashboard-ui", ".next", "standalone"
)
SERVER_JS = os.path.join(UI_ROOT, "packages", "dashboard-ui", "server.js")
HOST = "wasphere.test"
API_PORT = 3200


def wait_for(url, timeout=60):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=2).read()
            return True
        except Exception as e:
            last = e
            time.sleep(0.3)
    print("  ! never came up: %s (%s)" % (url, last))
    return False


def start(cmd, env, logname):
    log = open(os.path.join(HERE, logname), "wb")
    p = subprocess.Popen(cmd, env=env, stdout=log, stderr=subprocess.STDOUT,
                         cwd=os.path.abspath(UI_ROOT))
    return p, log


def run_case(cookie_secure, port, shot):
    env = dict(
        os.environ,
        NODE_ENV="production",
        PORT=str(port),
        HOSTNAME="127.0.0.1",
        DASHBOARD_API_URL="http://127.0.0.1:%d" % API_PORT,
        COOKIE_SECURE="true" if cookie_secure else "false",
        NEXT_TELEMETRY_DISABLED="1",
    )
    tag = "true" if cookie_secure else "false"
    ui, uilog = start(["node", os.path.abspath(SERVER_JS)], env, "ui-%s.log" % tag)
    try:
        if not wait_for("http://127.0.0.1:%d/login" % port):
            raise RuntimeError("dashboard-ui did not start")

        base = "http://%s:%d" % (HOST, port)
        out = {}
        with sync_playwright() as p:
            browser = p.chromium.launch(
                args=["--host-resolver-rules=MAP %s 127.0.0.1" % HOST]
            )
            ctx = browser.new_context(viewport={"width": 1280, "height": 720})
            page = ctx.new_page()

            statuses = []
            page.on("response", lambda r: statuses.append((r.request.method, r.url, r.status)))

            page.goto(base + "/login", wait_until="load")
            page.fill('input[type="email"], input[name="email"], #email', "user@example.com")
            page.fill('input[type="password"]', "correct-horse-battery")
            page.click('form button[type="submit"]')
            page.wait_for_load_state("networkidle")
            time.sleep(1.0)

            out["login_status"] = [s for s in statuses if "/api/auth/login" in s[1]]
            out["after_login_url"] = page.url
            out["after_login_text"] = page.inner_text("body").strip()
            out["cookies"] = sorted(c["name"] for c in ctx.cookies())
            page.screenshot(path=os.path.join(HERE, shot % "1-after-login"))

            # Deliverable 3: does the session survive repeated refreshes?
            refreshes = []
            for i in range(3):
                page.reload(wait_until="networkidle")
                refreshes.append(page.url)
            out["refresh_urls"] = refreshes
            out["final_text"] = page.inner_text("body").strip()
            page.screenshot(path=os.path.join(HERE, shot % "2-after-3-refreshes"))

            browser.close()
        return out
    finally:
        ui.terminate()
        try:
            ui.wait(timeout=15)
        except Exception:
            ui.kill()
        uilog.close()


def report(title, r):
    print("=" * 74)
    print(title)
    print("=" * 74)
    for m, u, s in r["login_status"]:
        print("  %-4s %-52s -> HTTP %s" % (m, u, s))
    print("  cookies stored by browser : %s" % (r["cookies"] or "NONE"))
    print("  URL after login           : %s" % r["after_login_url"])
    print("  page after login          : %s" % r["after_login_text"].replace("\n", " / ")[:150])
    print("  URLs after 3 refreshes    :")
    for u in r["refresh_urls"]:
        print("      %s" % u)
    print("  page after 3 refreshes    : %s" % r["final_text"].replace("\n", " / ")[:150])
    print()


if __name__ == "__main__":
    api, apilog = start([sys.executable, os.path.join(HERE, "fake_dashboard_api.py")],
                        dict(os.environ, PORT=str(API_PORT)), "fake-api.log")
    try:
        if not wait_for("http://127.0.0.1:%d/health" % API_PORT):
            sys.exit("stub dashboard-api did not start")

        a = run_case(True, 3304, "real-A-%s.png")
        report("RUN A - COOKIE_SECURE=true over plain http (the bug, as shipped)", a)

        b = run_case(False, 3305, "real-B-%s.png")
        report("RUN B - CONTROL: COOKIE_SECURE=false, everything else identical", b)
    finally:
        api.terminate()
        api.wait(timeout=10)
        apilog.close()

    a_bug = (
        a["cookies"] == []
        and "reason=expired" in a["after_login_url"]
        and "session has expired" in a["after_login_text"].lower()
    )
    b_ok = (
        "wa_access" in b["cookies"]
        and "/dashboard/overview" in b["after_login_url"]
        and all("/dashboard/overview" in u for u in b["refresh_urls"])
    )

    print("=" * 74)
    print("A reproduces the reported bug        : %s" % a_bug)
    print("B logs in AND survives 3 refreshes   : %s" % b_ok)
    print("ROOT CAUSE CONFIRMED ON REAL BUILD   : %s" % (a_bug and b_ok))
    print("=" * 74)
    sys.exit(0 if (a_bug and b_ok) else 1)
