# Install DD-WRT On A Linksys E4200 v1

## 1. Confirm The Hardware

Check the label on the bottom of the router before downloading or flashing
anything.

| Label | Action |
| --- | --- |
| `E4200` or confirmed E4200 v1 | Continue with this guide |
| `E4200 v2` | Stop; none of these images apply |
| Missing or ambiguous label | Stop and identify the board first |

The DD-WRT model page identifies E4200 v1 as a Broadcom BCM4718 platform with
16 MB flash and 64 MB RAM. E4200 v2 uses a different chipset and is not
supported by this procedure.

## 2. Prepare A Local Recovery Window

Use one computer and one Ethernet cable.

1. Download the current Linksys stock firmware for the exact hardware revision
   as a recovery file.
2. Record WAN type and any PPPoE credentials, LAN subnet, DHCP range, static
   reservations, Wi-Fi SSIDs, and Wi-Fi passwords.
3. Export the current configuration only as a same-firmware emergency backup.
   Do not restore a Linksys backup into DD-WRT or a DD-WRT backup into a
   different build.
4. Disable Wi-Fi, VPN software, proxies, and extra network adapters on the
   flashing computer.
5. Disconnect the router WAN cable and every LAN client except the flashing
   computer.
6. Connect the computer to a numbered LAN port, not the Internet/WAN port.
7. Use reliable power. Do not reboot, unplug, close the browser, or press reset
   while an image is being written.

## 3. Verify The Images

The pinned bundle uses this sequence:

| Stage | Image | Bytes |
| --- | --- | ---: |
| Initial stock flash | `dd-wrt.v24-21676_NEWD-2_K2.6_mini-e4200.bin` | 3,548,160 |
| Modern K2.6 bridge | `dd-wrt.v24-62374_NEWD-2_K2.6_mini-e4200.bin` | 3,859,456 |
| Final K3.x image | `dd-wrt.v24-62374_NEWD-2_K3.x_mega-e4200.bin` | 7,472,128 |

From this directory:

```bash
sha256sum -c SHA256SUMS
```

All three lines must report `OK`. Stop if a name, size, or hash differs.

`r21676` is a legacy, temporary initial-flash bridge. Keep the WAN disconnected
while it is installed and upgrade to the pinned modern build in the same
maintenance window. Do not operate `r21676` as the final Internet router.

Official download locations:

- [r21676 E4200 initial mini](https://download1.dd-wrt.com/dd-wrtv2/downloads/betas/2013/05-27-2013-r21676/broadcom_K26/dd-wrt.v24-21676_NEWD-2_K2.6_mini-e4200.bin)
- [r62374 E4200 K2.6 mini](https://download1.dd-wrt.com/dd-wrtv2/downloads/betas/2025/10-19-2025-r62374/broadcom_K26/dd-wrt.v24-62374_NEWD-2_K2.6_mini-e4200.bin)
- [r62374 E4200 K3.x mega](https://download1.dd-wrt.com/dd-wrtv2/downloads/betas/2025/10-19-2025-r62374/broadcom_K3X/dd-wrt.v24-62374_NEWD-2_K3.x_mega-e4200.bin)

## 4. Reset The Stock Router

The official DD-WRT E4200 v1 page calls for a 30/30/30 reset around the
initial flash:

1. With the router powered on, hold Reset for 30 seconds.
2. Keep holding Reset, remove power, and wait 30 seconds.
3. Keep holding Reset, restore power, and wait 30 seconds.
4. Release Reset and allow the router to boot fully.

Use this only after confirming E4200 v1. Do not generalize this reset method to
other Linksys revisions or modern routers.

Wait until the computer receives a LAN address, then open:

```text
http://192.168.1.1/
```

## 5. Initial Flash From Stock Linksys Firmware

1. Open the Linksys firmware upgrade page.
2. Select only:

   ```text
   dd-wrt.v24-21676_NEWD-2_K2.6_mini-e4200.bin
   ```

3. Start the upgrade once.
4. Wait for the success page, then wait at least three additional minutes.
5. Do not trust an early ping response as proof that flash writing is done.
6. Perform the E4200 v1 reset described above after the completed flash.
7. Wait for a DHCP address and open `http://192.168.1.1/`.
8. Set a new DD-WRT administrator username and a strong, unique password.

The first image must be the E4200-trailed mini file. Do not use an `nv60k`
image for the stock-to-DD-WRT transition. Keep WAN disconnected and continue
directly to the modern upgrade.

## 6. Upgrade To The Pinned Modern Build

This bundle records a conservative two-step upgrade after the initial K2.6
installation.

### Stage A: r62374 K2.6 mini

1. Open **Administration > Firmware Upgrade**.
2. Choose reset to default settings after flashing.
3. Select:

   ```text
   dd-wrt.v24-62374_NEWD-2_K2.6_mini-e4200.bin
   ```

4. Start once and wait for completion plus at least three minutes.
5. Allow a full boot, reset to defaults if the upgrade did not do so, and
   confirm the DD-WRT build number.

### Stage B: r62374 K3.x mega

1. Return to **Administration > Firmware Upgrade**.
2. Choose reset to default settings after flashing.
3. Select:

   ```text
   dd-wrt.v24-62374_NEWD-2_K3.x_mega-e4200.bin
   ```

4. Start once and wait for completion plus at least five minutes.
5. Allow the router to finish booting before reconnecting or resetting it.
6. Recreate the administrator password and inspect **Status > Router**.

Expected final build:

```text
DD-WRT v3.0-r62374 mega
```

The E4200 page permits a trailed K3.x image after DD-WRT is installed. This
guide retains the modern K2.6 bridge because it is part of the verified offline
recovery sequence. Do not substitute a generic K3.x image.

## 7. Configure A Clean DD-WRT Baseline

Configure and test DD-WRT before adding Astrill.

1. Set the correct WAN mode under **Setup > Basic Setup**.
2. Keep the LAN at `192.168.1.1/24` unless the upstream network conflicts.
3. Set DHCP range and static reservations.
4. Configure NTP and the correct time zone.
5. Configure 2.4 GHz and 5 GHz wireless with WPA2 Personal and AES.
6. Do not enable WAN web administration, WAN SSH, or WAN Telnet.
7. Reconnect the WAN cable.
8. Confirm DNS, HTTP, HTTPS, and a router reboot all work without Astrill.
9. Save an encrypted or private baseline backup.

The model page notes that the E4200 runs warm. Provide airflow; its page
suggests reducing radio transmit power from 100 mW to roughly 40-50 mW when
heat affects stability.

## 8. Enable LAN-Only Administration

Under **Services > Services**, enable SSHd for LAN administration. SSH and
Telnet use the username `root`, even if the web interface has another
administrator username.

Keep password login only as long as needed to authorize the dedicated key.
The Astrill Lazy Router setup later verifies key login before disabling SSH
password authentication and leaves WAN SSH disabled.

Create another configuration backup after:

- WAN and DNS work;
- both Wi-Fi bands work;
- the router reboots cleanly; and
- LAN administration is reachable.

Proceed to [`02-install-astrill.md`](02-install-astrill.md).

## Recovery Notes

If the web page disappears during an ordinary reboot, wait five minutes,
renew the computer's DHCP lease, and retry `192.168.1.1`. Clear stale browser
state or use a private window.

If the router never completes boot:

1. Stop repeated power cycling.
2. Disconnect WAN and all unnecessary clients.
3. Test the model-specific management or TFTP recovery window from a wired
   computer.
4. Use only a verified E4200 v1 image.
5. Escalate to serial or JTAG recovery rather than writing unrelated firmware.

Flashing `nv64k` or generic firmware can require JTAG NVRAM recovery, according
to the DD-WRT E4200 page.
