# Astrill Lazy Router

Astrill Lazy Router is a native Ubuntu controller and DD-WRT companion for
placing traffic beside, or through, the active Astrill tunnel. It adds a policy
layer without modifying Astrill's applet files or mark space.

The current deployment provides:

- direct or Astrill routing by service, website, IPv4 network, device, protocol,
  and port;
- isolated per-application identities on Ubuntu using macvlan network
  namespaces;
- a native GTK 4 and Libadwaita control application;
- a companion page at DD-WRT `/MyPage.asp?3`;
- an extension catalog with 44 common services and 14 region groups;
- guarded A/B policy activation, rollback, watchdog recovery, and fail-closed
  VPN routes;
- discovery and switching of all 178 locations in the installed Astrill
  applet;
- an encrypted, round-trip-verified recovery backup.

The installed default policy is `UU Remote -> Direct`.

## Run

The user-local desktop package is installed at
`/home/lachlan/Projects/astrill-lazy/.venv`.

```bash
astrill-lazy-gui
```

Useful CLI commands:

```bash
astrill-lazy status
astrill-lazy apply
astrill-lazy servers
astrill-lazy refresh
astrill-lazy rollback
astrill-lazy install-router
```

The SSH target defaults to the `astrill-router` host alias. The desktop
configuration is stored with mode `0600` at
`~/.config/astrill-lazy/config.json`.

## Country Model

This router has one Astrill tunnel. A rule can select:

- `Direct`, which uses the WAN gateway; or
- `Astrill`, which uses the one currently active Astrill location.

The GUI stores a preferred region for each VPN policy and reports incompatible
simultaneous preferences. It does not pretend that one tunnel can provide
several countries at once. Use the Locations view to switch the shared tunnel.
A future multi-tunnel provider can extend this model without changing the rule
schema.

## Current Deployment

- Router: Linksys E4200
- Firmware: DD-WRT `v3.0-r62374 mega`
- Astrill applet: `2.9.52`
- Router runtime: `/tmp/astrill-lazy`
- DD-WRT pages: policy `3`, status API `4`
- Router plugin: `0.1.0`, healthy
- Active example: `uuyc.163.com` resolved and marked direct

## Safety

Raw applet files, account state, generated OpenVPN configuration, and installer
credentials are ignored by Git. The complete snapshot is committed only as CMS
ciphertext under `backups/`; its private decryption key remains outside this
repository.

## Documentation

- [Architecture](docs/ARCHITECTURE.md)
- [Astrill and DD-WRT analysis](docs/ASTRILL_ANALYSIS.md)
- [Desktop application](docs/DESKTOP_APP.md)
- [Router installation and rollback](docs/ROUTER_INSTALL.md)
- [Rule model](docs/RULE_MODEL.md)
- [Extensions](docs/EXTENSIONS.md)
- [Backup and restore](docs/BACKUP_RESTORE.md)
- [Security](docs/SECURITY.md)
- [Testing and operations](docs/TESTING.md)
- [Changelog](CHANGELOG.md)
