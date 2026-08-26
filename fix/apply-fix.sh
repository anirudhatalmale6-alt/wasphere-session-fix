#!/usr/bin/env bash
#
# WaSphere — fix for "Your session has expired. Please log in again."
#
# Run it from anywhere:
#     bash apply-fix.sh /root/wasphere
#
# It is safe to run twice: every step checks whether it has already been done.
# Nothing is deleted, and a timestamped backup of .env is taken before it is
# touched. Use --revert to put everything back.
#
set -euo pipefail

WASPHERE_DIR="${1:-$(pwd)}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PATCH="$SCRIPT_DIR/wasphere-cookie-secure.patch"
REVERT=0
ASSUME_YES=0

for arg in "$@"; do
  case "$arg" in
    --revert) REVERT=1 ;;
    -y|--yes) ASSUME_YES=1 ;;
  esac
done

say()  { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
ok()   { printf '    \033[32mok\033[0m   %s\n' "$*"; }
skip() { printf '    \033[33mskip\033[0m %s\n' "$*"; }
die()  { printf '\n\033[31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

[ -d "$WASPHERE_DIR" ] || die "no such directory: $WASPHERE_DIR"
cd "$WASPHERE_DIR"
[ -f docker-compose.yml ] || die "no docker-compose.yml in $WASPHERE_DIR — pass the WaSphere folder as the first argument"
[ -f "$PATCH" ] || die "patch file not found next to this script: $PATCH"

MARKER="packages/dashboard-ui/lib/cookie-security.ts"

# ── which docker compose is installed (not needed to patch, only to rebuild) ──
if docker compose version >/dev/null 2>&1; then
  DC="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
  DC="docker-compose"
else
  DC=""
fi

# ── revert ───────────────────────────────────────────────────────────────────
if [ "$REVERT" = "1" ]; then
  say "Reverting the patch"
  if [ -f "$MARKER" ]; then
    patch -p1 -R --batch < "$PATCH" && ok "source restored"
  else
    skip "patch was not applied"
  fi
  say "Now rebuild:  ${DC:-docker compose} build dashboard-ui && ${DC:-docker compose} up -d"
  exit 0
fi

# ── 1. patch the source ──────────────────────────────────────────────────────
say "1/3  Patching the dashboard-ui auth routes"
if [ -f "$MARKER" ]; then
  skip "already patched ($MARKER exists)"
else
  patch -p1 --batch --forward < "$PATCH"
  ok "4 auth routes + docker-compose.yml + .env.example updated"
fi

# ── 2. set COOKIE_SECURE in .env ─────────────────────────────────────────────
say "2/3  Setting COOKIE_SECURE in .env"
[ -f .env ] || die ".env not found in $WASPHERE_DIR"

if grep -qE '^[[:space:]]*COOKIE_SECURE=' .env; then
  CURRENT=$(grep -E '^[[:space:]]*COOKIE_SECURE=' .env | head -1)
  skip "already present — $CURRENT"
else
  cp .env ".env.backup-$(date +%Y%m%d-%H%M%S)"
  {
    echo ""
    echo "# Added by apply-fix.sh — see README."
    echo "# false: the dashboard is served over plain http, so the browser would"
    echo "# otherwise throw the login cookie away. Set back to true once the"
    echo "# dashboard is behind HTTPS."
    echo "COOKIE_SECURE=false"
  } >> .env
  ok "COOKIE_SECURE=false appended (.env backed up first)"
fi

# ── 3. rebuild ───────────────────────────────────────────────────────────────
say "3/3  Rebuilding and restarting the dashboard UI container"
if [ -z "$DC" ]; then
  echo "    Neither 'docker compose' nor 'docker-compose' was found on this host."
  echo "    The source and .env are patched — rebuild the dashboard-ui service"
  echo "    however you normally do, and the fix takes effect."
  exit 0
fi
echo "    This runs:  $DC build dashboard-ui  then  $DC up -d"
echo "    Only the dashboard-ui image is rebuilt. Postgres, the WhatsApp"
echo "    gateway and their volumes are not touched."

if [ "$ASSUME_YES" != "1" ]; then
  printf '\n    Continue? [y/N] '
  read -r reply < /dev/tty
  case "$reply" in
    y|Y|yes|YES) ;;
    *) echo "    Stopped. Files are patched; rebuild when you are ready."; exit 0 ;;
  esac
fi

$DC build dashboard-ui
$DC up -d

say "Done"
cat <<'EOF'
    Open the dashboard and sign in. The session should now hold across
    refreshes.

    If it still fails, capture this and send it over — it shows whether the
    cookie is leaving the server at all:

        docker compose logs --tail=100 dashboard-ui

    Reminder: COOKIE_SECURE=false means the session cookie travels in clear
    text. Put the dashboard behind HTTPS when you can, then set it back to
    true and rebuild. See the README for that path.
EOF
