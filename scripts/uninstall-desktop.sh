#!/bin/sh

set -eu

ROOT=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
rm -f "$HOME/.local/bin/astrill-lazy" "$HOME/.local/bin/astrill-lazy-gui"
rm -f "$HOME/.local/share/applications/io.github.lachlanchen.AstrillLazyRouter.desktop"
rm -f "$HOME/.local/share/metainfo/io.github.lachlanchen.AstrillLazyRouter.metainfo.xml"
printf 'Desktop launchers removed. The source environment remains at %s/.venv\n' "$ROOT"
