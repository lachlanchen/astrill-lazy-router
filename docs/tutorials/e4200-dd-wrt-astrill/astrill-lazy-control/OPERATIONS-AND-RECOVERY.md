# Operations And Recovery

## Routine Update

Update the desktop first, inspect, and then explicitly upgrade the companion:

```bash
cd ~/Projects/astrill-lazy
git pull --ff-only
./scripts/install-desktop.sh
astrill-lazy inspect
astrill-lazy access read-write
astrill-lazy install-router
astrill-lazy status
```

Do not run an automatic destructive update at router boot. A matching companion
may reconstruct its verified tmpfs runtime after reboot, but a missing,
outdated, or mismatched package requires confirmation.

## Post-Change Checklist

After changing a connection setting or policy:

```bash
astrill-lazy status
curl -4 --max-time 15 https://ifconfig.co/ip
curl -4 --max-time 15 -I https://www.google.com/
```

Also verify:

- DD-WRT responds at `192.168.1.1`;
- Astrill My Page and the companion policy page render;
- the active endpoint is the one selected;
- policy health and precedence are ready;
- Direct-marked services work while Astrill is connected;
- Astrill-marked services fail closed if the tunnel is intentionally stopped;
- LAN access is unaffected; and
- a router reboot restores only the configured startup behavior.

The companion pages are normally:

```text
http://192.168.1.1/MyPage.asp?3
http://192.168.1.1/MyPage.asp?4
```

## Low-Burden Behavior

The desktop performs a startup check and user-requested refreshes. It does not
continuously poll the router over SSH.

The router-local watchdog runs every 60 seconds. Domain addresses refresh
about every 30 minutes. These checks use the router's local state and do not
run endpoint speed tests automatically.

Endpoint latency tests run only when requested from the desktop and use bounded
concurrency.

## Policy Rollback

To restore the previously retained policy:

```bash
astrill-lazy access read-write
astrill-lazy rollback
astrill-lazy status
```

Rollback is transactional. If the router could not retain a previous document
because of NVRAM reserve, the UI reports that limitation.

## Stop Only The Companion Runtime

If the overlay is unhealthy but SSH still works:

```bash
ssh astrill-router '/tmp/astrill-lazy/alctl stop'
```

This removes companion-owned jumps, chains, marks, and policy lookups from the
current runtime. It does not disconnect or uninstall Astrill.

Traffic then follows the original native Astrill behavior.

## Restore Astrill Only

For complete companion removal:

```bash
astrill-lazy access read-write
astrill-lazy uninstall-router
```

The GUI action is named **Restore Astrill Only**. It removes:

- companion runtime and watchdog;
- companion firewall and routing objects;
- companion NVRAM package and policies;
- companion startup line; and
- companion My Page entries.

It preserves:

- the Astrill applet;
- Astrill endpoint, protocol, and connected state;
- native Astrill website/device/Wi-Fi/VLAN settings;
- DD-WRT SSH configuration; and
- LAN, WAN, DHCP, and Wi-Fi configuration.

After uninstall, run:

```bash
astrill-lazy inspect
```

The desktop can remain installed in native-only read-only mode.

## Astrill Recovery

If native Astrill itself is unhealthy:

1. Stop or remove the companion first.
2. Confirm Direct WAN and DNS work.
3. Compare the current applet version and settings with the private baseline.
4. Disconnect and reconnect Astrill from its own page.
5. Reinstall Astrill only with a fresh private member-zone command when the
   vendor applet is missing or corrupt.
6. Restore only reviewed values; never write a complete old NVRAM dump over a
   different DD-WRT build.

Do not publish the installer URL or plaintext applet snapshot while requesting
support.

## Router Unreachable

Work from the bottom layer upward:

1. Use a wired LAN connection.
2. Disable the workstation Wi-Fi and VPN.
3. Confirm the workstation has a `192.168.1.x` address.
4. Ping `192.168.1.1`.
5. Try the DD-WRT web interface.
6. Try key-only SSH.
7. Use the retained LAN Telnet recovery path if it was intentionally kept.
8. Power-cycle once and wait five minutes.
9. Use the E4200 v1 firmware recovery procedure only if DD-WRT does not boot.

Do not repeatedly power-cycle a router during flash recovery.

## Security Checklist

- Keep web, SSH, and Telnet administration off the WAN.
- Use a dedicated router SSH key and verify the host fingerprint.
- Disable SSH password authentication after key login works.
- Keep any Telnet recovery path LAN-only.
- Store full inspections and LAN device tables with mode `0600`.
- Keep private backup directories with mode `0700`.
- Never commit passwords, MAC inventories, private keys, installer URLs,
  generated OpenVPN files, or plaintext NVRAM snapshots.
- Review `git diff --cached` before every push.

## Optional Isolated noVNC Debugging

On Ubuntu, the repository can start the GUI on a separate loopback-only display:

```bash
cd ~/Projects/astrill-lazy
./scripts/install-novnc-service.sh
systemctl --user enable --now \
  io.github.lachlanchen.AstrillLazyRouter.NoVNC.service
```

Use the URL printed by the installer through the local machine or an SSH
tunnel. Do not expose noVNC directly to the WAN.
