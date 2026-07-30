#!/usr/bin/env python3

from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "desktop"))

from astrill_lazy.catalog import load_catalog
from astrill_lazy.policy_bundle import PolicyBundle

PUBLIC = ROOT / "site" / "public"
POLICY = PUBLIC / "policies" / "daily-balanced-v1.json"
DATA = PUBLIC / "data"
ASSETS = PUBLIC / "assets"
PUBLIC_BASE = "https://lachlanchen.github.io/astrill-lazy-policies"


def main() -> int:
    payload = POLICY.read_bytes()
    catalog = load_catalog()
    bundle = PolicyBundle.from_bytes(payload, catalog=catalog)
    DATA.mkdir(parents=True, exist_ok=True)
    ASSETS.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / "figs" / "banner.png", ASSETS / "lazyingart-banner.png")
    services = [
        {
            "id": service.id,
            "name": service.name,
            "company": service.company,
            "provider_country": service.provider_country,
            "category": service.category,
            "profile_type": service.profile_type,
            "default_route": service.default_route.value,
            "preferred_region": service.preferred_region,
            "domains": list(service.domains),
            "aliases": list(service.aliases),
        }
        for service in catalog.services
    ]
    write_json(
        DATA / "catalog.json",
        {
            "schema_version": 1,
            "catalog": "core-catalog",
            "services": services,
        },
    )
    relative_policy = "policies/daily-balanced-v1.json"
    write_json(
        DATA / "release.json",
        {
            "schema_version": 1,
            "policy_id": bundle.bundle_id,
            "policy_version": bundle.version,
            "policy_rules": len(bundle.entries),
            "policy_bytes": len(payload),
            "policy_sha256": hashlib.sha256(payload).hexdigest(),
            "policy_url": relative_policy,
            "policy_absolute_url": f"{PUBLIC_BASE}/{relative_policy}",
            "router_boot_dependency": False,
            "contains_credentials": False,
        },
    )
    return 0


def write_json(path: Path, document: object) -> None:
    path.write_text(
        json.dumps(
            document,
            ensure_ascii=True,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="ascii",
    )


if __name__ == "__main__":
    raise SystemExit(main())
