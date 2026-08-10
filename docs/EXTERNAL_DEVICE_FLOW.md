# External Device Flow

`device-flow` creates a volatile router RAM overlay for one exact LAN device and
one or more exact destination domains. It is intended for a short operation such
as opening Google Play on a test phone while Ubuntu, Codex, the Mac, and every
other LAN client retain their existing routes.

The command requires both the device's IPv4 address and observed MAC address.
The router verifies that binding against its LAN neighbor table, resolves only
the named domains, applies only the selected TCP/UDP port, and uses generation
compare-and-swap. Broad CIDRs and wildcard domains are rejected. The overlay is
RAM-only and should be deleted as soon as the operation finishes.

```bash
astrill-lazy device-flow set \
  --owner echomind-play-mi10pro \
  --source 192.168.1.132 \
  --mac a2:04:fe:76:f4:17 \
  --domain play.googleapis.com \
  --domain play-fe.googleapis.com \
  --port 443 \
  --target vpn

astrill-lazy device-flow list --owner echomind-play-mi10pro

astrill-lazy device-flow delete --owner echomind-play-mi10pro
```

The default protocols are TCP and UDP so HTTPS and QUIC on port 443 can use the
same scope. Supply repeated `--protocol` arguments to narrow this further.
Because the DD-WRT companion routes resolved destination IPs, another hostname
temporarily sharing one of those IPs can follow the same route from that phone.
The exact source host, MAC guard, short lifetime, and immediate deletion bound
that CDN limitation.
