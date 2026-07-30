# Set Up Astrill Lazy Router

Astrill Lazy Router is a separate control layer. The desktop app mirrors safe
native Astrill settings and can optionally install a small DD-WRT companion for
service, website, device, and application routing.

It does not replace the Astrill applet, create another Astrill subscription, or
open several VPN endpoints. The E4200 has one active Astrill tunnel; all
`Astrill` policy rules use that shared endpoint.

## Design

The three layers have separate ownership:

| Layer | Owns |
| --- | --- |
| Astrill applet | Tunnel, endpoint, protocol, DNS, native filters |
| Desktop app | Editable policies, service catalog, UI, endpoint tests |
| Router companion | Validated packet marks, Direct/VPN tables, rollback |

The companion:

- leaves native Astrill files and native rule lists unchanged;
- inserts one early hook into its own A/B firewall chains;
- uses separate high mark bits and routing tables;
- validates the complete policy before activation;
- retains the previous policy when capacity permits;
- fails VPN-marked traffic closed if the tunnel disappears; and
- can be fully removed with **Restore Astrill Only**.

Fresh desktop configurations start native-only and read-only. Merely opening
the app does not install or rewrite the router.

## 1. Install On Ubuntu

The validated workstation is Ubuntu 24.04 with Python 3.12. Python 3.11 or
newer is required.

Install system prerequisites:

```bash
sudo apt update
sudo apt install \
  git openssh-client python3 python3-venv python3-gi \
  gir1.2-gtk-4.0 gir1.2-adw-1
```

Clone and install:

```bash
mkdir -p ~/Projects
cd ~/Projects
git clone https://github.com/lachlanchen/astrill-lazy-router.git astrill-lazy
cd astrill-lazy
./scripts/install-desktop.sh
```

Launch from the application menu or run:

```bash
astrill-lazy-gui
```

To opt into launch after desktop sign-in:

```bash
astrill-lazy autostart enable
astrill-lazy autostart status
```

Autostart is a desktop-session setting. The router companion has its own
router-local boot reconstruction and watchdog.

## 2. Prepare Key-Only Router Access

Keep a Telnet or local recovery session open until key-only SSH is verified.

In the app's **Router** settings use:

```text
Address: 192.168.1.1
User: root
Port: 22
```

Then:

1. Confirm the displayed router host-key fingerprint.
2. Use **Set up SSH key**.
3. Enter the current DD-WRT password for this one operation.
4. Let the app generate and authorize its dedicated Ed25519 key.
5. Confirm key login succeeds.
6. Confirm SSH password login is disabled and WAN SSH remains disabled.

The password is not stored. The default dedicated key is separate from a
general personal SSH key.

For a manual OpenSSH alias, adapt
[`ssh-config.example`](ssh-config.example), then test:

```bash
ssh -o BatchMode=yes astrill-router 'uname -a'
```

## 3. Audit In Native-Only Mode

Before allowing writes:

```bash
astrill-lazy access read-only
astrill-lazy access status
astrill-lazy inspect
```

In the GUI, refresh **Router**, **Astrill**, **Endpoints**, and **Devices**.
Confirm:

- the applet is healthy;
- connected/disconnected state matches My Page;
- the active endpoint and protocol match;
- native site and device modes match;
- the endpoint list loads from the installed applet; and
- the expected LAN clients are present.

Native-only inspection creates no companion runtime and performs no recurring
background SSH polling.

## 4. Capture The Baseline

Keep private outputs outside the public repository:

```bash
mkdir -p ~/.local/state/astrill-lazy
chmod 700 ~/.local/state/astrill-lazy
astrill-lazy inspect --full \
  > ~/.local/state/astrill-lazy/native-before-companion.json
chmod 600 ~/.local/state/astrill-lazy/native-before-companion.json
```

The full inspection can contain private device and routing information. Do not
commit it.

Also record one successful Direct public IP and one successful Astrill public
IP.

## 5. Install The Optional Companion

Only proceed after native Astrill is stable.

1. Turn off the local read-only guard or run:

   ```bash
   astrill-lazy access read-write
   ```

2. On **Router**, choose **Install / Upgrade**.
3. Review the confirmation describing NVRAM, startup, My Page, and runtime
   changes.
4. Approve once.
5. Wait for **Healthy**, the expected version, policy precedence, and watchdog.

The equivalent CLI command is:

```bash
astrill-lazy install-router
astrill-lazy status
```

The first install requires roughly 16 KiB of free NVRAM. Do not proceed if the
preflight rejects capacity. The compiled policy itself is limited to 6,144
bytes and is never silently truncated.

## 6. Apply A Minimal Policy

Start with one narrow exception, not the entire catalog.

For global Astrill routing:

1. Add `UU Remote`.
2. Select `Direct`.
3. Use priority `100`.
4. Leave country override unset for a Direct rule.
5. Apply the selected rule.
6. Reconnect UU Remote so it creates new flows.
7. Confirm `astrill-lazy status` remains healthy.
8. Compare UU Remote responsiveness with Astrill connected and disconnected.

Then add other essential Direct services such as WeChat, Taobao, Meituan, or
Jianguoyun one at a time. Keep Google, YouTube, Instagram, GitHub, and AI
services on Astrill when that is the desired behavior.

An applied policy replaces the complete selected router document. The GUI
preflights the compiled size before making any router change.

## 7. Understand Precedence

Rules are evaluated by increasing priority; the first match wins.

Example:

| Priority | Match | Result |
| ---: | --- | --- |
| 100 | UU Remote service destinations | Direct |
| 500 | Work laptop device | Astrill |

UU Remote goes Direct from that laptop, while its other traffic uses Astrill.

The overlay extends the native mode:

| Native mode | Typical companion use |
| --- | --- |
| Global Astrill | Add narrow Direct exceptions |
| Tunnel only listed | Add Astrill unions or explicit Direct subtraction |
| Exclude listed | Add Direct unions or explicit Astrill subtraction |

Removing a companion rule reveals the unchanged native result again.

## 8. Verify The Result

Use [`OPERATIONS-AND-RECOVERY.md`](OPERATIONS-AND-RECOVERY.md) for the full
checklist. The minimum commands are:

```bash
astrill-lazy access status
astrill-lazy inspect
astrill-lazy status
astrill-lazy rules
```

Test all four states after the first install:

1. Astrill disconnected, no companion match.
2. Astrill connected, no companion match.
3. Astrill connected, Direct companion match.
4. Router reboot followed by the intended Astrill autostart behavior.

Never flush router-wide connection tracking to test one application. Reconnect
only the affected application after policy changes.
