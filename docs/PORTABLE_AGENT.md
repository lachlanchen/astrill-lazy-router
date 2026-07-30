# Portable Restore Agent

## Role

The portable agent keeps one computer's source-bound RAM overlay available
after a router reboot without requiring the GTK or Qt GUI to be open. It runs
on macOS and Linux with Python 3.9 or newer, the standard library, and OpenSSH.

The router still has one physical Astrill tunnel and one active endpoint. The
agent does not create additional tunnels or consume another Astrill device
slot. It restores classification policy for the computer that enrolled it.

## Layer Split

`astrill-lazy agent plan` derives two bounded layers from enabled local rules:

| Layer | Contents | Lifetime |
| --- | --- | --- |
| Persistent core | Device identities, namespace-backed process identities, and the minimum UU Remote, WeChat, Taobao, Meituan, and Nutstore bypasses | Router NVRAM and boot |
| Computer overlay | Remaining destination service, company, domain, and network decisions | Router RAM, source/MAC scoped |
| Undeployed | Disabled rules and process rules without a router-visible namespace identity | Computer only |

The core works while every computer is off. Each computer gets a different
controller ID and can restore only its own overlay. The router composes all
owners with the core and rejects origin collisions, stale generations,
oversized documents, and changed source/MAC bindings.

## Build

The router companion must already be installed at the same exact version and
package digest as the local application. Inspect and confirm the router host
key fingerprint before building:

```bash
astrill-lazy agent plan
astrill-lazy agent build ~/Downloads/astrill-lazy-agent \
  --host ROUTER_ADDRESS \
  --user root \
  --port 22 \
  --identity-file '~/.ssh/astrill_lazy_router_ed25519' \
  --host-fingerprint SHA256:REVIEWED_FINGERPRINT
```

Building is read-only. It scans the live SSH host key, requires the supplied
fingerprint to match, and creates:

```text
astrill-lazy-agent.py
install-agent.sh
uninstall-agent.sh
alhybrid
alpage-ui
overlay.tsv
known_hosts
manifest.json
SHA256SUMS
```

The private key is never copied into the bundle. `identity_file` names the
private key path that must exist on the target computer with mode `0600`.
Bundle provenance omits the source URL and local path; it retains only policy
ID, version, and SHA-256.

## First Enrollment

Copy the complete bundle to the target computer. The target must already have
its own authorized router key at the path recorded in `manifest.json`.

Install without enabling startup:

```bash
sh BUNDLE/install-agent.sh BUNDLE
```

Enroll explicitly from the target computer. This first mutation lets the
router resolve the actual SSH source address and bridge MAC:

Linux:

```bash
python3 ~/.config/astrill-lazy/agent/astrill-lazy-agent.py \
  --manifest ~/.config/astrill-lazy/agent/manifest.json enroll
```

macOS:

```bash
python3 "$HOME/Library/Application Support/Astrill Lazy Router/agent/astrill-lazy-agent.py" \
  --manifest "$HOME/Library/Application Support/Astrill Lazy Router/agent/manifest.json" \
  enroll
```

Enable login startup only after successful readback:

```bash
sh BUNDLE/install-agent.sh BUNDLE --enable
```

Linux installs a user `systemd` service. macOS installs a user LaunchAgent.
Neither requires a stored router password.

## Recovery Behavior

The agent verifies all of these before an overlay write:

```text
local asset SHA-256/MD5 and private-key permissions
live SSH host-key fingerprint and strict known_hosts
running and stored companion version/package identity
ready policy runtime and verified Astrill precedence
controller owner, overlay hash, source address, and MAC binding
router runtime epoch
```

It records an attempt before the long router transaction and attempts at most
once per router runtime unless an operator uses `--force`. A matching live
overlay causes no write. Missing or changed identity fails closed and does not
replace another overlay.

The service checks at startup and every 15 minutes by default, with up to
30 seconds of jitter. An unreachable router is retried every 30 seconds. If
DD-WRT answers while the companion is still reconciling its boot runtime, the
agent also retries every 30 seconds for at most ten attempts, then returns to
the 15-minute interval. Ordinary checks are read-only SSH status calls. The
DNS/firewall overlay build runs only when a new router runtime lacks the
enrolled layer. This avoids a high-frequency polling burden.

## Status, Upgrade, And Removal

Read status without mutation:

```bash
python3 PATH/astrill-lazy-agent.py --manifest PATH/manifest.json status
```

Request one conservative restore:

```bash
python3 PATH/astrill-lazy-agent.py --manifest PATH/manifest.json restore
```

Rebuild and rerun the installer for an upgrade. Enrollment is retained only
when router endpoint, fingerprint, companion identity, controller ID, source,
helper, and overlay identity match exactly. A changed deployment remains
installed but disabled until explicit enrollment.

Disable startup while retaining enrollment:

```bash
sh BUNDLE/uninstall-agent.sh
```

Remove startup and local agent state:

```bash
sh BUNDLE/uninstall-agent.sh --purge
```

Removing the local service does not remove the router's persistent core.
Owner-overlay removal remains a separate confirmed controller action.
