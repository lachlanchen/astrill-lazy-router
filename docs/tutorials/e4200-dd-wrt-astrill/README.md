# Linksys E4200 v1, DD-WRT, Astrill, And Astrill Lazy Router

This is the recovery-oriented installation guide for the known-working E4200
deployment. It separates firmware, the vendor VPN applet, and the optional
Astrill Lazy Router companion so that each layer can be tested and rolled back
independently.

## Stop Before Flashing

These images and instructions are for **Linksys E4200 version 1 only**.

- If the case label says `E4200 v2`, stop. Version 2 uses different hardware.
- Never flash a generic, `nv64k`, E4200 v2, EA-series, or unrelated router
  image.
- Perform firmware work over a wired LAN connection with Wi-Fi disabled.
- Do not flash through Astrill, remote desktop, a wireless bridge, or the WAN.
- Loss of power or the wrong image can make the router unbootable.

The DD-WRT E4200 model page requires E4200-trailed images for the initial
flash and permits only E4200-trailed or `nv60k` images afterward.

## Known-Working Reference

This exact stack was verified on 2026-07-30:

| Layer | Verified value |
| --- | --- |
| Router | Linksys E4200 v1 |
| DD-WRT | `v3.0-r62374 mega`, 2025-10-19 |
| Kernel | Linux 4.4.302 |
| Astrill applet | 2.9.52 |
| Astrill Lazy desktop package | 0.2.12 |
| Astrill Lazy router companion | 0.2.10 |

This is a pinned recovery reference, not a claim that `r62374` is the newest
DD-WRT build. Revalidate model-specific forum reports before selecting a
different build.

## Folder Map

| Path | Purpose |
| --- | --- |
| [`01-install-dd-wrt.md`](01-install-dd-wrt.md) | Stock Linksys to pinned DD-WRT |
| [`02-install-astrill.md`](02-install-astrill.md) | Official Astrill applet installation and baseline test |
| [`SHA256SUMS`](SHA256SUMS) | Expected hashes for the three pinned images |
| [`astrill-installer.example.txt`](astrill-installer.example.txt) | Redacted command shape only |
| [`astrill-lazy-control/`](astrill-lazy-control/) | Separate desktop and companion setup |

The offline Nutstore copy places these documents beside the firmware files:

```text
e4200--dd-wrt--astrill/
|-- dd-wrt.v24-21676_NEWD-2_K2.6_mini-e4200.bin
|-- dd-wrt.v24-62374_NEWD-2_K2.6_mini-e4200.bin
|-- dd-wrt.v24-62374_NEWD-2_K3.x_mega-e4200.bin
|-- README.md
|-- 01-install-dd-wrt.md
|-- 02-install-astrill.md
|-- SHA256SUMS
`-- astrill-lazy-control/
```

The firmware binaries are intentionally not committed to the application
repository. Obtain them from DD-WRT or use the separately retained private
offline bundle, then verify their hashes.

## Installation Order

1. Confirm the router is E4200 v1 and record the existing network settings.
2. Verify every firmware image with `sha256sum -c SHA256SUMS`.
3. Install the E4200-specific DD-WRT mini image.
4. Upgrade through the pinned modern K2.6 mini image to K3.x mega.
5. Configure WAN, LAN, Wi-Fi, time, and LAN-only administration.
6. Confirm ordinary Internet access before installing any VPN.
7. Install Astrill using a newly generated private command from the member
   zone.
8. Confirm Direct, connected Astrill, disconnect, and reconnect behavior.
9. Install Astrill Lazy Router in native-only, read-only mode.
10. Establish key-only SSH and inspect the native applet.
11. Back up the working baseline.
12. Explicitly enable write access and install the optional companion.
13. Apply a small Direct policy first, verify it, then add broader policies.

Do not combine firmware flashing, Astrill installation, and companion
installation into one change window.

## Official Sources

- [DD-WRT Linksys E4200 model page](https://wiki.dd-wrt.com/wiki/index.php/Linksys_E4200)
- [DD-WRT r62374 download directory](https://download1.dd-wrt.com/dd-wrtv2/downloads/betas/2025/10-19-2025-r62374/)
- [Astrill DD-WRT applet installation](https://www.astrill.com/wiki/Astrill_Setup_Manual%3AInstalling_Astrill_VPN_applet_onto_your_DD-WRT_flashed_routers)
- [Astrill router setup member page](https://www.astrill.com/member-zone/tools/router-set-up)
- [Astrill Lazy Router repository (sign-in may be required)](https://github.com/lachlanchen/astrill-lazy-router)
