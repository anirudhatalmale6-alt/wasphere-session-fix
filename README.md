# WaSphere — "Your session has expired. Please log in again."

Diagnosis, fix and proof for a WaSphere v1.3.0 stack served over plain HTTP on
an IP and port (`http://<server-ip>:3004`).

Symptom: a user can be created in the panel, the password is accepted, but the
moment you sign in you land back on the login page with

> Your session has expired. Please log in again.

Deleting and recreating the user, or changing the password, makes no
difference — and that is the clue. The credentials were never the problem.

---

## 1. Root cause

The login itself works. What fails is the browser storing the session.

`packages/dashboard-ui/app/api/auth/login/route.ts` sets the two session
cookies like this:

```ts
const SECURE = process.env.NODE_ENV === "production";
...
cookieStore.set("wa_access", data.accessToken, {
  httpOnly: true,
  secure: SECURE,          // <—
  sameSite: "lax",
  path: "/",
  maxAge: 900,
});
```

`NODE_ENV` is `production` in the shipped `docker-compose.yml` (and in
`packages/dashboard-ui/Dockerfile`), so `secure` is `true` and both cookies go
out carrying the `Secure` attribute.

A browser only **stores** a `Secure` cookie when the page origin is a *secure
context*. `https://…` qualifies. So does `localhost` — which is exactly why the
stock instructions ("open http://localhost:3004") work fine, and why this never
shows up during a local trial. `http://<public-ip>:3004` does **not** qualify.
Chrome and Firefox drop the cookie on the floor, silently: nothing in the
browser console, nothing in the container logs.

From there the chain is mechanical:

| # | What happens | Where |
|---|---|---|
| 1 | `POST /api/auth/login` succeeds, returns **HTTP 200** and sets `wa_access` + `wa_refresh` with `Secure` | `app/api/auth/login/route.ts` |
| 2 | The browser discards both cookies — insecure origin | browser, silently |
| 3 | The page navigates to `/dashboard/overview` | `app/login/page.tsx` |
| 4 | The server-side guard reads `cookies().has("wa_access")` → **false** | `app/dashboard/layout.tsx:21` |
| 5 | `redirect("/login?reason=expired")` | same file |
| 6 | The login page renders the message for `reason=expired` | `app/login/page.tsx:19` |

The same `SECURE` constant is used in four route handlers — `login`,
`register`, `refresh` and `accept-invite` — so registration and token refresh
fail in exactly the same way.

### Why a week of editing `.env` and `docker-compose.yml` changed nothing

The obvious workaround is to stop `NODE_ENV` being `production` for the UI
container. It cannot work. The Next.js standalone entrypoint that the image
runs begins with:

```js
// .next/standalone/packages/dashboard-ui/server.js, line 5
process.env.NODE_ENV = 'production'
```

Whatever the container is handed is overwritten before a single line of
application code runs. Verified — see run C below: `NODE_ENV=development` was
passed in and the login still failed identically.

There is also no environment variable in stock WaSphere that controls the
cookie flag. `grep -ri "cookie_secure\|secure_cookie\|COOKIE_DOMAIN"` across the
whole repository returns nothing. So on plain HTTP the stock build cannot be
made to log in by configuration alone — the code has to change, or the site has
to get TLS.

---

## 2. Evidence

Everything below ran against the **real** WaSphere v1.3.0 `dashboard-ui`
production standalone build, with a stubbed `dashboard-api` so no database was
needed. The browser is a real Chromium driven by Playwright.

The origin is `http://wasphere.test:<port>`, resolved to `127.0.0.1` by
Chromium's own host-resolver rules. That detail matters: a hostname that is
neither `localhost` nor a loopback IP literal is an **insecure origin** — the
same class of origin as `http://<your-ip>:3004`. Using `localhost` would have
hidden the bug, which is the whole point.

| Run | Setting | Cookies kept | Ends up at |
|---|---|---|---|
| **A** | `COOKIE_SECURE=true` (as shipped) | **none** | `/login?reason=expired` — "Your session has expired." |
| **B** | `COOKIE_SECURE=false` (the fix) | `wa_access`, `wa_refresh` | `/dashboard/overview`, and still there after 3 refreshes |
| **C** | `NODE_ENV=development`, no patch | **none** | `/login?reason=expired` — proves the env-var route is a dead end |

Run **B is the control**. If B had failed too, the harness itself would be
broken and run A would prove nothing.

Screenshots are in `evidence/`:

- `real-A-1-after-login.png` — the client's exact error screen
- `real-A-2-after-3-refreshes.png` — still broken after refreshing
- `real-B-1-after-login.png` — logged in, overview renders
- `real-B-2-after-3-refreshes.png` — **deliverable 3**: session survives three refreshes
- `C-nodeenv-development.png` — `NODE_ENV` override, still broken

The harness is in `repro/` and re-runs with `python3 repro/run_real_stack.py`.

---

## 3. The fix

Two routes. They are not alternatives forever — option A is where you want to
end up.

### Option A — put the dashboard behind HTTPS (the right answer)

Point a domain at the server, issue a certificate (CyberPanel will do this with
Let's Encrypt), and reverse-proxy it to `127.0.0.1:3004`. Then:

```
DASHBOARD_UI_URL=https://app.your-domain.com
COOKIE_SECURE=true
```

Nothing else changes. The cookies are accepted because the origin is now
secure, and the session is no longer readable by anyone on the network path.

A reverse-proxy block that works for this app:

```nginx
location / {
    proxy_pass http://127.0.0.1:3004;
    proxy_http_version 1.1;
    proxy_set_header Host              $host;
    proxy_set_header X-Real-IP         $remote_addr;
    proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header Upgrade           $http_upgrade;
    proxy_set_header Connection        "upgrade";
}
```

`DASHBOARD_UI_URL` must match the address in the browser exactly — it is also
what `docker-compose.yml` feeds to `CORS_ORIGIN`.

### Option B — keep plain HTTP, make the flag configurable

This is what `fix/apply-fix.sh` does. It adds
`packages/dashboard-ui/lib/cookie-security.ts`:

```ts
export function cookieSecure(): boolean {
  const explicit = process.env.COOKIE_SECURE;
  if (explicit !== undefined && explicit.trim() !== "") {
    const v = explicit.trim().toLowerCase();
    return v === "true" || v === "1" || v === "yes";
  }
  return process.env.NODE_ENV === "production";
}
```

and calls it from the four auth routes instead of the hard-wired constant.
Defaults are unchanged: with `COOKIE_SECURE` unset the behaviour is exactly
what it is today, so this cannot break an HTTPS deployment.

It is read **inside the handler**, never at module load. A module-level read
gets evaluated during `next build` and baked into the image, which would ignore
whatever the container is actually given at runtime.

`docker-compose.yml` gains `COOKIE_SECURE: ${COOKIE_SECURE:-true}` on the
`dashboard-ui` service, and `.env` gains `COOKIE_SECURE=false`.

> **The trade-off, stated plainly.** With `COOKIE_SECURE=false` the session
> cookie travels in clear text over the network. Anyone positioned between the
> browser and the server — the same coffee-shop wifi, a compromised router, the
> hosting provider's network — can read it and use it to impersonate that user
> in the dashboard. It gets you working today. It is not where the panel should
> live permanently. Move to option A and set the flag back to `true`.

---

## 4. Applying it

Copy this folder to the server, then:

```
bash fix/apply-fix.sh /path/to/wasphere
```

The script:

1. patches the four auth routes, `docker-compose.yml` and `.env.example`
2. appends `COOKIE_SECURE=false` to `.env`, backing up `.env` first
3. asks before running `docker compose build dashboard-ui` and
   `docker compose up -d`

Only the `dashboard-ui` image is rebuilt. Postgres, the WhatsApp gateway and
their volumes are untouched, so existing WhatsApp sessions survive.

Running it twice is safe — each step detects that it has already been done.
`bash fix/apply-fix.sh /path/to/wasphere --revert` puts the source back exactly
as it was (verified: `git status` comes back clean).

Tested on a fresh clone of `wasphere/wasphere`: apply, re-apply, revert.

## 5. Verifying it worked

1. Sign in with a fresh user. You should land on the overview page.
2. Refresh three or four times. You should stay on it.
3. If you want the underlying proof, open DevTools → Application → Cookies
   before and after. `wa_access` and `wa_refresh` should now be listed for your
   origin; before the fix that list is empty.

If it still fails after the rebuild, the useful log is
`docker compose logs --tail=100 dashboard-ui` — but note that this particular
bug produces *no* log line at all, which is what made it hard to find.

## 6. For staging and future deployments

- Decide the deployment's scheme first. HTTPS → `COOKIE_SECURE=true`. Plain
  HTTP for a throwaway box → `COOKIE_SECURE=false`, and know what you are
  accepting.
- Keep `DASHBOARD_UI_URL` identical to what is in the browser's address bar,
  including scheme and port. It drives `CORS_ORIGIN` too.
- Do not bother setting `NODE_ENV` on `dashboard-ui`. The standalone server
  overwrites it on line 5.
- `localhost` is a secure context; a bare IP is not. Something that works at
  `http://localhost:3004` on your laptop can fail the moment it is reached by
  IP, and this is the class of bug it produces.
