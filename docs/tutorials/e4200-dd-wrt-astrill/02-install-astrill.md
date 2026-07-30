# Install The Astrill Applet On DD-WRT

Install Astrill only after the clean DD-WRT baseline works. The applet is an
independent vendor component; Astrill Lazy Router does not replace or patch it.

## 1. Prerequisites

- The E4200 is running the final DD-WRT image.
- WAN, DNS, HTTPS, and NTP work without a VPN.
- The DD-WRT web interface is reachable from the wired LAN.
- You can restore the clean DD-WRT configuration.
- Your Astrill account provides router applet access.

## 2. Generate A Fresh Private Installer

Sign in to Astrill and open:

[VPN Service > Router Set-up](https://www.astrill.com/member-zone/tools/router-set-up)

Astrill generates a command shaped like:

```sh
eval `wget -q -O - http://astroutercn.com/router/install/xxx/xxx`
```

The two real path values are account-specific credentials.

- Never put the real command in Git, a README, a ticket, a screenshot, chat,
  shell history, or the desktop configuration.
- Do not reuse the redacted example; `xxx/xxx` cannot install anything.
- Generate a fresh command when reinstalling.
- The vendor command may use plaintext HTTP. Run it only from the trusted LAN
  and review the fetched script when possible.

## 3. Official DD-WRT Web Installation

This follows Astrill's documented DD-WRT method.

1. Open `http://192.168.1.1/`.
2. Go to **Administration > Commands**.
3. Paste the private command into the command box.
4. Click **Run Commands** once.
5. Do **not** click **Save Startup**. Saving the generated URL would persist its
   credential.
6. Wait several minutes. The command may restart applet services.
7. Open **Status > My Page**.
8. Confirm the Astrill page renders and shows an endpoint list.

The official applet executable is expected at:

```text
/dev/astrill/astrillvpn
```

The applet stores its own boot integration and native `astrill_*` settings.

## 4. Safer Reviewed Installation Through Astrill Lazy

This is an alternative after the desktop controller and key-only SSH have been
prepared in native-only mode:

1. Open Astrill Lazy Router.
2. Keep the companion disabled.
3. On **Router**, confirm the correct host and SSH fingerprint.
4. Select the Astrill installation action.
5. Paste the newly generated command.
6. Review the displayed source, byte size, SHA-256, and plaintext-transport
   warning.
7. Confirm the one-time installation.

The desktop downloader:

- accepts a single installer URL or pasted UTF-8 shell script;
- rejects empty, NUL-containing, or larger-than-512-KiB content;
- shows a SHA-256 before execution;
- redacts the two URL credentials from retained source text; and
- verifies that the applet appears after execution.

It does not save the password, installer script, or private URL.

## 5. Establish The Native Astrill Baseline

Before installing the optional companion, test Astrill alone.

1. In **Status > My Page**, choose one known endpoint.
2. Start with OpenVPN UDP and an available applet-provided port.
3. Connect and wait for a stable connected state.
4. From a client, verify:

   ```bash
   curl -4 --max-time 15 https://ifconfig.co/ip
   curl -4 --max-time 15 -I https://www.google.com/
   ```

5. Record the VPN public IP.
6. Disconnect Astrill.
7. Repeat the two tests and confirm the public IP returns to the Direct WAN.
8. Reconnect and test again.
9. Reboot the router and verify the selected autostart behavior.

Astrill's applet documentation recommends UDP for speed and TCP when UDP is
blocked or unreliable. Select only protocol and port combinations actually
offered for the chosen server.

## 6. Configure Native Routing First

Astrill's own controls remain authoritative and available:

| Native website mode | Default path | Listed path |
| --- | --- | --- |
| Tunnel all | Astrill | Not applicable |
| Tunnel only listed | Direct | Astrill |
| Exclude listed | Astrill | Direct |

Its device filter similarly supports all, selected-only, and excluded-device
modes. Confirm those native modes work before adding the companion.

For the common deployment:

1. Use global Astrill routing.
2. Confirm normal websites work.
3. Add only essential native exclusions if needed.
4. Let Astrill Lazy add narrower Direct exceptions later.

Do not enter the same rule in several layers without recording which layer
owns it.

## 7. Back Up The Working Applet

Create a private snapshot only after native Astrill works. At minimum retain:

- DD-WRT build and model;
- encrypted DD-WRT settings backup;
- Astrill applet version;
- selected endpoint, protocol, and port;
- native website and device modes;
- current startup setting; and
- a tested Direct/VPN public-IP record.

The project-specific encrypted backup procedure is documented at
`docs/BACKUP_RESTORE.md` in the Astrill Lazy Router checkout.

Do not commit a plaintext Astrill applet, installer response, account URL,
NVRAM dump, device MAC table, or generated OpenVPN configuration.

## 8. Troubleshooting

### My Page does not appear

- Wait three minutes and reload.
- Confirm DD-WRT still has ordinary Internet access.
- Confirm `/dev/astrill/astrillvpn` exists and is executable through an
  authenticated LAN shell.
- Generate a fresh installer rather than reusing a published or stale URL.

### Connected but websites do not resolve

- Test an IPv4 address separately from a domain name.
- Inspect the DNS choice in Astrill's DNS page.
- Renew the client DHCP lease and flush only the client's DNS cache.
- Avoid restarting DD-WRT `dnsmasq` while Astrill is connected unless the
  active Astrill DNS state is preserved.

### Connected but slower than Direct

- Test a geographically closer endpoint.
- Compare UDP and TCP using supported ports.
- Disable applet acceleration for computer-heavy use if it makes performance
  worse.
- Test Direct and Astrill separately before changing routing lists.

### Remove the vendor applet

Astrill documents this command:

```sh
/dev/astrill/astrillvpn uninstall
```

Use it only when intentionally removing Astrill. `Restore Astrill Only` in our
application removes the separate companion, not the Astrill applet.

Proceed to [`astrill-lazy-control/`](astrill-lazy-control/) only after this
native baseline is stable.
