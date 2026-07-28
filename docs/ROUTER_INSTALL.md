# Router Installation And Rollback

## Prerequisites

- DD-WRT reachable through the `astrill-router` SSH alias
- key-only root SSH
- Astrill already installed and working
- at least about 16 KB of free NVRAM for this release

The deployed SSH key is
`~/.ssh/astrill_lazy_router_ed25519`. Its public fingerprint is:

```text
SHA256:DA01fyS6KuaJp8xyhsleXKKBf5C2iyT9OLLyknirI9w
```

Password authentication remains disabled for SSH. Telnet was intentionally
left available as a recovery path; its credentials are not stored here.

## Install Or Upgrade

```bash
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

Overall status can still be degraded when an enabled VPN policy is fail-closed
because Astrill is disconnected. That is not an installation failure.

In-place upgrades stop the old watchdog, replace the tmpfs package, restore the
same persisted rules, and start a new watchdog process.

The desktop GUI calls a lighter reconciliation path at startup and every 60
seconds. It performs no NVRAM write when the installed version, active jump,
and watchdog are current. It attempts `alctl start` before reinstalling a
degraded current version and can reconstruct a matching stored package without
rewriting it. If that identical package still fails, automatic reconciliation
reports the error instead of repeatedly writing NVRAM; use Install/Upgrade to
request an explicit rewrite.

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
- current and previous compiled rule documents;
- original startup/MyPage values for recovery metadata;
- installation marker.

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
unsets its NVRAM keys, and removes known runtime files. It does not:

- uninstall or restart Astrill;
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
