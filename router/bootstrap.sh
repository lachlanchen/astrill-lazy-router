#!/bin/sh

BASE=/tmp/astrill-lazy
ARCHIVE=/tmp/astrill-lazy-router.tar.gz
ENCODED=/tmp/astrill-lazy-router.b64
COUNT=$(nvram get astrill_lazy_pkg_count)
EXPECTED=$(nvram get astrill_lazy_pkg_md5)

case $COUNT in ''|*[!0-9]*) exit 1 ;; esac
[ "$COUNT" -gt 0 ] || exit 1
: > "$ENCODED"
index=0
while [ "$index" -lt "$COUNT" ]; do
    nvram get "astrill_lazy_pkg_$index" >> "$ENCODED"
    index=$((index + 1))
done
{
    printf 'begin-base64 644 astrill-lazy-router.tar.gz\n'
    cat "$ENCODED"
    printf '\n====\n'
} | uudecode -o "$ARCHIVE" || exit 1

ACTUAL=$(md5sum "$ARCHIVE" | awk '{print $1}')
[ -n "$EXPECTED" ] && [ "$ACTUAL" = "$EXPECTED" ] || exit 1
mkdir -p "$BASE"
[ ! -x "$BASE/alctl" ] || "$BASE/alctl" stop >/dev/null 2>&1
watchdog_pids() {
    ps w | awk '$0 ~ /\/tmp\/astrill-lazy\/alctl watchdog-loop[[:space:]]*$/ { print $1 }'
}
for pid in $(watchdog_pids); do
    kill "$pid" 2>/dev/null || true
done
attempts=0
while [ -n "$(watchdog_pids)" ] && [ "$attempts" -lt 2 ]; do
    attempts=$((attempts + 1))
    sleep 1
done
for pid in $(watchdog_pids); do
    kill -9 "$pid" 2>/dev/null || true
done
attempts=0
while [ -n "$(watchdog_pids)" ] && [ "$attempts" -lt 3 ]; do
    attempts=$((attempts + 1))
    sleep 1
done
[ -z "$(watchdog_pids)" ] || exit 1
tar -xzf "$ARCHIVE" -C /tmp || exit 1
chmod 700 "$BASE/alctl" "$BASE/alapi" "$BASE/alpage"
"$BASE/alctl" start >/dev/null 2>&1
