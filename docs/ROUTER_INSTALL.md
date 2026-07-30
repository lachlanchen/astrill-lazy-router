# Router Installation And Rollback

## Prerequisites

- DD-WRT reachable through the `astrill-router` SSH alias
- key-only root SSH
- Astrill already installed and working
- enough projected NVRAM headroom for the encoded package and persistent core
  while retaining the enforced 2 KiB reserve

The GUI can prepare these prerequisites from the Router page: connection
settings default to `192.168.1.1`, `root`, and port `22`; SSH authorization uses
a transient password; and a missing Astrill applet accepts a user-provided,
hash-reviewed installer. The CLI workflow below assumes setup is already
complete.

Installing DD-WRT and the vendor applet on a stock Linksys E4200 v1 is covered
separately in the
[E4200 DD-WRT and Astrill tutorial](tutorials/e4200-dd-wrt-astrill/README.md).
That firmware procedure does not apply to E4200 v2.

Password authentication remains disabled for SSH. Telnet was intentionally
left available as a recovery path; its credentials are not stored here.
Generate and verify a dedicated deployment key rather than copying a
machine-specific fingerprint from documentation. Firmware-specific setup and
the validated `sshd_passwd_auth` NVRAM name are documented in
[Native-only operation](NATIVE_ONLY.md#dd-wrt-key-only-ssh).

## Install Or Upgrade

```bash
astrill-lazy access read-write
astrill-lazy install-router
```

The `0.2.11` installer:

1. creates a deterministic gzip/tar package;
2. base64 encodes it into NVRAM-safe chunks;
3. compares both version and package MD5, so a different same-version package
   is never treated as current;
4. projects encoded package growth, policy-record migration, startup/MyPage
   growth, key-name/terminator overhead, and the 2 KiB reserve before making
   the first NVRAM mutation;
5. snapshots the exact bytes and set/unset presence of every owned package,
   policy, startup, MyPage, and installation value needed to reconstruct the
   previous runtime;
6. acquires the same controller lock as policy writes, compare-and-swap checks
   the complete snapshot, and repeats the live headroom check immediately
   before mutation;
7. normalizes the 6,502-byte bootstrap, stores it as a deterministic
   2,560-byte gzip/base64 payload, and stores the MD5 of that encoded payload
   plus one canonical trailing newline;
8. appends, rather than replaces, the Astrill startup and adds the fixed
   policy/status MyPage commands;
9. commits once, releases the controller lock, reconstructs, and starts the
   runtime;
10. independently decodes the committed core as a clean reboot would, then
    requires the expected version, package MD5, unique installed jump, and
    watchdog before reporting success; and
11. restores and commits the snapshot in a guarded recovery transaction, then
    reconstructs the locally validated captured package through the current
    serialized recovery logic, if bootstrap or post-install verification
    fails. Recovery refuses to overwrite a newer policy/package/startup state.

On the documented E4200 preflight snapshot, the measured live projection for
this build starts with 6,284 NVRAM bytes free. The complete package,
compressed-bootstrap, hook, metadata, and persistent-rule migration adds 2,772
bytes, leaving 3,512 bytes free—1,464 bytes above the enforced 2,048-byte
reserve. This is a snapshot measurement, not a durable capacity guarantee. The
installer recomputes the projection under the controller lock immediately
before mutation.

Failed-upgrade rollback does not execute the captured old bootstrap. After
restoring the exact NVRAM snapshot, the desktop-shipped current bootstrap runs
in serialized recovery mode, bound to the expected restored version, package
MD5, and canonical old stored-bootstrap MD5. It rechecks those identities
before and under the shared lock, verifies and stages the captured package,
then starts its restored `alctl`. A legacy runtime whose status lacks
`package_md5` is accepted only after every restored runtime file matches the
MD5 derived from the validated captured archive.

A disconnected companion is ready when its Direct table, table `212` blackhole
default, VPN-mark forwarding guard, active policy jump, and watchdog are all
verified. Native and companion RPDB preferences are intentionally absent in
that safe down state. Degradation means one of those protections could not be
verified; disconnection alone is not an installation failure.

In-place upgrades stop the old watchdog, extract into a private staging
directory, publish the verified tmpfs package files through atomic renames,
invalidate package-bound overlays and helper state, restore the same persistent
core, and start a new watchdog process. The running `PACKAGE_MD5` marker is
published only after archive verification and runtime replacement. The RAM-only
`alhybrid` extension is uploaded and digest-verified by a confirmed desktop
controller under the shared controller lock, not stored in NVRAM. Immediately
after reboot the base companion therefore activates the verified core by
itself; policy transactions become available when the desktop reconnects and
supplies the matching extension.

The `clients --json` operation is read-only. It merges DHCP leases, static
reservations, and complete ARP neighbors on the configured LAN bridge,
deduplicates by MAC address, and excludes WAN-interface neighbors.

The desktop GUI calls a lighter reconciliation path once at startup. It does
not repeat that check through background SSH polling. Manual Refresh retries
the same safe path, including when desktop login startup occurred before
DD-WRT finished booting. The check performs no NVRAM write when version,
package and stored-bootstrap-payload digests, stored chunk/bootstrap
integrity, persistent hooks, running package marker, active jump, and watchdog
are current. It attempts `alctl start` before reinstalling a degraded current
version and can reconstruct that exact verified stored package without
rewriting it. If that identical package still fails, reconciliation reports
the error instead of repeatedly writing NVRAM; use Install/Upgrade to request
an explicit rewrite.

Removing the desktop timer does not remove the installed companion's own
router-local recovery. Its watchdog still runs every 60 seconds on DD-WRT. If
only the persistent core is active, its domains may refresh every 30 watchdog
cycles (approximately 30 minutes). An active RAM overlay is not rebuilt on that
cycle; it is loaded only through an explicit action, one-shot startup/network
restoration, or a manual restore/reload. None of these rules introduces a
desktop SSH polling loop.

Current integration values:

```text
runtime: /tmp/astrill-lazy
policy page: http://192.168.1.1/MyPage.asp?3
status page: http://192.168.1.1/MyPage.asp?4
```

The router web password is independent of the root Telnet account and was not
changed.

## Persistent Data

The plugin owns only NVRAM keys beginning with `astrill_lazy_`:

- package chunks, count, MD5, and version;
- deterministic gzip/base64 bootstrap payload and its canonical payload MD5;
- the current compiled persistent core and, when NVRAM reserve permits, its
  previous generation, stored as gzip/base64 when smaller than plain TSV;
- original startup/MyPage values for recovery metadata;
- installation marker.

Upgrade migrates legacy plain policy values before writing larger package
chunks. This avoids transient NVRAM exhaustion, and the controller continues
to read the legacy keys as a recovery fallback.

Controller overlays are deliberately absent from this list. Their validated
documents and metadata live under `/tmp/astrill-lazy/overlays`, and the
deterministically composed effective policy lives under
`/tmp/astrill-lazy/effective.tsv`. Overlay put/remove and one-shot restore
operations perform no `nvram set` or `nvram commit`.

`rc_startup` still runs the original `astrill_bootstrap` first. The plugin
captures its own stored encoded bootstrap payload and digest once, rejects an
invalid digest or blank payload, reconstructs the canonical trailing newline
for MD5, and verifies it. It then decodes that same captured payload through
`uudecode` and gzip, rejects an empty decoded script, and executes it. The
bootstrap receives the expected payload digest, verifies the stored canonical
payload before and again after acquiring the controller lock, and separately
verifies the reconstructed package archive. It never verifies one NVRAM value
and decodes or executes a second read. `mypage_scripts` retains Astrill as
pages 1 and 2.

## Operations

```bash
astrill-lazy status
astrill-lazy apply
astrill-lazy refresh
astrill-lazy rollback
ssh astrill-router '/tmp/astrill-lazy/alctl logs'
```

The desktop uses generation-guarded operations for hybrid storage:

```text
alctl core-apply EXPECTED_VERSION EXPECTED_PACKAGE_MD5 EXPECTED_HELPER_MD5 \
  EXPECTED_GENERATION FILE|-
alctl core-rollback EXPECTED_VERSION EXPECTED_PACKAGE_MD5 EXPECTED_HELPER_MD5 \
  EXPECTED_GENERATION
alctl overlay-put EXPECTED_VERSION EXPECTED_PACKAGE_MD5 EXPECTED_HELPER_MD5 \
  OWNER EXPECTED_GENERATION SOURCE_OR_AUTO \
  EXPECTED_SOURCE_OR_DASH EXPECTED_MAC_OR_DASH FILE|-
alctl overlay-remove EXPECTED_VERSION EXPECTED_PACKAGE_MD5 EXPECTED_HELPER_MD5 \
  OWNER EXPECTED_GENERATION
alctl toggle-origin EXPECTED_VERSION EXPECTED_PACKAGE_MD5 EXPECTED_HELPER_MD5 ID
alctl route-origin EXPECTED_VERSION EXPECTED_PACKAGE_MD5 EXPECTED_HELPER_MD5 \
  ID direct|vpn
alctl effective-status --json
```

`apply` and `rollback` remain explicit administrator compatibility commands
without generation compare-and-swap. Normal GUI core changes use the guarded
forms. Their raw identity-bound syntax is:

```text
alctl apply EXPECTED_VERSION EXPECTED_PACKAGE_MD5 EXPECTED_HELPER_MD5 FILE|-
alctl rollback EXPECTED_VERSION EXPECTED_PACKAGE_MD5 EXPECTED_HELPER_MD5 [--json]
```

Before either form, the desktop atomically stages the exact helper from its
bundle. After acquiring the shared lock, `alctl` requires the supplied
version/package MD5 to match both running markers and NVRAM, and requires the
supplied helper MD5 to match the executable it will source. The source value
`auto` derives the SSH peer's LAN address and, when available, its bridge ARP
MAC. An explicitly entered host or CIDR is the advanced path; a host also
adopts its observed bridge MAC when available.
For a reboot restore, the desktop supplies the last trusted resolved source and
MAC in the two expected fields. DD-WRT rejects a DHCP/ARP reassignment before
any candidate chain is activated. First loads and explicitly reviewed rebinds
use `-` preconditions.

`alctl stop` removes only this plugin's jump, A/B chains, preferences, and
tables. It does not stop Astrill.

## Uninstall

```bash
astrill-lazy uninstall-router
```

Uninstall first stops the plugin through the controller lock, captures the
exact NVRAM bytes and every numbered package chunk, then reacquires that shared
lock. It compare-and-swap checks the snapshot and a quiescent runtime before
mutation; concurrent package, policy, startup, or MyPage changes are refused.
The locked transaction removes its exact startup line and MyPage commands,
unsets its NVRAM keys, commits and verifies the complete uninstalled state,
removes known runtime files, and audits the firewall, policy rules, tables,
watchdog, hooks, pages, chunks, and runtime residue. If mutation or audit
fails, it restores and commits the exact NVRAM snapshot in the same session.

The GUI labels this operation `Restore Astrill Only` and disables automatic
reinstallation only after the audit and native Astrill readback pass. It does
not:

- uninstall or restart Astrill;
- disconnect Astrill or change its endpoint or protocol;
- disable SSH;
- remove the authorized SSH key;
- change DD-WRT web or Telnet credentials.

## Recovery

If the policy runtime is unhealthy:

```bash
ssh astrill-router '/tmp/astrill-lazy/alctl stop'
```

Traffic then returns to Astrill's original behavior. If SSH is unavailable, use
the retained Telnet recovery path and run the same command. The complete
pre-plugin integration values are in the encrypted backup.

The bootstrap has been invoked repeatedly, upgrade recovery is verified, and
the plugin reconstructed successfully after a physical router reboot.
Astrill's existing `astrill_autostart=0` setting was deliberately preserved;
the plugin does not decide whether the upstream VPN should connect at boot.

For the hybrid acceptance sequence, reboot with Astrill disconnected and
confirm the core is active before any GUI starts. Then start one paired GUI,
confirm exactly one missing-overlay restoration for the new runtime epoch,
verify the reported owner/source/MAC/generation/hash, and compare the complete
NVRAM digest before and after the overlay operation. A failed admission or
restore must retain the prior core/effective chain and must not be retried in a
tight loop. For a large overlay, also verify bounded eight-way/five-second DNS
prefetch, test-then-commit of one `iptables-restore --noflush` document into the
inactive chain, and exact post-commit topology/rule-count readback before the
A/B jump changes.
