#!/bin/sh

set -eu

ROOT=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
UNIT_NAME=io.github.lachlanchen.AstrillLazyRouter.NoVNC.service
UNIT_SOURCE=$ROOT/data/$UNIT_NAME
UNIT_DIRECTORY=${HOME}/.config/systemd/user
UNIT_PATH=$UNIT_DIRECTORY/$UNIT_NAME

for command in systemctl Xvfb openbox x11vnc websockify dbus-run-session; do
    command -v "$command" >/dev/null 2>&1 || {
        printf 'missing noVNC service dependency: %s\n' "$command" >&2
        exit 1
    }
done
[ -x "$ROOT/.venv/bin/astrill-lazy-gui" ] || {
    printf 'install the desktop application first: %s/scripts/install-desktop.sh\n' \
        "$ROOT" >&2
    exit 1
}

mkdir -p "$UNIT_DIRECTORY"
case $ROOT in
    *'
'*) printf 'repository path cannot contain a newline\n' >&2; exit 1 ;;
esac
escaped_root=$(printf '%s' "$ROOT" | sed \
    -e 's/[\\&|]/\\&/g' \
    -e 's/%/%%/g' \
    -e 's/"/\\"/g')
temporary_unit=$(mktemp "$UNIT_DIRECTORY/.astrill-lazy-novnc.XXXXXX")
trap 'rm -f "$temporary_unit"' EXIT HUP INT TERM
sed "s|@ROOT@|$escaped_root|g" "$UNIT_SOURCE" > "$temporary_unit"
install -m 0644 "$temporary_unit" "$UNIT_PATH"
systemctl --user daemon-reload
systemctl --user enable --now "$UNIT_NAME"
printf '%s\n' \
    'noVNC service enabled at http://127.0.0.1:6086/vnc.html?host=127.0.0.1&port=6086&autoconnect=1&resize=scale'
