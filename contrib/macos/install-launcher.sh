#!/bin/sh

set -eu

ROOT=$(CDPATH='' cd -- "$(dirname -- "$0")/../.." && pwd)
SOURCE=$ROOT/contrib/macos/open-astrill-lazy.applescript
APPLICATION_DIRECTORY=${HOME}/Applications
APPLICATION="$APPLICATION_DIRECTORY/Astrill Lazy Router.app"

command -v osacompile >/dev/null 2>&1 || {
    printf 'osacompile is required on macOS\n' >&2
    exit 1
}

mkdir -p "$APPLICATION_DIRECTORY"
temporary_directory=$(mktemp -d "${TMPDIR:-/tmp}/astrill-lazy-launcher.XXXXXX")
previous_application="$temporary_directory/previous.app"
trap 'rm -rf "$temporary_directory"' EXIT HUP INT TERM

osacompile -o "$temporary_directory/Astrill Lazy Router.app" "$SOURCE"
if [ -e "$APPLICATION" ]; then
    mv "$APPLICATION" "$previous_application"
fi
mv "$temporary_directory/Astrill Lazy Router.app" "$APPLICATION"

if ! defaults read com.apple.dock persistent-apps 2>/dev/null |
    grep -Fq 'Astrill Lazy Router.app'; then
    application_url=$(printf 'file://%s/' "$APPLICATION" | sed 's/ /%20/g')
    defaults write com.apple.dock persistent-apps -array-add \
        "{\"tile-data\"={\"file-data\"={\"_CFURLString\"=\"$application_url\";\"_CFURLStringType\"=15;};};\"tile-type\"=\"file-tile\";}"
    killall Dock >/dev/null 2>&1 || true
fi

printf 'Installed %s and added it to the Dock.\n' "$APPLICATION"
