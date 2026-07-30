# Policy Distribution

## Purpose

Astrill Lazy policy bundles distribute service-level Direct or Astrill
decisions without distributing router state. A bundle references only IDs in
the installed `core-catalog`; it cannot contain a device address, MAC address,
process path, SSH setting, executable command, or arbitrary destination.

The public workspace is:

```text
https://lachlanchen.github.io/astrill-lazy-policies/
```

The first stable release is `daily-balanced` version `1.0.0`, tagged
`policy-v1.0.0` in the public source repository. Its policy SHA-256 is:

```text
59cedac9c159df3deb60afe93eeed528b7b6ec006d8073a87ba04eebf69a2fbe
```

## Trust Boundary

The website is consumed by a computer, never by the router. The router does
not have to support HTTPS and does not download or execute website content.
The computer validates the JSON schema, catalog references, size and rule
limits, and exact SHA-256 before changing its local configuration.

The hash protects against accidental replacement and against a policy body
that differs from the command the operator accepted. The website and its
release metadata share one publishing account, so the digest is not an
independent defense against compromise of that account. Immutable Git tags
and a separately reviewed command provide the stronger release record.

Applying a bundle changes local policy only. It never connects Astrill,
installs the companion, opens SSH, or writes the router. Router deployment is
a separate write-guarded action.

## Inspect And Apply

Validate the published bundle without changing local configuration:

```bash
astrill-lazy policy-bundle inspect \
  https://lachlanchen.github.io/astrill-lazy-policies/policies/daily-balanced-v1.json \
  --sha256 59cedac9c159df3deb60afe93eeed528b7b6ec006d8073a87ba04eebf69a2fbe
```

Apply it to the local service library:

```bash
astrill-lazy policy-bundle apply \
  https://lachlanchen.github.io/astrill-lazy-policies/policies/daily-balanced-v1.json \
  --sha256 59cedac9c159df3deb60afe93eeed528b7b6ec006d8073a87ba04eebf69a2fbe
```

The default replaces the local service-policy set but preserves every device
and process rule. Add `--merge` to update referenced services while retaining
other local service rules.

## Export And Customize

The website supports provider-country, category, route, and text filters,
durable selection, batch Direct or Astrill assignment, stable reset, and a
local JSON download. A custom download is not a signed release. Calculate and
review its digest before applying it:

```bash
sha256sum astrill-lazy-custom-policy.json
astrill-lazy policy-bundle inspect astrill-lazy-custom-policy.json
astrill-lazy policy-bundle apply astrill-lazy-custom-policy.json \
  --sha256 REPLACE_WITH_REVIEWED_SHA256
```

Export the current local service decisions without private selectors:

```bash
astrill-lazy policy-bundle export policy.json \
  --bundle-id daily-balanced \
  --version 1.0.0
```

## Schema

Schema version 1 allows these top-level fields:

```text
schema_version bundle_id version catalog description rules
```

Each rule allows exactly:

```text
origin_id service_id route region enabled priority
```

Unknown fields, duplicate origins, duplicate services, unknown catalog IDs,
unknown regions, invalid Direct/region combinations, oversized documents, and
more than 320 rules are rejected before configuration is saved.

## Reproducible Publication

Generate catalog and release metadata from the maintained source:

```bash
python3 scripts/build-policy-site.py
pytest tests/test_policy_site.py tests/test_policy_bundle.py
```

The release gate revalidates the policy against the catalog, recomputes bytes
and SHA-256, checks the complete published catalog, and scans the public tree
for private network addresses, MAC addresses, host paths, private keys, and
Astrill installer material. GitHub Pages publishes only `site/public`.
