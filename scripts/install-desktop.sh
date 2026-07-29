#!/bin/sh

set -eu

ROOT=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
VENV=$ROOT/.venv
LOCAL_BIN=${HOME}/.local/bin
APPLICATIONS=${HOME}/.local/share/applications
METAINFO=${HOME}/.local/share/metainfo

python_bin=${ASTRILL_LAZY_PYTHON:-}
if [ -n "$python_bin" ]; then
    command -v "$python_bin" >/dev/null 2>&1 || {
        printf 'configured Python was not found: %s\n' "$python_bin" >&2
        exit 1
    }
else
    for candidate in python3.12 python3.11 python3; do
        if command -v "$candidate" >/dev/null 2>&1 &&
            "$candidate" -c \
                'import sys; raise SystemExit(sys.version_info < (3, 11))'
        then
            python_bin=$(command -v "$candidate")
            break
        fi
    done
fi
[ -n "$python_bin" ] || {
    printf 'Astrill Lazy Router requires Python 3.11 or newer\n' >&2
    exit 1
}
"$python_bin" -c 'import sys; raise SystemExit(sys.version_info < (3, 11))' || {
    printf 'Astrill Lazy Router requires Python 3.11 or newer: %s\n' \
        "$python_bin" >&2
    exit 1
}
"$python_bin" -m venv --system-site-packages "$VENV"
"$VENV/bin/python" -m pip install "setuptools>=68" wheel
"$VENV/bin/python" -m pip install --no-build-isolation --editable "$ROOT"
mkdir -p "$LOCAL_BIN" "$APPLICATIONS" "$METAINFO"
ln -sfn "$VENV/bin/astrill-lazy" "$LOCAL_BIN/astrill-lazy"
ln -sfn "$VENV/bin/astrill-lazy-gui" "$LOCAL_BIN/astrill-lazy-gui"
case ${ASTRILL_LAZY_ENABLE_AUTOSTART:-0} in
    0) ;;
    1) "$VENV/bin/astrill-lazy" autostart enable >/dev/null ;;
    *)
        printf 'ASTRILL_LAZY_ENABLE_AUTOSTART must be 0 or 1\n' >&2
        exit 2
        ;;
esac
sed "s|^Exec=.*|Exec=$VENV/bin/astrill-lazy-gui|" \
    "$ROOT/data/io.github.lachlanchen.AstrillLazyRouter.desktop" \
    > "$APPLICATIONS/io.github.lachlanchen.AstrillLazyRouter.desktop"
cp "$ROOT/data/io.github.lachlanchen.AstrillLazyRouter.metainfo.xml" "$METAINFO/"
chmod 644 \
    "$APPLICATIONS/io.github.lachlanchen.AstrillLazyRouter.desktop" \
    "$METAINFO/io.github.lachlanchen.AstrillLazyRouter.metainfo.xml"
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$APPLICATIONS" >/dev/null 2>&1 || true
fi
printf 'Installed Astrill Lazy Router at %s\n' "$VENV"
