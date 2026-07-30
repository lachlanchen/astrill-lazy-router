# Security

## Trust Boundaries

- The router controller runs as DD-WRT root.
- The desktop app runs as the logged-in Ubuntu or Windows user.
- Only the application namespace helper runs through Polkit.
- Astrill remains an independent privileged applet.
- Catalog extensions are data-only and are not executed.

## Router Access

The router uses key-only SSH through the `astrill-router` alias. Password SSH is
disabled. The private key remains under `~/.ssh`; only its public key is stored
in DD-WRT NVRAM.

Fresh desktop profiles can instead address `192.168.1.1` directly. The GUI
stores the host, user, port, and identity path, but never the router password.
Authorize Key passes a transient password to `sshpass` through its environment,
verifies the generated Ed25519 key, and only then disables SSH password login.
The Windows controller does not use `sshpass`. Guided onboarding shows the
candidate SHA-256 SSH fingerprint before any credential prompt, pins the
confirmed key in the application configuration directory, and uses a transient
LAN Telnet password only to append the generated public key. Telnet is
unencrypted and must be used only on a trusted local network. The password is
never written to disk, logs, process arguments, or environment variables.
Normal Windows commands then use the pinned file, `BatchMode=yes`, and
`StrictHostKeyChecking=yes`.

The native Windows application launches its noninteractive `ssh.exe`,
`ssh-keyscan.exe`, and `ssh-keygen.exe` helpers without a console window. The
separate interactive SSH setup action remains visible by design and keeps
`StrictHostKeyChecking=ask`.

Telnet remains enabled as a deliberate recovery mechanism. This is less secure
than SSH on an untrusted LAN. Disable it only after confirming another console
or recovery path, and do not couple that change to a policy upgrade.

The DD-WRT web account is separate from Telnet root access. Its password was not
guessed, reset, or stored by this project.

Ubuntu desktop login startup is a mode `0644` freedesktop entry in the current
user's configuration when explicitly enabled. Windows native installation
creates a current-user Startup-folder shortcut without administrator access;
it points only to the installed executable and is removed by uninstall when
its target matches that executable. Companion reconciliation uses the existing
key-only SSH alias and stores no router or Ubuntu password. It may reconstruct
an already-confirmed, fingerprint-matched package from router NVRAM after
reboot, but it never silently installs or persistently rewrites a missing,
stale, or inconsistent package.

The Windows hybrid deployment manifest contains no secret. It records a
random controller ID, confirmed router host-key fingerprint, companion
version and exact package MD5, expected core and owner-overlay generations and
document MD5 hashes, the allowed source address/MAC binding, the most recently
observed runtime epoch, and the last one-shot restore attempt. A router,
version, package, source, MAC, hash, or generation mismatch blocks silent
replacement and requires explicit review. The SSH private key remains the
authentication boundary.

A fresh configuration is native-only and read-only. The GUI and CLI block
policy apply/rollback/refresh, endpoint switching, connection changes, and
native-setting writes until the local operator enables write access. The GUI's
separately confirmed companion onboarding action can enable write access after
a successful install. The guard prevents accidents; it is not a security
boundary against someone who controls the user account or invokes SSH directly.

The native settings mirror uses an explicit safe-key allowlist. Astrill
account values, router passwords, installer URLs, and generated OpenVPN
credentials are neither requested nor returned. Writes use normalized values,
commit once, and read every changed key back for exact verification. Endpoint
selections can only be constructed from validated records parsed from the
installed applet. A failed native reconnect restores the prior allowlisted
values and attempts to recover the prior active session.

Windows favorite membership changes are narrower writes to
`astrill_favlist`. The operator must confirm each add or remove, and the
controller obtains a fresh native-settings snapshot first. The router compares
that expected favorite list with the current NVRAM value before setting it,
commits once, and the controller verifies an exact readback. Concurrent or
malformed values stop the write. The action is disabled while the native
Astrill page has an unsaved draft and does not install the companion,
reconnect the tunnel, or start background polling.

Astrill installer input is transient. The GUI uses a redacted `xxx/xxx`
template, limits downloaded or pasted shell text to 512 KiB, displays its
SHA-256 digest, and requires a second confirmation before root execution. A
repository test rejects token-bearing Astrill installer paths.

## Input Handling

Persistent-core TSV input is limited to 6,144 bytes and exactly ten fields per
rule. Owner-overlay input uses the same ten-field destination schema with a
separate 32 KiB/320-row admission boundary; its source condition is supplied
by the owner chain rather than a second TSV selector. The controller validates:

- small ASCII IDs;
- IPv4 addresses and prefixes;
- DNS names;
- target, match kind, protocol, priority, and enabled enums;
- bounded destination ports and ranges;
- URL-encoded display labels.

An owner overlay rejects `device` rows because combining an embedded device
selector with the owner source chain would be ambiguous. The router also
validates stable controller IDs, expected generations, maximum overlay count,
total effective bytes/rows, generated firewall matches, reclaimable memory,
and build duration. Automatic source binding resolves the SSH peer to a `/32`
and records its current bridge-neighbor MAC. Reboot restoration sends the
previously trusted resolved source and MAC as router-enforced preconditions, so
a DHCP/ARP reassignment is rejected before chain construction or activation.

Enabled domains are deduplicated and resolved in a bounded pool of eight jobs
with a five-second deadline per lookup. A failed fresh lookup may use only the
prior validated address cache. Firewall tokens are serialized only from the
validated enums, IPv4 values, ports, source, and MAC fields; rule content is
never passed to `eval`, `sh -c`, or command substitution as executable text.

The batched restore document names only the fixed inactive A/B chain and never
edits `PREROUTING`. The helper checks exact chain references, reclaimable
memory, and the transaction deadline before a dry run with
`iptables-restore --noflush --test`, repeats the guards before the single
`--noflush` commit, and requires exact rule-count and topology readback before
publishing the new jump.

The DD-WRT page permits only fixed command names and validated IDs. Arbitrary
website text is edited over SSH in the native app, not interpolated into
`apply.cgi`.

## Routing Safety

- Private/local destinations return before policy marking.
- Direct and VPN marks use bits outside Astrill's mask.
- The plugin uses separate policy tables and does not flush Astrill chains.
- Policy-rule preferences are allocated and verified after Astrill's native
  rules settle. Cleanup normally uses the recorded preferences; if that record
  is missing, only exact companion mark, mask, and table signatures are
  recovered.
- VPN table `212` retains a lower-priority blackhole fallback while `tun0` is
  active and the blackhole becomes its only default while disconnected.
- An unmanaged native undercut stays fail-closed, degraded, and rebase-required
  until observed down or explicitly reconnected; automatic repair does not
  ratchet the owned preferences downward.
- A/B activation leaves the previous chain live until the new chain is ready.
- Persistent-core writes use generation compare-and-swap, verified encoded
  NVRAM readback, and in-session rollback if commit or activation fails.
- Companion installation and removal share the controller lock used by policy
  writes. Installation compare-and-swap checks its complete exact-byte NVRAM
  snapshot, repeats the live headroom projection, and refuses rollback over
  any newer package, policy, startup, or MyPage state. Post-install
  verification independently decodes the persisted current core instead of
  trusting the already-running tmpfs copy. Failed-upgrade recovery validates
  the captured package, restores exact NVRAM, and reconstructs the old runtime
  with the desktop-shipped current bootstrap in identity-bound serialized
  recovery mode. Legacy status without a package marker requires exact
  restored-runtime file MD5s. Removal audits the stopped runtime and owned
  hooks before reporting native-only success.
- The normalized bootstrap is deterministic-gzip/base64 encoded before NVRAM
  storage. The stored MD5 covers the encoded payload plus one canonical
  trailing newline. The startup launcher hashes one captured payload, decodes
  that same payload, and executes the derived script; bootstrap revalidates
  the stored payload before and under the shared lock, verifies the package
  archive, stages extraction away from the live runtime, and publishes the
  running package marker only after replacement.
- RAM overlays are controller-owned, source/MAC-scoped, generation guarded,
  deterministically composed after the global core, and never committed to
  NVRAM. One owner cannot replace another owner's file. They are rebuilt only
  by explicit/one-shot restoration paths, not by a periodic watchdog cycle.
- Every core or overlay mutation is bound to expected version, base-package
  MD5, and RAM-helper MD5. `alctl` verifies the running and stored base identity
  plus the helper executable under the controller lock before sourcing helper
  code or changing policy state.
- The watchdog checks applet/firewall restarts every 60 seconds. Its 30-cycle
  DNS rebuild is core-only and runs only while no RAM overlay is active.
- `alctl stop` removes only plugin-owned objects.
- Automatic reconciliation checks version and runtime markers before any
  install, verifies deterministic package/stored-bootstrap-payload
  fingerprints, stored integrity, and exact persistent hooks before recovery,
  so a healthy or identically broken companion does not cause repeated NVRAM
  writes.
- Route detection is read-only and requires the existing tunnel to be up.
  Recommendations are displayed before a separate apply action; incomplete
  comparisons retain the current route.

Domain matching still depends on known, resolved service domains. Unknown CDN,
dynamic ICE relay, and peer-to-peer destinations follow the router's ordinary
Astrill behavior rather than being magically attributed to a company. A
source-device rule or process-aware device-local backend is safer than adding
broad hosting-provider CIDRs.

## Application Helper

`astrill-lazy-netns` and the boot profile runner require root and reject:

- malformed profile and interface names;
- missing parent links;
- non-desktop UIDs;
- invalid session PIDs;
- relative or non-executable application paths;
- invalid DHCP addresses and unsupported netmasks.

It launches the app through `runuser` after entering the namespace, preserving
only required session variables. Arguments remain an `argv` list and are never
evaluated by a shell.

Boot profiles are read from root-owned files under
`/etc/astrill-lazy/profiles`. The systemd template passes those fixed values
as arguments to the same validated helper. The runner does not accept commands
from the desktop session; it only waits for that user's GNOME session bus.

macOS application flow reports accept an exact IPv4 source, TCP or UDP, at
most 15 source ports, a Direct/Astrill target, and a constrained ID. The router
retains at most 16 such rows and 1,024 bytes under `/tmp`; they are not written
to NVRAM. Private and multicast destinations return before a flow can set a
route mark. The provided UU reporter inspects only processes inside the signed
application's bundle path and uses key-only SSH with strict host-key checking.

For a shared multi-user installation, install the helper root-owned under
`/usr/local/libexec` and use a narrow Polkit policy. The source deployment
executes the checked-out helper through the standard `pkexec` authentication
policy.

## Secret Handling

Git ignores:

- `.private-backups`;
- private keys and PEM files;
- OpenVPN files;
- logs and environment files;
- virtual environments and build output.

The repository contains a public encryption certificate and encrypted CMS
backup only. The decryption key, SSH key, Astrill account values, generated VPN
configuration, and account-specific installer URL remain outside Git.

Before release, the publishable tree is scanned for the known installer account
and token values.

## Upstream Risk

The backed-up Astrill installer and applet contain patterns that would not be
chosen for new privileged software, including plaintext HTTP bootstrap and
shell `eval` in request handling. Those observations are documented so future
upgrades can be reviewed. This project does not redistribute the plaintext
applet or claim to correct Astrill's upstream trust model.
