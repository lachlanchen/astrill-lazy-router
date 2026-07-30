#!/bin/sh

set -eu

PURGE=${1:-}
case $PURGE in ''|--purge) ;; *)
    printf '%s\n' "Usage: uninstall-agent.sh [--purge]" >&2
    exit 2
    ;;
esac

case $(uname -s) in
    Darwin)
        DEST="$HOME/Library/Application Support/Astrill Lazy Router/agent"
        PLIST="$HOME/Library/LaunchAgents/com.lachlan.astrill-lazy-agent.plist"
        launchctl bootout "gui/$(id -u)/com.lachlan.astrill-lazy-agent" \
            >/dev/null 2>&1 || true
        rm -f "$PLIST"
        ;;
    Linux)
        DEST="${XDG_CONFIG_HOME:-"$HOME/.config"}/astrill-lazy/agent"
        UNIT="${XDG_CONFIG_HOME:-"$HOME/.config"}/systemd/user/astrill-lazy-agent.service"
        systemctl --user disable --now astrill-lazy-agent.service \
            >/dev/null 2>&1 || true
        rm -f "$UNIT"
        systemctl --user daemon-reload
        ;;
    *)
        printf '%s\n' "The portable agent supports macOS and Linux." >&2
        exit 1
        ;;
esac

if [ "$PURGE" = "--purge" ]; then
    rm -rf "$DEST"
    printf 'Removed service and agent state: %s\n' "$DEST"
else
    printf 'Removed service; retained enrolled state at %s\n' "$DEST"
fi
