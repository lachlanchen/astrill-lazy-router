#!/bin/sh

set -eu

usage() {
    cat <<'EOF'
usage: install-novnc-service.sh [options]

Options:
  --display NUMBER   isolated X display number (default: 45)
  --vnc-port PORT    loopback VNC port (default: 5927)
  --web-port PORT    loopback noVNC port (default: 6087)
  --install-only     enable at boot without starting immediately
  -h, --help         show this help
EOF
}

ROOT=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
UNIT_NAME=io.github.lachlanchen.AstrillLazyRouter.NoVNC.service
UNIT_SOURCE=$ROOT/data/$UNIT_NAME
UNIT_DIRECTORY=${HOME}/.config/systemd/user
UNIT_PATH=$UNIT_DIRECTORY/$UNIT_NAME
CONFIG_DIRECTORY=${HOME}/.config/astrill-lazy
ENV_PATH=$CONFIG_DIRECTORY/novnc.env
DISPLAY_NUMBER=${ASTRILL_LAZY_NOVNC_DISPLAY:-45}
VNC_PORT=${ASTRILL_LAZY_VNC_PORT:-5927}
WEB_PORT=${ASTRILL_LAZY_NOVNC_PORT:-6087}
START_NOW=1

while [ "$#" -gt 0 ]; do
    case $1 in
        --display)
            [ "$#" -ge 2 ] || { usage >&2; exit 2; }
            DISPLAY_NUMBER=$2
            shift 2
            ;;
        --vnc-port)
            [ "$#" -ge 2 ] || { usage >&2; exit 2; }
            VNC_PORT=$2
            shift 2
            ;;
        --web-port)
            [ "$#" -ge 2 ] || { usage >&2; exit 2; }
            WEB_PORT=$2
            shift 2
            ;;
        --install-only)
            START_NOW=0
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            printf 'unknown option: %s\n' "$1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

valid_number() {
    case $1 in ''|*[!0-9]*) return 1 ;; esac
    [ "$1" -ge "$2" ] && [ "$1" -le "$3" ]
}

valid_number "$DISPLAY_NUMBER" 1 999 || {
    printf 'display number must be between 1 and 999\n' >&2
    exit 2
}
valid_number "$VNC_PORT" 1024 65535 || {
    printf 'VNC port must be between 1024 and 65535\n' >&2
    exit 2
}
valid_number "$WEB_PORT" 1024 65535 || {
    printf 'noVNC port must be between 1024 and 65535\n' >&2
    exit 2
}
[ "$VNC_PORT" != "$WEB_PORT" ] || {
    printf 'VNC and noVNC ports must be different\n' >&2
    exit 2
}

for command in systemctl Xvfb openbox x11vnc websockify dbus-run-session xdotool; do
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

user_systemctl() {
    runtime_directory=${XDG_RUNTIME_DIR:-/run/user/$(id -u)}
    XDG_RUNTIME_DIR="$runtime_directory" \
        DBUS_SESSION_BUS_ADDRESS="unix:path=$runtime_directory/bus" \
        systemctl --user "$@"
}

mkdir -p "$UNIT_DIRECTORY" "$CONFIG_DIRECTORY"
case $ROOT in
    *'
'*) printf 'repository path cannot contain a newline\n' >&2; exit 1 ;;
esac
escaped_root=$(printf '%s' "$ROOT" | sed \
    -e 's/[\\&|]/\\&/g' \
    -e 's/%/%%/g' \
    -e 's/"/\\"/g')
temporary_unit=$(mktemp "$UNIT_DIRECTORY/.astrill-lazy-novnc.XXXXXX")
temporary_env=$(mktemp "$CONFIG_DIRECTORY/.novnc.XXXXXX")
trap 'rm -f "$temporary_unit" "$temporary_env"' EXIT HUP INT TERM
sed "s|@ROOT@|$escaped_root|g" "$UNIT_SOURCE" > "$temporary_unit"
cat > "$temporary_env" <<EOF
ASTRILL_LAZY_NOVNC_DISPLAY=$DISPLAY_NUMBER
ASTRILL_LAZY_VNC_PORT=$VNC_PORT
ASTRILL_LAZY_NOVNC_PORT=$WEB_PORT
EOF
install -m 0644 "$temporary_unit" "$UNIT_PATH"
install -m 0600 "$temporary_env" "$ENV_PATH"
user_systemctl daemon-reload
if [ "$START_NOW" = 1 ]; then
    user_systemctl enable --now "$UNIT_NAME"
else
    user_systemctl enable "$UNIT_NAME"
fi
printf \
    'noVNC service enabled at http://127.0.0.1:%s/vnc.html?host=127.0.0.1&port=%s&autoconnect=1&resize=scale\n' \
    "$WEB_PORT" "$WEB_PORT"
