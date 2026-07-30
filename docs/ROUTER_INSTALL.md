# Router Installation And Rollback

## Prerequisites

- DD-WRT reachable through the `astrill-router` SSH alias
- key-only root SSH
- Astrill already installed and working
- at least about 16 KB of free NVRAM for this release

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

The installer:

1. creates a deterministic gzip/tar package;
2. base64 encodes it into NVRAM-safe chunks;
3. stores chunk count, MD5, version, and bootstrap;
4. preserves the prior startup and MyPage values;
5. appends, rather than replaces, the Astrill startup;
6. adds policy and status MyPage commands;
7. commits once;
8. reconstructs and starts the runtime;
9. requires the expected version, installed jump, and watchdog before reporting
   success.

A disconnected companion is ready when its Direct table, table `212` blackhole
default, VPN-mark forwarding guard, active policy jump, and watchdog are all
verified. Native and companion RPDB preferences are intentionally absent in
that safe down state. Degradation means one of those protections could not be
verified; disconnection alone is not an installation failure.

In-place upgrades stop the old watchdog, replace the tmpfs package, restore the
same persisted rules, and start a new watchdog process.

The `clients --json` operation is read-only. It merges DHCP leases, static
reservations, and complete ARP neighbors on the configured LAN bridge,
deduplicates by MAC address, and excludes WAN-interface neighbors.

The desktop GUI calls a lighter reconciliation path once at startup. It does
not repeat that check through background SSH polling. Manual Refresh retries
the same safe path, including when desktop login startup occurred before
DD-WRT finished booting. The check performs no NVRAM write when the installed
version, active jump, and watchdog are current. It attempts `alctl start`
before reinstalling a degraded current version and can reconstruct a matching
stored package without rewriting it. If that identical package still fails,
reconciliation reports the error instead of repeatedly writing NVRAM; use
Install/Upgrade to request an explicit rewrite.

Removing the desktop timer does not remove the installed companion's own
router-local recovery. Its watchdog still runs every 60 seconds on DD-WRT, and
its domain rules still refresh locally every 30 watchdog cycles (approximately
30 minutes). Neither operation opens a desktop SSH session.

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
- bootstrap script;
- the current compiled rule document and, when NVRAM reserve permits, the
  previous document, stored as gzip/base64 when that is smaller than plain TSV;
- original startup/MyPage values for recovery metadata;
- installation marker.

Upgrade migrates legacy plain policy values before writing larger package
chunks. This avoids transient NVRAM exhaustion, and the controller continues
to read the legacy keys as a recovery fallback.

`rc_startup` still runs the original `astrill_bootstrap` first. The plugin then
pipes its own stored bootstrap to `sh`. `mypage_scripts` retains Astrill as
pages 1 and 2.

## Operations

```bash
astrill-lazy status
astrill-lazy apply
astrill-lazy refresh
astrill-lazy rollback
ssh astrill-router '/tmp/astrill-lazy/alctl logs'
```

`alctl stop` removes only this plugin's jump, A/B chains, preferences, and
tables. It does not stop Astrill.

## Uninstall

```bash
astrill-lazy uninstall-router
```

Uninstall stops the plugin, removes its exact startup line and MyPage commands,
unsets its NVRAM keys, removes known runtime files, and audits the firewall,
policy rules, watchdogs, startup hooks, pages, and runtime directory. The GUI
labels this operation `Restore Astrill Only` and disables automatic
reinstallation only after the audit passes. It does not:

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
