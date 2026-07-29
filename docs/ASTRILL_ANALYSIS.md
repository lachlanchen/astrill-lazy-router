# Astrill And DD-WRT Analysis

Analysis was performed read-only after creating the private router snapshot. No
Astrill reinstall, update, uninstall, or credential change was performed.

## Platform Inventory

| Item | Observed value |
| --- | --- |
| Router | Linksys E4200 |
| CPU | Broadcom BCM4716, MIPS 74Kc, 480 MHz |
| Firmware | DD-WRT `v3.0-r62374 mega`, 2025-10-19 |
| Kernel | Linux `4.4.302` |
| RAM | About 58 MB total, about 38 MB available during inventory |
| Root | Read-only SquashFS, about 5.5 MB |
| Persistent writable mount | None available; JFFS is not enabled |
| Astrill runtime | 8 MB tmpfs at `/dev/astrill` |
| Astrill applet | `2.9.52` |
| LAN | `192.168.1.0/24`, router `192.168.1.1` |
| WAN | `vlan2`, upstream gateway `192.168.2.1` |
| Tunnel | `tun0`, gateway `198.18.0.1` when connected |

NVRAM had roughly 32 KB free before the plugin. The compressed router package
uses eight bounded chunks and remains well within that budget.

## Applet Layout

The live applet is a self-extracting shell program. Its small wrapper tails a
gzip payload from itself and executes the private script from tmpfs. The
snapshot contains:

- the self-extracting `astrillvpn` applet;
- the unpacked shell/HTML/JavaScript script;
- `openvpn`, its generated configuration, and logs;
- run, watchdog, status, route, and rule helper files;
- relevant NVRAM, iptables, routing, DNS, and inventory output.

The unpacked script is about 369 KB and 6,467 lines. The desktop parser locates
the server list with bracket-aware scanning and regular expressions. It never
uses JavaScript `eval`. The installed applet exposes 178 server endpoints and
932 endpoint records.

## Native Connection Contract

The applet connection page stores the selected server, node, encoded address,
port, port index, protocol, VPN mode, cipher, MTU, acceleration, disconnect
blocking, favorite cycling, favorites, and startup state in `astrill_*` NVRAM
keys. Its four transport values are OpenVPN UDP, OpenVPN TCP, RouterPro VPN
UDP, and RouterPro VPN TCP. Cipher applies only to OpenVPN; MTU applies only to
UDP.

Available protocols and ports depend on the selected server records. The
desktop therefore derives options from the installed applet instead of keeping
a parallel server table. A favorite record contains only numeric IDs, the
encoded address, validated port or port range, UDP/TCP mode, and VPN mode. No
account data is needed to parse or write these settings.

The native page separates saving a disconnected selection from connecting it.
The desktop retains that distinction and adds a confirmed reconnect operation
for a changed active session. All observed values are mirrored through an
allowlist; the applet executable and generated VPN configuration are not
patched.

## Astrill Routing Behavior

Astrill installs split defaults `0.0.0.0/1` and `128.0.0.0/1` through `tun0`,
leaving the ordinary WAN default in the main table. Its policy implementation
uses:

- table `111` for a device-selection result;
- tables `110` through `114` for site, VPN, ISP, and exception paths;
- `0x1000000/0x3000000` for ISP/direct;
- `0x2000000/0x3000000` for VPN;
- preference `32764` for the direct-mark policy rule in the initial snapshot;
  the connected global-mode state later used preferences `29998` through
  `30001`.

The applet supports site modes for all traffic, selected sites, excluded sites,
and an international-region list. It separately supports all, selected, and
excluded device modes. The desktop maps these to effective outcomes:

| Native mode | Outside list | Inside list |
| --- | --- | --- |
| Global (`0`) | Astrill | Stored list inactive |
| Include (`1`) | Direct | Astrill |
| Exclude (`2`) | Astrill | Direct |

Automatic modes `3` and `4` remain applet-owned unless the user explicitly
changes the flattened website controls. Dirty tracking prevents an unrelated
save from activating or rewriting a stored inactive list.

At inventory time, one device exclusion created a source rule to table `111`.
The applet watchdog had also accumulated duplicate `tun0` MASQUERADE entries.
This project records that condition but does not rewrite Astrill-owned NAT
rules.

Astrill writes the DNS servers pushed by OpenVPN into
`/tmp/resolv.dnsmasq`. A later DD-WRT `stopservice dnsmasq` /
`startservice dnsmasq` cycle regenerates that file from WAN state and does not
rerun Astrill's tunnel-up handler. During the static DHCP migration this
replaced Astrill DNS with the ISP resolver, whose answer for
`www.youtube.com` was incorrect. The active session's own logged pushed DNS
was restored and dnsmasq was sent `SIGHUP`; the tunnel was not reconnected.
The private DHCP apply and restore scripts now preserve this runtime DNS state
around their service restart.

## DD-WRT MyPage Integration

DD-WRT's `do_mypage` implementation interprets the numeric query as an index
into the whitespace-separated `mypage_scripts` NVRAM value and executes that
command into a temporary response file. Query parameters are not a useful API
input to the selected command.

Before installation:

1. `/dev/astrill/astrillvpn` generated Astrill's full page.
2. `/dev/astrill/astrill_getstatus` returned connection state.

After installation:

3. `/tmp/astrill-lazy/alpage` generates the companion policy page.
4. `/tmp/astrill-lazy/alapi` returns policy JSON.

Mutating buttons use authenticated DD-WRT `/apply.cgi` calls with fixed command
forms, matching the technique used by the Astrill page. Free-form website input
is intentionally not accepted through that shell-backed endpoint.

## Installer Review

The user-supplied installer was downloaded into the private snapshot for
analysis only. It is a short bootstrap that embeds account-specific URL values,
downloads a router-specific payload over plaintext HTTP, and executes it.

A read-only attempt to fetch the fresh payload while Astrill was active returned
an "already connected" shell response. The command was not evaluated and no
reinstall occurred. The complete live applet was backed up and unpacked instead.

Security-relevant observations:

- the installer URL itself is a credential and must not be published;
- the bootstrap downloads code over plaintext HTTP;
- the applet's CGI query parser decodes fields and uses shell `eval`;
- the applet is root code loaded into writable tmpfs;
- this build has no `ipset`, TOS matcher, or DSCP matcher suitable for a more
  dynamic classification design.

Astrill Lazy Router does not patch those behaviors. It uses key-only SSH,
strictly validated rule fields, fixed `argv` construction, independent chains,
and an encrypted backup.
