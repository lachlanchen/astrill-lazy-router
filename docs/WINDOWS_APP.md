# Native Windows Application

Astrill Lazy Router includes a native PySide6/Qt frontend for Windows. It runs
directly on Windows, uses the Windows OpenSSH client to reach DD-WRT, and does
not require WSL, GTK, VNC, or noVNC.

The Windows frontend controls the same native Astrill applet and optional
DD-WRT companion as the Ubuntu frontend. Routing is still enforced on DD-WRT;
the Windows application is a controller and does not install a local VPN,
proxy, packet filter, or network driver.

## Supported Features

The native Windows application provides:

- native-only status, Astrill settings, endpoint, and LAN-client inspection;
- local Direct/Astrill policies for services, domains, IPv4 networks, and LAN
  devices;
- the 261-profile service catalog and Suggested, Direct, or Astrill batch
  policy actions;
- country-preference and shared-endpoint summaries;
- confirmed Astrill connect and disconnect controls, plus a companion-backed
  action that selects an endpoint and reconnects the router's shared tunnel;
- installation, repair, refresh, rollback, and complete removal of the
  optional DD-WRT companion;
- a local read-only guard that is enabled for every fresh configuration.

The app has eight views: Policies, Services, Countries, Devices, Endpoints,
Astrill, Router, and Settings. Local policy edits are saved immediately, but
they do not affect traffic until the companion is installed and `Apply
policies` succeeds.

## Requirements

Running the packaged application requires:

- Windows 10 or Windows 11;
- the Windows OpenSSH Client (`ssh.exe`) on `PATH`;
- a DD-WRT router with a working Astrill applet;
- verified key-only root SSH access to the router, or temporary LAN Telnet
  access for guided first-time key authorization.

Check OpenSSH from PowerShell:

```powershell
Get-Command ssh.exe
ssh.exe -V
```

OpenSSH Client can be added through **Settings > Optional features** if it is
not already installed.

Building from source additionally requires Python 3.11 or newer and network
access for the first dependency installation. The packaged application
contains its Python and Qt runtime; end users do not need a separate Python
installation after it has been built.

## Build From Source

Clone or update the repository, then run the native build script from its root:

```powershell
git clone https://github.com/lachlanchen/astrill-lazy-router.git
Set-Location .\astrill-lazy-router

powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File .\contrib\windows\build-native.ps1
```

The script:

1. creates `build\windows-venv`;
2. installs the project with its `windows` dependencies;
3. builds a windowed PyInstaller bundle;
4. verifies that the catalog, router package, and schema data were included.

The result is the complete folder:

```text
dist\windows\Astrill Lazy Router\
```

Do not copy only `Astrill Lazy Router.exe`; its adjacent `_internal` runtime is
required. The build script does not launch the result.

Use `-Python` to select a specific interpreter:

```powershell
.\contrib\windows\build-native.ps1 `
  -Python "C:\Path\To\Python311\python.exe"
```

`-VirtualEnvironment`, `-DistPath`, and `-WorkPath` can relocate build output.
`-SkipDependencyInstall` is intended only for an already-prepared build
environment.

The current source build is a PyInstaller folder rather than an MSI or MSIX,
and the build script does not code-sign it. Windows reputation warnings are
therefore possible on a newly built binary.

## Install Or Update

Install the built bundle for the current Windows user:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File .\contrib\windows\install-native.ps1
```

The installer:

- copies the complete bundle to
  `%LOCALAPPDATA%\Programs\Astrill Lazy Router`;
- stages an update before replacing the installed copy;
- refuses to update while the installed application is running;
- creates one Desktop shortcut and one current-user Startup shortcut;
- removes an older same-named Start Menu shortcut;
- does not create a Start Menu entry, request administrator access, or launch
  the application during installation.

The Startup shortcut is recreated idempotently on every install or update. It
launches the installed app after the current user signs in, including after a
Windows reboot; it does not run before sign-in or as a Windows service. Its
location is:

```text
%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\Astrill Lazy Router.lnk
```

To install a bundle built in another directory:

```powershell
.\contrib\windows\install-native.ps1 `
  -PackagePath "D:\Builds\Astrill Lazy Router"
```

Close Astrill Lazy Router before updating, rebuild the bundle, and rerun the
installer. Launch it from the Desktop shortcut or directly:

```powershell
& "$env:LOCALAPPDATA\Programs\Astrill Lazy Router\Astrill Lazy Router.exe"
```

## Set Up The Dedicated SSH Key

The application generates or reuses a dedicated local Ed25519 key through
Windows OpenSSH. The private key remains under the configured local path; only
its public half is sent to DD-WRT. Background router commands use
`BatchMode=yes`, `IdentitiesOnly=yes`, `StrictHostKeyChecking=yes`, and the
application-pinned `known_hosts` file. Password prompts and unverified host
keys therefore fail closed.

The fresh Settings values are host `192.168.1.1`, user `root`, port `22`, and
private-key path `~/.ssh/astrill_lazy_router_ed25519`. For guided setup:

1. Keep the PC and router on a trusted local network.
2. Enter the explicit host, user, port, and identity path in **Settings**.
3. Select **Set up key via Telnet**.
4. Compare the displayed SSH algorithm and SHA-256 fingerprint with a trusted
   router record, Web UI, label, or local console. Cancel if it cannot be
   verified.
5. Confirm the Telnet warning and enter the router's root Telnet password.
6. Wait for both strict key-only SSH checks to succeed.

Telnet sends credentials without encryption. The guided flow therefore uses
it once on the LAN and only after fingerprint confirmation. The password is
held transiently in application memory and is never saved in configuration,
logs, command arguments, or environment variables. The setup script verifies
router UID 0, preserves existing authorized keys, appends the public key
idempotently, and restarts SSH. It disables SSH password authentication and
WAN SSH only after Windows OpenSSH proves that the dedicated key works, then
verifies key-only access again. Telnet itself remains available as the
documented recovery path.

An SSH alias can instead be used as the host field. Select **Use OpenSSH config
for user, port, and private key** when the alias should supply those options:

```sshconfig
Host astrill-router
    HostName 192.168.1.1
    User root
    IdentityFile ~/.ssh/id_ed25519_astrill_router
    IdentitiesOnly yes
```

Guided onboarding requires explicit fields rather than an alias. The
interactive terminal remains available as an advanced fallback and explicitly
uses `StrictHostKeyChecking=ask`. The application never supplies
`StrictHostKeyChecking=no` and never auto-accepts a new key. If OpenSSH reports
that a host key changed, first
verify that the router was intentionally replaced, reset, or rekeyed. Only
after that independent verification should the affected entry named in the
error be removed:

For example, when the error names `192.168.1.1`:

```powershell
ssh-keygen.exe -R "192.168.1.1"
```

Substitute the exact host, IP, or `[host]:port` named by OpenSSH. Do not delete
the complete `known_hosts` file to bypass a mismatch.

The Settings host accepts only an SSH alias, hostname, IP address, or
`user@host` without spaces or command-line options. User, port, and private
key also have dedicated validated fields. Relative private-key paths are
rejected; use an absolute path or one beginning with `~`.

## Safe First Use

A fresh Windows configuration is:

- native-only: the optional companion is not assumed to exist;
- read-only: router-changing operations are blocked;
- empty: no policy is seeded or applied automatically.

The application refreshes status on startup and every 60 seconds. In
native-only mode those refreshes read the Astrill applet and DD-WRT state
directly.

When the local configuration records a previously confirmed companion,
refresh also reconciles router-reboot state:

- a current, healthy runtime is reused as-is;
- if the validated current package remains in NVRAM but `/tmp` was cleared,
  the runtime is reconstructed from that stored package without rewriting
  NVRAM;
- if neither persistent markers nor a runtime remain, the desktop falls back
  atomically to native-only mode and keeps **Install / upgrade** available;
- SSH or router unavailability leaves companion mode unchanged so the next
  monitor refresh can retry.

An inconsistent runtime or a package requiring a version rewrite fails closed.
The app never silently installs or rewrites a missing or incompatible
companion; that still requires the separate **Install / upgrade**
confirmation.

Recommended first-use sequence:

1. Set up and test key-only SSH in **Settings**.
2. Keep the read-only guard enabled while reviewing **Router**, **Astrill**,
   **Devices**, and **Endpoints**.
3. Add or edit local policies if desired. This changes only the current user's
   configuration.
4. Review the router prerequisites and recovery procedure in
   [Router Installation And Rollback](ROUTER_INSTALL.md).
5. Select **Install / upgrade** in **Router** and confirm the exact DD-WRT
   writes. On a fresh profile that confirmation also disables the local guard;
   a failed installation restores it automatically.
6. In **Endpoints**, load the server list, select a server and protocol, then
   select **Connect router to selected endpoint**. The separate confirmation
   writes the endpoint to DD-WRT and briefly reconnects the shared router
   tunnel. It does not connect a VPN or change routing on the Windows PC.
7. Apply policies only through the separate confirmation when intended.

Guarded remote write operations include native Astrill setting changes,
connection changes, companion installation, policy application, endpoint
switching, domain refresh, rollback, and companion removal. Destructive or
traffic-interrupting actions add confirmation dialogs, and the controller
checks the read-only guard again even when a button remains visible.

The endpoint list remains available for read-only inspection. Switching is
enabled only while the app is idle, the read-only guard is off, the companion
is enabled, and a server is selected. The companion transaction restores the
previous router endpoint settings when the requested endpoint does not connect.
All Astrill-targeted devices and policies still share that one router tunnel.

The guard is an accident-prevention feature, not an authorization boundary.
A user who can edit the configuration or invoke `ssh.exe` directly can still
change the router.

## Windows Limitations

There is no Windows per-application WFP routing backend in this release.
Ubuntu application policies depend on a Linux macvlan network namespace,
Polkit, and a distinct DD-WRT DHCP identity. The Windows frontend therefore:

- cannot create a Windows process/application policy;
- cannot launch an application inside an isolated routing identity;
- cannot prepare or clean up an Ubuntu application namespace.

An existing Ubuntu process rule can be displayed as a policy record, but its
network identity still has to be launched and managed from Ubuntu. A genuine
Windows equivalent would require a separately designed, privileged, and
signed Windows Filtering Platform service or driver; none is installed by this
project.

Other intentional boundaries:

- one router Astrill tunnel means one active VPN endpoint for all VPN policies;
- routing enforcement occurs on DD-WRT, not in the Windows network stack;
- the policy engine is currently IPv4;
- Windows login startup is installer-managed rather than exposed as an in-app
  switch; the Windows UI does not expose Polkit helpers, extension management,
  route detection, or namespace lifecycle controls;
- the native app does not use or expose noVNC.

## Configuration And Uninstall

The Windows configuration is stored at:

```text
%LOCALAPPDATA%\Astrill Lazy Router\config.json
```

`XDG_CONFIG_HOME`, when explicitly set, takes precedence. The configuration
contains the SSH target, local policies, enabled catalog IDs, companion mode,
and read-only state. The confirmed SSH host key is pinned beside it as
`known_hosts`. The dedicated private key remains at its separately configured
path. Neither file contains the router Telnet password or Astrill account
credentials.

Uninstall the native application with:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File .\contrib\windows\uninstall-native.ps1
```

Close the application first. `-Force` terminates only a running executable
from the expected per-user installation directory. Uninstall removes the
application folder and matching Desktop, Startup, and legacy Start Menu
shortcuts but deliberately preserves unrelated shortcuts, user configuration,
and SSH keys.

## Native App Versus The noVNC Launcher

`install-native.ps1` installs the Qt application documented here. The older
`install-launcher.ps1` installs a browser shortcut that opens an Ubuntu noVNC
session through an SSH tunnel to another workstation. It is not the native
Windows application and is unnecessary for normal Windows use.

The two installers use the same display name, so do not install both on the
same Windows account unless that shortcut replacement is intentional.
