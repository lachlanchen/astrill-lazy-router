#!/bin/sh

set -eu

ROOT=$(CDPATH='' cd -- "$(dirname -- "$0")/../.." && pwd)
SOURCE=${1:-192.168.1.99}
REMOTE_HOST=${2:-glassagent-ubuntu}
REMOTE_CLI=${3:-/home/lachlan/Projects/astrill-lazy/.venv/bin/astrill-lazy}
ROUTER_ADDRESS=${4:-192.168.1.1}
LABEL=com.lachlan.astrill-lazy-uuremote-route
SUPPORT_DIR="$HOME/Library/Application Support/Astrill Lazy Router"
PROGRAM="$SUPPORT_DIR/uuremote-route-reporter"
AGENTS_DIR="$HOME/Library/LaunchAgents"
PLIST="$AGENTS_DIR/$LABEL.plist"
TEMPLATE="$ROOT/contrib/macos/$LABEL.plist.in"
LOG="$HOME/Library/Logs/AstrillLazyRouter-uuremote-route.log"
DOMAIN=gui/$(id -u)

if [ "${1:-}" = "--uninstall" ]; then
    launchctl bootout "$DOMAIN/$LABEL" >/dev/null 2>&1 || true
    rm -f "$PLIST" "$PROGRAM"
    printf 'Removed the UU Remote route reporter.\n'
    exit 0
fi

printf '%s\n' "$SOURCE" | awk -F. '
    NF != 4 { exit 1 }
    {
        for (i = 1; i <= 4; i++) {
            if ($i !~ /^[0-9]+$/ || $i < 0 || $i > 255) exit 1
        }
    }
' || {
    printf 'Source must be an IPv4 address.\n' >&2
    exit 2
}
case $REMOTE_HOST in ''|*[!A-Za-z0-9._@:-]*)
    printf 'Remote host contains unsupported characters.\n' >&2
    exit 2
esac
case $REMOTE_CLI in /*) ;; *)
    printf 'Remote CLI must be an absolute path.\n' >&2
    exit 2
esac
printf '%s\n' "$ROUTER_ADDRESS" | awk -F. '
    NF != 4 { exit 1 }
    {
        for (i = 1; i <= 4; i++) {
            if ($i !~ /^[0-9]+$/ || $i < 0 || $i > 255) exit 1
        }
    }
' || {
    printf 'Router address must be an IPv4 address.\n' >&2
    exit 2
}

mkdir -p "$SUPPORT_DIR" "$AGENTS_DIR" "$(dirname "$LOG")"
chmod 700 "$SUPPORT_DIR"
install -m 700 "$ROOT/contrib/macos/uuremote-route-reporter.sh" "$PROGRAM"

temporary=$(mktemp "${TMPDIR:-/tmp}/astrill-lazy-uuremote-route.XXXXXX")
trap 'rm -f "$temporary"' EXIT HUP INT TERM
sed \
    -e "s|@PROGRAM@|$PROGRAM|g" \
    -e "s|@SOURCE_ADDRESS@|$SOURCE|g" \
    -e "s|@REMOTE_HOST@|$REMOTE_HOST|g" \
    -e "s|@REMOTE_CLI@|$REMOTE_CLI|g" \
    -e "s|@ROUTER_ADDRESS@|$ROUTER_ADDRESS|g" \
    -e "s|@LOG@|$LOG|g" \
    "$TEMPLATE" > "$temporary"
plutil -lint "$temporary" >/dev/null
install -m 600 "$temporary" "$PLIST"

launchctl bootout "$DOMAIN/$LABEL" >/dev/null 2>&1 || true
launchctl bootstrap "$DOMAIN" "$PLIST"
launchctl enable "$DOMAIN/$LABEL"
launchctl kickstart "$DOMAIN/$LABEL"
printf 'Installed the UU Remote route reporter for %s.\n' "$SOURCE"
