#!/bin/sh

# Run the GTK controller on an isolated X display exposed through local noVNC.

set -eu

ROOT=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
DISPLAY_NUMBER=${ASTRILL_LAZY_NOVNC_DISPLAY:-45}
VNC_PORT=${ASTRILL_LAZY_VNC_PORT:-5927}
WEB_PORT=${ASTRILL_LAZY_NOVNC_PORT:-6087}
GUI=${ASTRILL_LAZY_GUI:-$ROOT/.venv/bin/astrill-lazy-gui}
RUNTIME_DIR=${XDG_RUNTIME_DIR:-/tmp}/astrill-lazy-novnc-$DISPLAY_NUMBER
DISPLAY_VALUE=:$DISPLAY_NUMBER

for value in "$DISPLAY_NUMBER" "$VNC_PORT" "$WEB_PORT"; do
    case $value in
        ''|*[!0-9]*) printf 'display and ports must be integers\n' >&2; exit 2 ;;
    esac
done
for command in Xvfb openbox x11vnc websockify dbus-run-session xdotool; do
    command -v "$command" >/dev/null 2>&1 || {
        printf 'missing debug dependency: %s\n' "$command" >&2
        exit 1
    }
done
[ -x "$GUI" ] || {
    printf 'GUI executable was not found: %s\n' "$GUI" >&2
    exit 1
}

mkdir -p "$RUNTIME_DIR"
chmod 700 "$RUNTIME_DIR"
unset WAYLAND_DISPLAY

cleanup() {
    for process_id in \
        "${fit_pid:-}" "${app_pid:-}" "${websockify_pid:-}" \
        "${vnc_pid:-}" "${wm_pid:-}" "${xvfb_pid:-}"; do
        [ -z "$process_id" ] || kill "$process_id" 2>/dev/null || true
    done
}
trap cleanup EXIT INT TERM

fit_app_window() {
    attempts=0
    while [ "$attempts" -lt 40 ]; do
        attempts=$((attempts + 1))
        window=$(DISPLAY=$DISPLAY_VALUE XAUTHORITY= xdotool \
            search --onlyvisible --pid "$app_pid" 2>/dev/null |
            tail -n 1)
        if [ -z "$window" ]; then
            window=$(DISPLAY=$DISPLAY_VALUE XAUTHORITY= xdotool \
                search --onlyvisible --name '^Astrill Lazy Router$' 2>/dev/null |
                tail -n 1)
        fi
        if [ -n "$window" ]; then
            geometry=$(DISPLAY=$DISPLAY_VALUE XAUTHORITY= \
                xdotool getdisplaygeometry)
            width=${geometry% *}
            height=${geometry#* }
            DISPLAY=$DISPLAY_VALUE XAUTHORITY= xdotool \
                windowmap "$window" \
                windowmove --sync "$window" 0 0 \
                windowsize --sync "$window" "$width" "$height" \
                windowraise "$window"
            return
        fi
        sleep 0.25
    done
    printf 'could not find the Astrill Lazy Router window to fit\n' >&2
}

Xvfb "$DISPLAY_VALUE" -screen 0 1440x1000x24 -nolisten tcp -ac -noreset \
    -extension Composite >"$RUNTIME_DIR/xvfb.log" 2>&1 &
xvfb_pid=$!
sleep 1
kill -0 "$xvfb_pid" 2>/dev/null || {
    cat "$RUNTIME_DIR/xvfb.log" >&2
    exit 1
}

DISPLAY=$DISPLAY_VALUE openbox >"$RUNTIME_DIR/openbox.log" 2>&1 &
wm_pid=$!
x11vnc -display "$DISPLAY_VALUE" -rfbport "$VNC_PORT" -localhost -nopw \
    -forever -shared -quiet >"$RUNTIME_DIR/x11vnc.log" 2>&1 &
vnc_pid=$!
websockify --web=/usr/share/novnc "127.0.0.1:$WEB_PORT" \
    "127.0.0.1:$VNC_PORT" >"$RUNTIME_DIR/websockify.log" 2>&1 &
websockify_pid=$!

printf 'noVNC: http://127.0.0.1:%s/vnc.html?host=127.0.0.1&port=%s&autoconnect=1&resize=scale\n' \
    "$WEB_PORT" "$WEB_PORT"
dbus-run-session -- env DISPLAY="$DISPLAY_VALUE" GDK_BACKEND=x11 \
    GSK_RENDERER=cairo GTK_USE_PORTAL=0 NO_AT_BRIDGE=1 "$GUI" &
app_pid=$!
fit_app_window &
fit_pid=$!
wait "$app_pid"
