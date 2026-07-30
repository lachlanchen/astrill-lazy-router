# Backup And Restore

## Pre-0.2.12 Upgrade Snapshot

The live router was captured immediately before the planned companion
`0.2.10` to `0.2.12` upgrade:

```text
.private-backups/20260731-013246-pre-0.2.12
backups/astrill-router-backup-20260731-013246-pre-0.2.12.cms
```

The private snapshot contains complete NVRAM, the existing companion and
Astrill runtime archive, controller status, the 5,959-byte effective rule
document, firewall/routing/interface/process/memory/DNS state, Ubuntu
configuration, router public key, and Ubuntu/macOS connectivity baselines.
The installed `0.2.10` companion does not implement `effective-status`; its
ordinary status, rule document, runtime files, and NVRAM policy were captured
instead.

The snapshot has 15 archive entries. Its verified identities are:

```text
plaintext bytes: 113260
plaintext SHA-256: 9462bcc4064eaee74d04e18cd8e5b11c7f922f9602d20289e191b407525d8d65
ciphertext bytes: 113822
ciphertext SHA-256: ab9c28f67521c5d4afdb0f64d15ff66d41d56d69061a815ffd0a9bd0155d98c9
```

CMS decryption was tested with the existing mode `0600` private key and the
result compared byte-for-byte with the Git-ignored plaintext archive. The
temporary decrypted copy was removed after verification.

## Private DHCP Change Record

The current static DHCP migration is intentionally not committed because it
contains device MAC addresses. Its before state, requested table, validated
applied table, guarded apply script, restore script, and after verification
are stored locally with mode `0700` under:

```text
~/Documents/Private Router/static-leases-2026-07-29
```

The router had no static leases before the migration. The restore script
returns to that captured empty static table and restarts only dnsmasq; it does
not change Astrill, firewall, SSH, WAN, or companion state.

## Snapshot

The private snapshot was captured before installing the router plugin:

```text
/home/lachlan/Projects/astrill-lazy/.private-backups/20260728-165627
```

It includes the live Astrill runtime, unpacked applet, OpenVPN binary and
configuration, logs, relevant NVRAM, iptables, policy routing, DNS files, router
inventory, and the downloaded installer response used for analysis.

That directory and its plaintext tar are ignored by Git.

## Encrypted Repository Copy

```text
backups/astrill-router-backup-20260728-165627.cms
backups/backup-recipient.crt
```

- Format: OpenSSL CMS EnvelopedData, DER
- Content cipher: AES-256-CBC
- Ciphertext size: 1,119,022 bytes
- Ciphertext SHA-256:
  `72a61bdb88e692ac0c208b01e925a275727e47349fbf5e4f089f29452abaf316`

The certificate is public. The private decryption key is outside the
repository:

```text
~/.config/astrill-lazy/backup-private.pem
```

It is mode `0600` and must be backed up separately. Losing it makes the CMS file
unrecoverable.

## Verify And Decrypt

```bash
openssl cms -decrypt -binary -inform DER \
  -in backups/astrill-router-backup-20260728-165627.cms \
  -inkey ~/.config/astrill-lazy/backup-private.pem \
  -out /tmp/astrill-router-backup.tar.gz

sha256sum /tmp/astrill-router-backup.tar.gz
tar -tzf /tmp/astrill-router-backup.tar.gz
```

The verified plaintext tar SHA-256 is:

```text
5138cb40a2b09dee664b5a5543c71729547bc6e2ff393ce55697c03425f7f756
```

The encryption workflow was tested by decrypting the committed CMS file and
comparing it byte-for-byte with the source tar. The archive contains 23 entries.

## Recreate The Ciphertext

```bash
tar -czf .private-backups/astrill-router-backup-20260728-165627.tar.gz \
  -C .private-backups 20260728-165627

openssl cms -encrypt -binary -aes-256-cbc \
  -in .private-backups/astrill-router-backup-20260728-165627.tar.gz \
  -outform DER \
  -out backups/astrill-router-backup-20260728-165627.cms \
  backups/backup-recipient.crt
```

CMS encryption is randomized, so recreating it changes the ciphertext hash even
when plaintext is identical.

## Restore Guidance

Do not blindly write the snapshot over a running router. Firmware, WAN state,
and Astrill account state may have changed.

For plugin recovery, use `astrill-lazy uninstall-router` first. For Astrill
recovery:

1. decrypt and inspect the snapshot offline;
2. compare current firmware and applet versions;
3. restore only the required NVRAM values or runtime files;
4. keep an authenticated Telnet or SSH recovery session open;
5. verify WAN and LAN access before committing NVRAM.

The raw installer download contains credential material. Never paste it into an
issue, commit, CI log, or shell history on a shared host.
