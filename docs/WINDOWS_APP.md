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
- confirmed Astrill connect and disconnect controls, plus companion-backed
  endpoint changes;
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
- verified key-only root SSH access to the router.

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
- creates one **Desktop shortcut only**;
- removes an older same-named Start Menu shortcut;
- does not create a Start Menu entry, enable login startup, request
  administrator access, or launch the application.

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

## Verify The SSH Host Key

The application delegates host-key and private-key handling to Windows
OpenSSH. It does not copy, generate, or store a private key, and its background
router commands use `BatchMode=yes`. Password prompts and first-contact
host-key prompts therefore fail closed.

An SSH alias is the easiest configuration, especially when a non-default port
or dedicated key is used. Create or edit `%USERPROFILE%\.ssh\config`:

```sshconfig
Host astrill-router
    HostName 192.168.1.1
    User root
    IdentityFile ~/.ssh/id_ed25519_astrill_router
    IdentitiesOnly yes
```

Then establish trust deliberately:

1. Obtain the router SSH host-key fingerprint through a trusted path, such as
   its local console or the record made when SSH was provisioned.
2. Open **Settings** in Astrill Lazy Router and enter `astrill-router`, or a
   direct target such as `root@192.168.1.1`.
3. Select **Open interactive SSH setup**.
4. Compare the fingerprint shown by `ssh.exe` with the trusted fingerprint.
   Type `yes` only when they match.
5. Complete public-key setup if needed, close the terminal, and select **Save
   and test**. The test must succeed without a password prompt.

The application never supplies `StrictHostKeyChecking=no` and never
auto-accepts a new key. If OpenSSH reports that a host key changed, first
verify that the router was intentionally replaced, reset, or rekeyed. Only
after that independent verification should the affected entry named in the
error be removed:

For example, when the error names `192.168.1.1`:

```powershell
ssh-keygen.exe -R "192.168.1.1"
```

Substitute the exact host, IP, or `[host]:port` named by OpenSSH. Do not delete
the complete `known_hosts` file to bypass a mismatch.

The Settings target accepts only an SSH alias, hostname, IP address, or
`user@host` without spaces or command-line options. Put advanced options such
as `Port` in the OpenSSH config alias.

## Safe First Use

A fresh Windows configuration is:

- native-only: the optional companion is not assumed to exist;
- read-only: router-changing operations are blocked;
- empty: no policy is seeded or applied automatically.

The application refreshes status on startup and every 60 seconds. In
native-only mode those refreshes read the Astrill applet and DD-WRT state
directly. Unlike the Ubuntu startup integration, the Windows frontend does not
automatically install or repair the companion.

Recommended first-use sequence:

1. Verify and test SSH in **Settings**.
2. Keep the read-only guard enabled while reviewing **Router**, **Astrill**,
   **Devices**, and **Endpoints**.
3. Add or edit local policies if desired. This changes only the current user's
   configuration.
4. Review the router prerequisites and recovery procedure in
   [Router Installation And Rollback](ROUTER_INSTALL.md).
5. Disable the guard only when router changes are intended and confirm the
   warning.
6. Install the optional companion from **Router**, then apply policies.

Guarded remote write operations include native Astrill setting changes,
connection changes, companion installation, policy application, endpoint
switching, domain refresh, rollback, and companion removal. Destructive or
traffic-interrupting actions add confirmation dialogs, and the controller
checks the read-only guard again even when a button remains visible.

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
- the Windows UI does not expose Ubuntu login autostart, Polkit helpers,
  extension management, route detection, or namespace lifecycle controls;
- the native app does not use or expose noVNC.

## Configuration And Uninstall

The Windows configuration is stored at:

```text
%LOCALAPPDATA%\Astrill Lazy Router\config.json
```

`XDG_CONFIG_HOME`, when explicitly set, takes precedence. The configuration
contains the SSH target, local policies, enabled catalog IDs, companion mode,
and read-only state. It does not contain the SSH private key, router password,
or Astrill account credentials.

Uninstall the native application with:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File .\contrib\windows\uninstall-native.ps1
```

Close the application first. `-Force` terminates only a running executable
from the expected per-user installation directory. Uninstall removes the
application folder and matching shortcuts but deliberately preserves user
configuration and SSH keys.

## Native App Versus The noVNC Launcher

`install-native.ps1` installs the Qt application documented here. The older
`install-launcher.ps1` installs a browser shortcut that opens an Ubuntu noVNC
session through an SSH tunnel to another workstation. It is not the native
Windows application and is unnecessary for normal Windows use.

The two installers use the same display name, so do not install both on the
same Windows account unless that shortcut replacement is intentional.
