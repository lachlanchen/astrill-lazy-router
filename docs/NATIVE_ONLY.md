# Native-Only And Read-Only Routers

## When To Use This Mode

A DD-WRT router can already provide useful selective routing through Astrill's
own website, device, Wi-Fi, and VLAN modes. Astrill Lazy Router does not need to
install its companion merely to inspect that state.

Use native-only, read-only mode when:

- the router already routes the required websites and devices correctly;
- the router has not been prepared for the companion;
- the hardware has limited NVRAM;
- a second or unfamiliar router should be audited before any change; or
- the desktop application is being evaluated without a deployment decision.

A fresh desktop configuration starts in this mode. It has no seeded policy and
does not itself change platform login-startup integration, install, repair,
reconnect, commit, or otherwise modify the router. Ubuntu startup remains
opt-in; the native Windows installer manages its per-user Startup shortcut
independently of router access mode.

Native-only startup performs read-only SSH, Astrill, and companion presence
checks. For a previously confirmed companion, Windows startup can reconstruct
the current validated runtime from its retained NVRAM package after a router
reboot. If the router retained neither its markers nor runtime, the desktop
falls back to native-only mode. Network unavailability never triggers that
fallback, and a missing, stale, or inconsistent package is never silently
installed or rewritten. When installation is required, the GUI asks for
explicit approval. Approving companion installation changes the local
configuration to read-write companion mode; dismissing the dialog leaves
native-only mode unchanged.

## Safe Inspection

Configure a key-only SSH alias named `astrill-router`, then run:

```bash
astrill-lazy access status
astrill-lazy inspect
astrill-lazy status
astrill-lazy servers
```

`inspect` reads native Astrill state, the optional companion markers, endpoint
count, DHCP leases, static reservations, and LAN ARP neighbors. Its default
output reports counts rather than personal website/device records. Use
`astrill-lazy inspect --full` only when the detailed local inventory is
required.

The native client reader does not require `/tmp/astrill-lazy/alctl` and creates
no file on the router. It transfers tagged hexadecimal snapshots because the
validated DD-WRT BusyBox image has `hexdump` but no `base64` command.

The GUI displays a read-only banner, disables policy and native-setting
mutations, and uses the same companion-free client reader for the Devices page.
It performs one startup check and does not poll the router over SSH in the
background. Later status and data reads are manual, page-demand, or returned by
an action. A successfully loaded empty device inventory is cached rather than
read again on every page visit. If desktop login startup runs before DD-WRT is
ready, use the manual Refresh action after the router finishes booting. The
SSH, Astrill installer, and companion onboarding actions remain available
because each has its own explicit confirmation.

## Native Include-Mode Example

A second validated Linksys E4200 was already working without this project's
companion:

| Native setting | Observed behavior |
| --- | --- |
| Website mode `1` | Direct by default; listed websites use Astrill |
| Device mode `2` | Astrill by default; listed devices use Direct |
| Wi-Fi mode `0` | Native Astrill default |
| VLAN mode `0` | Native Astrill default |
| Astrill autostart | Enabled |
| Companion markers/runtime | Absent |

An ordinary Internet probe exited directly, while a listed AI service exited
through the active Astrill endpoint. This confirms selective native routing,
not a global tunnel. Endpoint and client counts are intentionally not treated
as constants because applet data and live LAN state change.

## Enabling Changes Deliberately

The local write guard is explicit:

```bash
astrill-lazy access read-write
astrill-lazy access status
```

Reopen the GUI after changing access mode. Installing the companion remains a
separate action:

```bash
astrill-lazy install-router
```

Return to inspection-only access at any time:

```bash
astrill-lazy access read-only
```

Read-only mode is a local safety guard, not an authorization boundary. Anyone
who can edit the user's configuration or invoke SSH directly can still change
the router.

## Configuration Compatibility

Schema version 1 uses compatibility-aware defaults:

| Configuration | Companion | Write access |
| --- | --- | --- |
| No file yet | Disabled | Read-only |
| New saved file | Explicit field | Explicit field |
| Legacy file missing both fields | Enabled | Read-write |
| Existing file with `companion_enabled` | Preserved | Legacy read-write unless explicitly guarded |

The legacy rule prevents an upgrade from silently disabling a companion that
was already deployed. Set the write guard explicitly on an older installation
if inspection-only operation is desired.

## DD-WRT Key-Only SSH

Keep Telnet or another console available until key login has been tested.
Credentials must never be committed to this repository.

The Router page automates local key creation and can authorize that key using a
password supplied for one operation. The password entry defaults to `admin` for
factory-style DD-WRT setups but is never stored. Existing installations should
enter their actual password. The setup stages and verifies key login before it
disables SSH password authentication, and it does not alter Telnet.

On the workstation:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/astrill_lazy_router_ed25519 \
  -C astrill-lazy-router
cat ~/.ssh/astrill_lazy_router_ed25519.pub
```

On this DD-WRT release, the relevant NVRAM names are:

```text
sshd_enable
sshd_port
sshd_passwd_auth
sshd_forwarding
remote_mgt_ssh
sshd_authorized_keys
```

The password-authentication key is `sshd_passwd_auth`, not
`sshd_passwd`. Confirm firmware-specific names in
`/etc/config/sshd.webservices` or `/etc/config/base.nvramconfig` before
committing. A conservative LAN-only configuration is:

```text
sshd_enable=1
sshd_port=22
sshd_passwd_auth=0
sshd_forwarding=0
remote_mgt_ssh=0
```

Set the literal public key in `sshd_authorized_keys`, start SSH, and verify a
second key-only session before the single `nvram commit`. Do not close the
Telnet recovery session during validation. The workstation alias can then use:

```sshconfig
Host astrill-router
    HostName 192.168.1.1
    User root
    Port 22
    IdentityFile ~/.ssh/astrill_lazy_router_ed25519
    IdentitiesOnly yes
    PasswordAuthentication no
    KbdInteractiveAuthentication no
```

Leaving `remote_mgt_ssh=0` keeps SSH off the WAN. Whether Telnet remains
enabled is a separate recovery/security decision and is never changed by the
desktop application.

## Firmware Lessons

- Do not assume GNU userland. This firmware lacks `base64`.
- Use `set -e` or explicit guards in multi-command setup scripts. Otherwise a
  failed key-staging command can be followed by a service start with incomplete
  state.
- Discover the actual firmware NVRAM key before changing authentication.
- Validate SSH in volatile state first and commit once only after key login and
  password rejection both succeed.
- Keep router setup, companion installation, and Astrill policy changes as
  separate operations so each can be tested and rolled back independently.
- Never write router host private keys, account values, Telnet passwords, or a
  full personal LAN inventory into logs or public documentation.

## Implementation Bugs Found During This Audit

| Problem | Effect | Resolution |
| --- | --- | --- |
| Fresh configuration implied `companion_enabled=true` | Merely opening a new GUI could invoke companion reconciliation | Fresh state is now native-only and read-only |
| Missing configuration seeded a UU policy | Evaluation started with an unexplained editable rule | Fresh state now starts with no policy |
| Companion-disabled mode had no independent write guard | Native Save and connection controls could still mutate DD-WRT | `read_only` now guards every router mutation path |
| Devices always called `alctl clients` | Native-only routers could not load LAN inventory | A local parser now merges read-only DHCP, static, and ARP snapshots |
| The noVNC unit named one developer checkout path | A clone under `astrill-lazy-router` failed after reboot | The installer now renders the actual absolute checkout path |
| Ubuntu source installation always enabled login startup | Evaluation changed its desktop session unexpectedly | Ubuntu autostart is opt-in |
| A Conda `python3` could resolve to Python 3.10 | The installer could create an environment below the declared Python 3.11 minimum | The installer now selects and verifies a compatible interpreter |
| A common noVNC web port was already occupied | A second isolated test display could not bind | Display, VNC, and web ports remain separately overridable |
| Applet endpoint count differed by one from an earlier note | A dynamic upstream list looked like a regression | Tests and docs treat the count as observed data, not a constant |

Legacy configurations remain compatible: the safety defaults apply only when
no configuration exists, while older explicit deployments retain their
companion and write behavior.
