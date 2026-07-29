#!/bin/sh

set -eu

UNIT_NAME=io.github.lachlanchen.AstrillLazyRouter.NoVNC.service
UNIT_PATH=${HOME}/.config/systemd/user/$UNIT_NAME

systemctl --user disable --now "$UNIT_NAME" >/dev/null 2>&1 || true
rm -f "$UNIT_PATH"
systemctl --user daemon-reload
printf 'Removed the Astrill Lazy Router noVNC user service.\n'
