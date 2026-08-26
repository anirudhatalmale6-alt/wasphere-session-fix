"""
Drives a real Chromium through the WaSphere login flow twice:

  run A - SECURE=1  : exactly what the client's server does today
  run B - SECURE=0  : the same flow with the Secure attribute dropped

Both runs use the origin http://wasphere.test:<port>, resolved to 127.0.0.1 by
Chromium's own host-resolver rules. That matters: a hostname that is not
"localhost" and not a loopback IP literal is an INSECURE origin, which is the
same class of origin as http://<public-ip>:3004. Cookies are never sent
anywhere off this machine.

The control is run B. If B also failed, the harness itself would be broken and
run A would prove nothing.
"""

import os
import subprocess
import sys
import time
import urllib.request

from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
HOST = "wasphere.test"


def wait_for_port(port, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen("http://127.0.0.1:%d/login" % port, timeout=1).read()
            return True
        except Exception:
            time.sleep(0.2)
    return False


def run_case(secure, port, shot):
    env = dict(os.environ, SECURE="1" if secure else "0", PORT=str(port))
    log = open(os.path.join(HERE, "server-%s.log" % ("secure" if secure else "insecure")), "wb")
    srv = subprocess.Popen([sys.executable, os.path.join(HERE, "mini_wasphere.py")],
                           env=env, stdout=log, stderr=subprocess.STDOUT)
    try:
        if not wait_for_port(port):
            raise RuntimeError("mini server did not start on port %d" % port)

        base = "http://%s:%d" % (HOST, port)
        result = {}
        with sync_playwright() as p:
            browser = p.chromium.launch(args=[
                "--host-resolver-rules=MAP %s 127.0.0.1" % HOST,
            ])
            ctx = browser.new_context(viewport={"width": 1280, "height": 720})
            page = ctx.new_page()

            set_cookie_headers = []
            page.on("response", lambda r: set_cookie_headers.append(
                (r.url, r.headers.get("set-cookie", ""))
            ) if "/api/auth/login" in r.url else None)

            page.goto(base + "/login", wait_until="load")
            page.click("#submit")
            page.wait_for_load_state("networkidle")

            result["final_url"] = page.url
            result["body"] = page.inner_text("body").strip()
            result["cookies_in_jar"] = sorted(c["name"] for c in ctx.cookies())
            result["set_cookie_sent_by_server"] = set_cookie_headers

            page.screenshot(path=os.path.join(HERE, shot))
            browser.close()
        return result
    finally:
        srv.terminate()
        srv.wait(timeout=10)
        log.close()


def report(title, r):
    print("=" * 72)
    print(title)
    print("=" * 72)
    for url, sc in r["set_cookie_sent_by_server"]:
        print("  server Set-Cookie : %s" % sc.replace("\n", " | "))
    print("  cookies kept by browser : %s" % (r["cookies_in_jar"] or "NONE"))
    print("  final URL               : %s" % r["final_url"])
    print("  page text               : %s" % r["body"].replace("\n", " / "))
    print()


if __name__ == "__main__":
    a = run_case(True, 3104, "A-secure-cookie-over-http.png")
    report("RUN A - Secure cookie over plain http (what the server does today)", a)

    b = run_case(False, 3105, "B-secure-flag-removed.png")
    report("RUN B - CONTROL: identical flow, Secure attribute dropped", b)

    a_bug = ("expired" in a["final_url"]) and (a["cookies_in_jar"] == [])
    b_ok = ("DASHBOARD OK" in b["body"]) and ("wa_access" in b["cookies_in_jar"])

    print("=" * 72)
    print("A reproduces the client's bug : %s" % a_bug)
    print("B (control) logs in cleanly   : %s" % b_ok)
    print("ROOT CAUSE CONFIRMED          : %s" % (a_bug and b_ok))
    print("=" * 72)
    sys.exit(0 if (a_bug and b_ok) else 1)
