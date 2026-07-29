#!/usr/bin/env python3

from __future__ import annotations

import argparse
import socket
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "desktop"))

from astrill_lazy.catalog import load_catalog


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate catalog structure and optional live IPv4 DNS resolution"
    )
    parser.add_argument(
        "--dns",
        action="store_true",
        help="require every unique seed domain to resolve to IPv4",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=24,
        help="parallel DNS workers (default: 24)",
    )
    return parser


def resolve_ipv4(domain: str) -> tuple[str, bool]:
    for attempt in range(3):
        try:
            socket.getaddrinfo(
                domain,
                443,
                family=socket.AF_INET,
                type=socket.SOCK_STREAM,
            )
        except OSError:
            if attempt < 2:
                time.sleep(0.2 * (attempt + 1))
        else:
            return domain, True
    return domain, False


def main() -> int:
    arguments = build_parser().parse_args()
    if not 1 <= arguments.workers <= 64:
        raise SystemExit("--workers must be between 1 and 64")

    catalog = load_catalog()
    categories = Counter(item.category for item in catalog.services)
    profile_types = Counter(item.profile_type for item in catalog.services)
    provider_countries = Counter(item.provider_country for item in catalog.services)
    all_domains = [domain for service in catalog.services for domain in service.domains]
    domain_use = Counter(all_domains)
    unique_domains = sorted(domain_use)

    print(
        f"profiles={len(catalog.services)} categories={len(categories)} "
        f"seeds={len(all_domains)} unique_seeds={len(unique_domains)} "
        f"shared_seeds={sum(count > 1 for count in domain_use.values())}"
    )
    print(
        "profile_types="
        + ",".join(f"{key}:{profile_types[key]}" for key in sorted(profile_types))
    )
    print(
        "categories="
        + ",".join(f"{key}:{categories[key]}" for key in sorted(categories))
    )
    print(
        "provider_countries="
        + ",".join(
            f"{key}:{provider_countries[key]}" for key in sorted(provider_countries)
        )
    )

    if not arguments.dns:
        return 0

    with ThreadPoolExecutor(max_workers=arguments.workers) as executor:
        failures = [
            domain
            for domain, resolved in executor.map(resolve_ipv4, unique_domains)
            if not resolved
        ]
    print(
        f"dns_resolved={len(unique_domains) - len(failures)} dns_failed={len(failures)}"
    )
    if failures:
        for domain in failures:
            print(f"unresolved: {domain}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
