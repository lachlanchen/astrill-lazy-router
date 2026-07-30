#!/bin/sh

BASE=/tmp/astrill-lazy
ARCHIVE=/tmp/astrill-lazy-router.$$.tar.gz
ENCODED=/tmp/astrill-lazy-router.$$.b64
BOOTSTRAP_COPY=/tmp/astrill-lazy-bootstrap.$$.sh
STAGE=/tmp/astrill-lazy-install.$$
LOCK=$BASE/controller.lock
locked=false

bootstrap_cleanup() {
    rm -rf "$STAGE"
    rm -f "$ENCODED" "$ARCHIVE" "$BOOTSTRAP_COPY" "$BASE/"*.new.$$
    if [ "$locked" = true ]; then
        rm -f "$LOCK/pid"
        rmdir "$LOCK" 2>/dev/null || true
    fi
}
trap bootstrap_cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

normalize_md5() {
    case $1 in ''|*[!0-9a-fA-F]*) return 1 ;; esac
    [ "${#1}" -eq 32 ] || return 1
    printf '%s' "$1" | tr 'A-F' 'a-f'
}

RECOVERY=${ASTRILL_LAZY_RECOVERY:-0}
if [ "$RECOVERY" = 1 ]; then
    RECOVERY_VERSION=${ASTRILL_LAZY_RECOVERY_VERSION:-}
    case $RECOVERY_VERSION in ''|*[!0-9A-Za-z._-]*) exit 1 ;; esac
    RECOVERY_PACKAGE_MD5=$(normalize_md5 \
        "${ASTRILL_LAZY_RECOVERY_PACKAGE_MD5:-}") || exit 1
    RECOVERY_BOOTSTRAP_MD5=$(normalize_md5 \
        "${ASTRILL_LAZY_RECOVERY_BOOTSTRAP_MD5:-}") || exit 1
else
    [ "$RECOVERY" = 0 ] || exit 1
    BOOTSTRAP_EXPECTED=$(normalize_md5 \
        "${ASTRILL_LAZY_BOOTSTRAP_MD5:-}") || exit 1
fi

verify_bootstrap_identity() {
    bootstrap_value=$(nvram get astrill_lazy_bootstrap) || return 1
    [ -n "$(printf '%s' "$bootstrap_value" | tr -d '[:space:]')" ] ||
        return 1
    printf '%s\n' "$bootstrap_value" > "$BOOTSTRAP_COPY" || return 1
    BOOTSTRAP_ACTUAL=$(md5sum "$BOOTSTRAP_COPY" | awk '{print $1}') ||
        return 1
    if [ "$RECOVERY" = 1 ]; then
        [ "$BOOTSTRAP_ACTUAL" = "$RECOVERY_BOOTSTRAP_MD5" ] &&
            [ "$(nvram get astrill_lazy_installed)" = 1 ] &&
            [ "$(nvram get astrill_lazy_version)" = "$RECOVERY_VERSION" ] &&
            [ "$(nvram get astrill_lazy_pkg_md5 | tr 'A-F' 'a-f')" = \
                "$RECOVERY_PACKAGE_MD5" ]
    else
        stored_bootstrap_md5=$(nvram get astrill_lazy_bootstrap_md5) ||
            return 1
        [ "$(printf '%s' "$stored_bootstrap_md5" | tr 'A-F' 'a-f')" = \
            "$BOOTSTRAP_EXPECTED" ] &&
            [ "$BOOTSTRAP_ACTUAL" = "$BOOTSTRAP_EXPECTED" ]
    fi
}

verify_bootstrap_identity || exit 1

COUNT=$(nvram get astrill_lazy_pkg_count) || exit 1
EXPECTED=$(nvram get astrill_lazy_pkg_md5) || exit 1
case $COUNT in ''|*[!0-9]*) exit 1 ;; esac
case $EXPECTED in ''|*[!0-9a-fA-F]*) exit 1 ;; esac
[ "$COUNT" -gt 0 ] && [ "${#EXPECTED}" -eq 32 ] || exit 1
EXPECTED=$(printf '%s' "$EXPECTED" | tr 'A-F' 'a-f')
[ "$RECOVERY" != 1 ] || [ "$EXPECTED" = "$RECOVERY_PACKAGE_MD5" ] || exit 1
: > "$ENCODED" || exit 1
index=0
while [ "$index" -lt "$COUNT" ]; do
    chunk=$(nvram get "astrill_lazy_pkg_$index") || exit 1
    [ -n "$chunk" ] || exit 1
    printf '%s' "$chunk" >> "$ENCODED" || exit 1
    index=$((index + 1))
done
{
    printf 'begin-base64 644 astrill-lazy-router.tar.gz\n'
    cat "$ENCODED"
    printf '\n====\n'
} | uudecode -o "$ARCHIVE" || exit 1
ACTUAL=$(md5sum "$ARCHIVE" | awk '{print $1}') || exit 1
[ "$ACTUAL" = "$EXPECTED" ] || exit 1
mkdir -p "$BASE" || exit 1

watchdog_pids() {
    ps w | awk '$0 ~ /\/tmp\/astrill-lazy\/alctl watchdog-loop[[:space:]]*$/ { print $1 }'
}

stop_watchdogs() {
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
    [ -z "$(watchdog_pids)" ]
}

# Stop legacy/refresh owners before waiting, then close the final respawn
# window after this process publishes ownership of the shared lock.
stop_watchdogs || exit 1

attempts=0
while ! mkdir "$LOCK" 2>/dev/null; do
    lock_pid=$(cat "$LOCK/pid" 2>/dev/null || printf 0)
    case $lock_pid in ''|*[!0-9]*) lock_pid=0 ;; esac
    if [ "$lock_pid" -le 1 ] || ! kill -0 "$lock_pid" 2>/dev/null; then
        sleep 1
        lock_pid=$(cat "$LOCK/pid" 2>/dev/null || printf 0)
        case $lock_pid in ''|*[!0-9]*) lock_pid=0 ;; esac
    fi
    if [ "$lock_pid" -le 1 ] || ! kill -0 "$lock_pid" 2>/dev/null; then
        rm -f "$LOCK/pid"
        rmdir "$LOCK" 2>/dev/null || true
        continue
    fi
    lock_command=$(tr '\000' ' ' < "/proc/$lock_pid/cmdline" 2>/dev/null || true)
    case " $lock_command " in
        *" $BASE/alctl refresh "*)
            kill -9 "$lock_pid" 2>/dev/null || true
            sleep 1
            continue
            ;;
    esac
    attempts=$((attempts + 1))
    [ "$attempts" -lt 90 ] || exit 1
    sleep 1
done
locked=true
printf '%s\n' "$$" > "$LOCK/pid" || exit 1
stop_watchdogs || exit 1
[ ! -f "$BASE/policy-transaction" ] || {
    printf '%s\n' \
        "refusing package replacement while policy recovery is pending" >&2
    exit 1
}
verify_bootstrap_identity || exit 1

ACTUAL=$(md5sum "$ARCHIVE" | awk '{print $1}') || exit 1
[ "$ACTUAL" = "$EXPECTED" ] || exit 1
[ "$(nvram get astrill_lazy_installed)" = 1 ] || exit 1
[ "$(nvram get astrill_lazy_pkg_md5 | tr 'A-F' 'a-f')" = "$ACTUAL" ] ||
    exit 1
mkdir -p "$STAGE" || exit 1
tar -xzf "$ARCHIVE" -C "$STAGE" || exit 1
SOURCE=$STAGE/astrill-lazy
for name in alctl alapi alpage VERSION; do
    [ -s "$SOURCE/$name" ] || exit 1
    cp "$SOURCE/$name" "$BASE/$name.new.$$" || exit 1
done
[ "$(nvram get astrill_lazy_version)" = \
    "$(cat "$SOURCE/VERSION")" ] || exit 1
chmod 700 "$BASE/alctl.new.$$" "$BASE/alapi.new.$$" "$BASE/alpage.new.$$" ||
    exit 1
chmod 600 "$BASE/VERSION.new.$$" || exit 1
rm -f "$BASE/PACKAGE_MD5"
for name in alctl alapi alpage VERSION; do
    mv "$BASE/$name.new.$$" "$BASE/$name" || exit 1
done

rm -f "$BASE/alhybrid" \
    "$BASE/rules.tsv" "$BASE/rules.previous.tsv" \
    "$BASE/effective.tsv" "$BASE/effective.previous.tsv" \
    "$BASE/runtime-epoch" "$BASE/core-generation" "$BASE/core-recovery" \
    "$BASE/resolved.tsv" "$BASE/unresolved.txt" "$BASE/generated-matches" \
    "$BASE/chain-a.document-hash" "$BASE/chain-b.document-hash"
rm -rf "$BASE/overlays"
mkdir -p "$BASE/overlays" || exit 1
printf '%s\n' "$ACTUAL" > "$BASE/PACKAGE_MD5.new.$$" || exit 1
mv "$BASE/PACKAGE_MD5.new.$$" "$BASE/PACKAGE_MD5" || exit 1

rm -f "$LOCK/pid"
rmdir "$LOCK" || exit 1
locked=false
"$BASE/alctl" start >/dev/null 2>&1
