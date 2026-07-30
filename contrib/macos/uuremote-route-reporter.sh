#!/bin/sh

# Reports only UU Remote's persistent UDP media socket to the router companion.

set -eu
PATH=/usr/bin:/bin:/usr/sbin:/sbin
export PATH
umask 077

flow_id=${ASTRILL_LAZY_FLOW_ID:-mac-uuremote}
source_address=${ASTRILL_LAZY_SOURCE_ADDRESS:-}
remote_host=${ASTRILL_LAZY_REMOTE_HOST:-glassagent-ubuntu}
remote_cli=${ASTRILL_LAZY_REMOTE_CLI:-}
router_address=${ASTRILL_LAZY_ROUTER_ADDRESS:-192.168.1.1}
heartbeat_seconds=${ASTRILL_LAZY_HEARTBEAT_SECONDS:-600}
state_dir=${HOME}/Library/Caches/AstrillLazyRouter
state_file=$state_dir/uuremote-route.state
lock_dir=$state_dir/uuremote-route.lock

fail() {
    printf 'uuremote-route-reporter: %s\n' "$*" >&2
    exit 1
}

valid_ipv4() {
    printf '%s\n' "${1:-}" | awk -F. '
        NF != 4 { exit 1 }
        {
            for (i = 1; i <= 4; i++) {
                if ($i !~ /^[0-9]+$/ || $i < 0 || $i > 255) exit 1
            }
        }
    '
}

case $flow_id in ''|*[!A-Za-z0-9._-]*) fail "invalid flow id" ;; esac
valid_ipv4 "$source_address" || fail "invalid source address"
valid_ipv4 "$router_address" || fail "invalid router address"
case $remote_host in ''|*[!A-Za-z0-9._@:-]*) fail "invalid remote host" ;; esac
case $remote_cli in /*) ;; *) fail "remote CLI must be an absolute path" ;; esac
case $heartbeat_seconds in ''|*[!0-9]*) fail "invalid heartbeat interval" ;; esac
[ "$heartbeat_seconds" -ge 300 ] || fail "heartbeat must be at least 300 seconds"

mkdir -p "$state_dir"
chmod 700 "$state_dir"
mkdir "$lock_dir" 2>/dev/null || exit 0
trap 'rmdir "$lock_dir" 2>/dev/null || true' EXIT HUP INT TERM

write_state() {
    saved_ports=$1
    saved_sync=$2
    saved_router_ready=$3
    candidate=$state_file.$$
    printf '%s|%s|%s\n' \
        "$saved_ports" "$saved_sync" "$saved_router_ready" > "$candidate"
    chmod 600 "$candidate"
    mv "$candidate" "$state_file"
}

process_ids=$(
    pgrep -u "$(id -u)" -f \
        '^/Applications/UURemote\.app/Contents/(MacOS|Helpers)/' 2>/dev/null ||
        true
)
ports=
if [ -n "$process_ids" ]; then
    process_list=$(printf '%s\n' "$process_ids" | paste -sd, -)
    ports=$(
        lsof -nP -a -p "$process_list" -iUDP 2>/dev/null |
            awk '
                NR > 1 && $5 == "IPv4" {
                    endpoint = $NF
                    if (endpoint ~ /->/) next
                    sub(/^.*:/, "", endpoint)
                    if (endpoint ~ /^[0-9]+$/ &&
                        endpoint >= 1 && endpoint <= 65535) {
                        print endpoint
                    }
                }
            ' |
            sort -nu |
            head -n 15 |
            paste -sd, -
    )
fi

previous_ports=
last_sync=0
previous_router_ready=0
if [ -r "$state_file" ]; then
    IFS='|' read -r previous_ports last_sync previous_router_ready \
        < "$state_file" || true
fi
case $last_sync in ''|*[!0-9]*) last_sync=0 ;; esac
case $previous_router_ready in 0|1) ;; *) previous_router_ready=0 ;; esac
now=$(date +%s)
router_ready=0
nc -z -G 1 "$router_address" 22 >/dev/null 2>&1 && router_ready=1

if [ "$router_ready" -ne 1 ]; then
    write_state "$previous_ports" "$last_sync" 0
    exit 0
fi
if [ -z "$ports" ] && [ -z "$previous_ports" ]; then
    write_state "" "$last_sync" 1
    exit 0
fi
if [ "$ports" = "$previous_ports" ] &&
    [ "$previous_router_ready" -eq 1 ] &&
    [ $((now - last_sync)) -lt "$heartbeat_seconds" ]; then
    exit 0
fi

set -- ssh \
    -o BatchMode=yes \
    -o PasswordAuthentication=no \
    -o KbdInteractiveAuthentication=no \
    -o StrictHostKeyChecking=yes \
    -o ConnectTimeout=5 \
    -o ConnectionAttempts=1 \
    "$remote_host" \
    "$remote_cli" app-flow
if [ -n "$ports" ]; then
    set -- "$@" set "$flow_id" "$source_address" udp "$ports" direct
    action="set"
else
    set -- "$@" delete "$flow_id"
    action="delete"
fi

"$@" >/dev/null
write_state "$ports" "$now" 1
logger -t astrill-lazy-uuremote-route \
    "action=$action source=$source_address udp_source_ports=${ports:-none}"
