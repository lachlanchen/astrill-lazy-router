# Direct Device CONNECT Proxy

`astrill-lazy-direct-proxy` is a manual, temporary relay for one exact LAN
device and exact hostname when ordinary DNS selects an unreachable provider
edge. It never starts, discovers, or uses Astrill. The host stays on its normal
direct route.

The relay accepts HTTPS `CONNECT` only, permits TCP port 443 only, allows exact
private source IPv4 addresses, rejects IP-literal and private destinations, and
preserves end-to-end TLS. Every destination hostname must be explicitly
allowlisted. Overrides select another public IP for the same TLS hostname; they
do not decrypt or rewrite provider traffic.

Example:

```bash
astrill-lazy-direct-proxy \
  --listen-host 0.0.0.0 \
  --listen-port 18080 \
  --allow-source 192.168.1.132 \
  --allow-host firebaseinstallations.googleapis.com \
  --allow-host fcmregistrations.googleapis.com \
  --override firebaseinstallations.googleapis.com=142.251.170.95 \
  --override fcmregistrations.googleapis.com=142.251.170.95
```

Set the external device's HTTP proxy to the host LAN address and port only for
the bounded task. Record its prior proxy state first and restore that exact
state in a trap or equivalent cleanup path. Stop this relay immediately after
the task. Do not expose the listening port outside a trusted LAN.

Some operating-system services and embedded SDK transports ignore the user
configured HTTP proxy. Treat a successful hostname probe as route calibration,
not proof that the target application path used the relay. Confirm the intended
traffic reaches the relay before drawing a product-behavior conclusion. This
tool is not a VPN, DNS replacement, transparent router, or general browser
proxy.

The proxy logs only source address, CONNECT hostname, port, and selected public
endpoint. It does not log TLS payloads, URLs inside TLS, credentials, tokens, or
response bodies.
