# Extensions

Extensions add declarative service and region catalogs. They do not run code on
the router.

## Search Paths

In precedence order:

1. directories in `ASTRILL_LAZY_EXTENSION_PATH`;
2. `~/.local/share/astrill-lazy/extensions`;
3. the source checkout `extensions`;
4. the active Python prefix under `share/astrill-lazy/extensions`.

The first valid manifest for an extension ID wins. Only extensions enabled in
the desktop configuration are merged. `core-catalog` is always enabled.

## Layout

```text
example-catalog/
  manifest.json
  services-core.json
  services-extra.json
  regions.json
```

`manifest.json`:

```json
{
  "schema_version": 1,
  "id": "example-catalog",
  "name": "Example Catalog",
  "version": "1.0.0",
  "minimum_app_version": "0.2.0",
  "capabilities": [
    "catalog.services",
    "catalog.regions"
  ],
  "entrypoints": {
    "services": [
      "services-core.json",
      "services-extra.json"
    ],
    "regions": "regions.json"
  }
}
```

Either entrypoint can be omitted, and each can name one file or an ordered list
of files. Paths must remain inside the extension directory. IDs must match the
directory name. Catalog files use schema version 1 and arrays named `services`
or `regions`; duplicate JSON keys and duplicate IDs are rejected.

## Service Entry

```json
{
  "id": "example-service",
  "name": "Example Service",
  "company": "Example Company",
  "category": "Work",
  "profile_type": "app",
  "default_route": "vpn",
  "preferred_region": "singapore",
  "domains": [
    "example.com",
    "examplecdn.com"
  ],
  "aliases": [
    "Example"
  ],
  "source": "https://example.com/"
}
```

Profile types are `company`, `app`, or `website`. Domains are seed hostnames,
not wildcard expressions. Every domain, source URL, preferred region, and
route/region combination is validated before the extension loads. A service
can contain at most 16 unique seed hosts.

Run the full catalog audit with:

```bash
python scripts/validate-catalog.py
python scripts/validate-catalog.py --dns
```

The live DNS mode retries transient failures and requires every unique seed to
publish an IPv4 address usable by this router.

## Region Entry

```json
{
  "id": "example-region",
  "name": "Example Region",
  "kind": "astrill",
  "match": [
    "Token in Astrill server name"
  ]
}
```

Region kinds are `direct`, `vpn`, or `astrill`.

## Merge And Upgrade

Extensions load after the core catalog in configured order. A later service or
region with the same ID replaces the earlier declaration. This permits a local
catalog to add domains or change defaults without forking the application.

The GUI refuses to disable an extension while a source rule still depends on a
service that would disappear. Router rules continue to work after an extension
upgrade until the desktop applies a newly compiled document.

The router package itself is a companion plugin, not a catalog extension.
Catalog output is compiled into the stable TSV contract, so adding a provider
does not increase router code or NVRAM package size.
