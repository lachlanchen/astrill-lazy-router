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

Telnet remains enabled as a deliberate recovery mechanism. This is less secure
than SSH on an untrusted LAN. Disable it only after confirming another console
or recovery path, and do not couple that change to a policy upgrade.

The DD-WRT web account is separate from Telnet root access. Its password was not
guessed, reset, or stored by this project.

Desktop login startup is a mode `0644` freedesktop entry in the current user's
configuration when explicitly enabled. It stores only the absolute GUI
executable path. Companion
reconciliation uses the existing key-only SSH alias and stores no router or
Ubuntu password.

A fresh configuration is native-only and read-only. The GUI and CLI block
policy apply/rollback/refresh, endpoint switching, connection changes, and
native-setting writes until the local operator enables write access. The GUI's
separately confirmed companion onboarding action can enable write access after
a successful install. The guard prevents accidents; it is not a security
boundary against someone who controls the user account or invokes SSH directly.

The native settings mirror uses an explicit safe-key allowlist. Astrill
account values, router passwords, installer URLs, and generated OpenVPN
credentials are neither requested nor returned. Writes use normalized values,
commit once, and read every changed key back for exact verification.

Astrill installer input is transient. The GUI uses a redacted `xxx/xxx`
template, limits downloaded or pasted shell text to 512 KiB, displays its
SHA-256 digest, and requires a second confirmation before root execution. A
repository test rejects token-bearing Astrill installer paths.

## Input Handling

Router TSV input is limited to 6,144 bytes and exactly ten fields per rule.
The controller validates:

- small ASCII IDs;
- IPv4 addresses and prefixes;
- DNS names;
- target, match kind, protocol, priority, and enabled enums;
- bounded destination ports and ranges;
- URL-encoded display labels.

iptables commands are constructed with positional arguments. Rule content is
never passed to `eval`, `sh -c`, or command substitution as executable text.

The DD-WRT page permits only fixed command names and validated IDs. Arbitrary
website text is edited over SSH in the native app, not interpolated into
`apply.cgi`.

## Routing Safety

- Private/local destinations return before policy marking.
- Direct and VPN marks use bits outside Astrill's mask.
- The plugin uses separate policy tables and does not flush Astrill chains.
- VPN policy has a blackhole default when `tun0` is down.
- A/B activation leaves the previous chain live until the new chain is ready.
- The watchdog repairs applet/firewall restarts within 15 seconds.
- `alctl stop` removes only plugin-owned objects.
- Automatic reconciliation checks version and runtime markers before any
  install, and compares the deterministic package fingerprint before recovery,
  so a healthy or identically broken companion does not cause repeated NVRAM
  writes.
- Route detection is read-only and requires the existing tunnel to be up.
  Recommendations are displayed before a separate apply action; incomplete
  comparisons retain the current route.

Domain matching still depends on known, resolved service domains. Unknown CDN
hostnames follow the router's ordinary Astrill behavior rather than being
magically attributed to a company.

## Application Helper

`astrill-lazy-netns` requires root and rejects:

- malformed profile and interface names;
- missing parent links;
- non-desktop UIDs;
- invalid session PIDs;
- relative or non-executable application paths;
- invalid DHCP addresses and unsupported netmasks.

It launches the app through `runuser` after entering the namespace, preserving
only required session variables. Arguments remain an `argv` list and are never
evaluated by a shell.

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
