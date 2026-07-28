# Backup And Restore

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
