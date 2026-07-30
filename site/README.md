# Astrill Lazy Policy Site

This directory is the reproducible source for the public, token-free policy
workspace published from `lachlanchen/astrill-lazy-policies`.

Only catalog service metadata, catalog-relative routing decisions, public
documentation links, and portable-agent source are eligible for publication.
Device addresses, MAC addresses, executable paths, SSH material, Astrill
installer URLs/tokens, local manifests, and router backups are prohibited.

Build the generated data before publishing:

```bash
python3 scripts/build-policy-site.py
```

The generated `site/public/data/release.json` records the exact SHA-256 of the
stable policy document used by the URL import command.
