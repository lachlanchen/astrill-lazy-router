#!/bin/sh

set -eu

usage() {
    printf '%s\n' \
        "Usage: install-agent.sh BUNDLE_DIR [--enable]" \
        "Installs the portable agent. --enable requires prior explicit enrollment."
}

if [ "$#" -lt 1 ] || [ "$#" -gt 2 ]; then
    usage >&2
    exit 2
fi
BUNDLE=$1
ACTION=${2:-}
[ -d "$BUNDLE" ] || {
    printf 'Agent bundle was not found: %s\n' "$BUNDLE" >&2
    exit 1
}
case $ACTION in ''|--enable) ;; *) usage >&2; exit 2 ;; esac

for name in astrill-lazy-agent.py alhybrid alpage-ui overlay.tsv \
    known_hosts manifest.json SHA256SUMS; do
    [ -f "$BUNDLE/$name" ] || {
        printf 'Agent bundle is missing %s\n' "$name" >&2
        exit 1
    }
done

if command -v sha256sum >/dev/null 2>&1; then
    (cd "$BUNDLE" && sha256sum -c SHA256SUMS)
elif command -v shasum >/dev/null 2>&1; then
    (cd "$BUNDLE" && shasum -a 256 -c SHA256SUMS)
else
    printf '%s\n' "A SHA-256 verification tool is required." >&2
    exit 1
fi

case $(uname -s) in
    Darwin)
        DEST="$HOME/Library/Application Support/Astrill Lazy Router/agent"
        ;;
    Linux)
        CONFIG_HOME=${XDG_CONFIG_HOME:-"$HOME/.config"}
        DEST="$CONFIG_HOME/astrill-lazy/agent"
        ;;
    *)
        printf '%s\n' "The portable agent supports macOS and Linux." >&2
        exit 1
        ;;
esac

mkdir -p "$DEST"
chmod 700 "$DEST"
STAGE="$DEST/.install.$$"
rm -rf "$STAGE"
mkdir -p "$STAGE"
trap 'rm -rf "$STAGE"' EXIT HUP INT TERM
for name in astrill-lazy-agent.py alhybrid alpage-ui overlay.tsv \
    known_hosts manifest.json SHA256SUMS; do
    cp "$BUNDLE/$name" "$STAGE/$name"
done
chmod 700 "$STAGE/astrill-lazy-agent.py" "$STAGE/alhybrid" "$STAGE/alpage-ui"
chmod 600 "$STAGE/overlay.tsv" "$STAGE/known_hosts" \
    "$STAGE/manifest.json" "$STAGE/SHA256SUMS"

# Preserve runtime enrollment only for the exact same immutable deployment.
if [ -f "$DEST/manifest.json" ]; then
    python3 - "$DEST/manifest.json" "$STAGE/manifest.json" <<'PY'
import json
import os
import sys
import tempfile

current_path, candidate_path = sys.argv[1:]
with open(current_path, encoding="utf-8") as handle:
    current = json.load(handle)
with open(candidate_path, encoding="utf-8") as handle:
    candidate = json.load(handle)
immutable = (
    "router_host",
    "router_user",
    "router_port",
    "router_host_key_fingerprint",
    "companion_version",
    "companion_package_md5",
    "helper_md5",
    "controller_id",
    "source",
    "overlay_md5",
    "overlay_sha256",
)
if all(current.get(key) == candidate.get(key) for key in immutable):
    for key in (
        "resolved_source",
        "source_mac",
        "enrolled",
        "overlay_generation",
        "last_runtime_epoch",
        "last_attempt_epoch",
        "last_error",
    ):
        candidate[key] = current.get(key)
descriptor, temporary_name = tempfile.mkstemp(
    prefix=".manifest.",
    suffix=".tmp",
    dir=os.path.dirname(candidate_path),
)
try:
    with os.fdopen(descriptor, "w", encoding="ascii") as handle:
        json.dump(candidate, handle, ensure_ascii=True, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temporary_name, 0o600)
    os.replace(temporary_name, candidate_path)
finally:
    try:
        os.unlink(temporary_name)
    except FileNotFoundError:
        pass
PY
fi

for name in astrill-lazy-agent.py alhybrid alpage-ui overlay.tsv \
    known_hosts manifest.json SHA256SUMS; do
    mv -f "$STAGE/$name" "$DEST/$name"
done
rm -rf "$STAGE"
trap - EXIT HUP INT TERM

PYTHON=$(command -v python3)
"$PYTHON" "$DEST/astrill-lazy-agent.py" \
    --manifest "$DEST/manifest.json" status >/dev/null

if [ "$ACTION" != "--enable" ]; then
    printf 'Installed agent at %s\n' "$DEST"
    printf 'Enroll explicitly, then rerun with --enable.\n'
    exit 0
fi

python3 - "$DEST/manifest.json" <<'PY'
import json
import sys
with open(sys.argv[1], encoding="utf-8") as handle:
    manifest = json.load(handle)
if manifest.get("enrolled") is not True:
    raise SystemExit("Agent is not enrolled; run its enroll command first.")
PY

case $(uname -s) in
    Darwin)
        PLIST="$HOME/Library/LaunchAgents/com.lachlan.astrill-lazy-agent.plist"
        mkdir -p "$HOME/Library/LaunchAgents"
        python3 - "$PLIST" "$PYTHON" "$DEST" <<'PY'
import html
import os
import sys
import tempfile

path, python, root = sys.argv[1:]
values = {
    "python": html.escape(python),
    "agent": html.escape(os.path.join(root, "astrill-lazy-agent.py")),
    "manifest": html.escape(os.path.join(root, "manifest.json")),
    "stdout": html.escape(os.path.join(root, "launchd.stdout.log")),
    "stderr": html.escape(os.path.join(root, "launchd.stderr.log")),
}
document = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>com.lachlan.astrill-lazy-agent</string>
<key>ProgramArguments</key><array>
<string>{python}</string><string>{agent}</string>
<string>--manifest</string><string>{manifest}</string><string>watch</string>
</array>
<key>RunAtLoad</key><true/><key>KeepAlive</key><true/>
<key>ProcessType</key><string>Background</string>
<key>StandardOutPath</key><string>{stdout}</string>
<key>StandardErrorPath</key><string>{stderr}</string>
</dict></plist>
""".format(**values)
descriptor, temporary_name = tempfile.mkstemp(
    prefix=".astrill-lazy-agent.",
    suffix=".plist",
    dir=os.path.dirname(path),
)
try:
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(document)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temporary_name, 0o600)
    os.replace(temporary_name, path)
finally:
    try:
        os.unlink(temporary_name)
    except FileNotFoundError:
        pass
PY
        launchctl bootout "gui/$(id -u)/com.lachlan.astrill-lazy-agent" \
            >/dev/null 2>&1 || true
        launchctl bootstrap "gui/$(id -u)" "$PLIST"
        ;;
    Linux)
        UNIT_DIR="${XDG_CONFIG_HOME:-"$HOME/.config"}/systemd/user"
        UNIT="$UNIT_DIR/astrill-lazy-agent.service"
        mkdir -p "$UNIT_DIR"
        cat > "$UNIT" <<EOF
[Unit]
Description=Astrill Lazy source-bound overlay restore agent
After=network-online.target

[Service]
Type=simple
ExecStart=$PYTHON $DEST/astrill-lazy-agent.py --manifest $DEST/manifest.json watch
Restart=on-failure
RestartSec=15
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=default.target
EOF
        chmod 600 "$UNIT"
        systemctl --user daemon-reload
        systemctl --user enable --now astrill-lazy-agent.service
        ;;
esac
printf 'Enabled agent startup at %s\n' "$DEST"
