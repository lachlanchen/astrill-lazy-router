#!/bin/sh

set -eu

ROOT=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
VENV=$ROOT/.venv
LOCAL_BIN=${HOME}/.local/bin
APPLICATIONS=${HOME}/.local/share/applications
METAINFO=${HOME}/.local/share/metainfo

python3 -m venv --system-site-packages "$VENV"
"$VENV/bin/python" -m pip install "setuptools>=68" wheel
"$VENV/bin/python" -m pip install --no-build-isolation --editable "$ROOT"
mkdir -p "$LOCAL_BIN" "$APPLICATIONS" "$METAINFO"
ln -sfn "$VENV/bin/astrill-lazy" "$LOCAL_BIN/astrill-lazy"
ln -sfn "$VENV/bin/astrill-lazy-gui" "$LOCAL_BIN/astrill-lazy-gui"
"$VENV/bin/astrill-lazy" autostart enable >/dev/null
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
