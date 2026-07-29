#!/bin/sh

set -eu

UNIT_NAME=io.github.lachlanchen.AstrillLazyRouter.NoVNC.service
UNIT_PATH=${HOME}/.config/systemd/user/$UNIT_NAME
ENV_PATH=${HOME}/.config/astrill-lazy/novnc.env

user_systemctl() {
    runtime_directory=${XDG_RUNTIME_DIR:-/run/user/$(id -u)}
    XDG_RUNTIME_DIR="$runtime_directory" \
        DBUS_SESSION_BUS_ADDRESS="unix:path=$runtime_directory/bus" \
        systemctl --user "$@"
}

user_systemctl disable --now "$UNIT_NAME" >/dev/null 2>&1 || true
rm -f "$UNIT_PATH" "$ENV_PATH"
user_systemctl daemon-reload
printf 'Removed the Astrill Lazy Router noVNC user service.\n'
