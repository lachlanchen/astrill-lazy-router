from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from astrill_lazy.catalog import load_catalog
from astrill_lazy.policy_bundle import PolicyBundle

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "site" / "public"
POLICY = PUBLIC / "policies" / "daily-balanced-v1.json"
RELEASE = PUBLIC / "data" / "release.json"
CATALOG = PUBLIC / "data" / "catalog.json"


def test_published_policy_release_is_exact_and_catalog_bound() -> None:
    catalog = load_catalog()
    payload = POLICY.read_bytes()
    bundle = PolicyBundle.from_bytes(payload, catalog=catalog)
    release = json.loads(RELEASE.read_text(encoding="ascii"))

    assert bundle.bundle_id == "daily-balanced"
    assert bundle.version == "1.0.0"
    assert len(bundle.entries) == 88
    assert {
        "uu-remote",
        "wechat",
        "taobao",
        "meituan",
        "nutstore",
    } <= {entry.service_id for entry in bundle.entries}
    assert release["policy_sha256"] == hashlib.sha256(payload).hexdigest()
    assert release["policy_bytes"] == len(payload)
    assert release["policy_rules"] == len(bundle.entries)
    assert release["policy_absolute_url"].startswith("https://")
    assert release["router_boot_dependency"] is False
    assert release["contains_credentials"] is False


def test_published_catalog_matches_the_bundled_core_catalog() -> None:
    catalog = load_catalog()
    document = json.loads(CATALOG.read_text(encoding="ascii"))
    published = {service["id"]: service for service in document["services"]}

    assert document["schema_version"] == 1
    assert set(published) == set(catalog.services_by_id)
    for service_id, service in catalog.services_by_id.items():
        assert published[service_id]["name"] == service.name
        assert published[service_id]["company"] == service.company
        assert published[service_id]["provider_country"] == service.provider_country
        assert published[service_id]["domains"] == list(service.domains)


def test_public_site_contains_no_host_or_router_secrets() -> None:
    text_files = [
        path
        for path in PUBLIC.rglob("*")
        if path.is_file() and path.suffix in {"", ".css", ".html", ".js", ".json"}
    ]
    contents = "\n".join(
        path.read_text(encoding="utf-8", errors="strict") for path in text_files
    )
    forbidden = {
        "password assignment": re.compile(
            r"\b(?:router_)?password\s*[:=]",
            re.IGNORECASE,
        ),
        "private key": re.compile(r"OPENSSH PRIVATE KEY"),
        "private host path": re.compile(r"/home/lachlan(?:/|\\b)"),
        "private IPv4 address": re.compile(
            r"\b(?:10|127)\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"
            r"|\b192\.168\.\d{1,3}\.\d{1,3}\b"
            r"|\b172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}\b"
        ),
        "MAC address": re.compile(r"\b(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}\b"),
        "Astrill installer": re.compile(r"astroutercn\.com/router/install/"),
    }

    for label, pattern in forbidden.items():
        assert not pattern.search(contents), f"public site contains {label}"


def test_site_bootstrap_command_requires_an_exact_hash() -> None:
    script = (PUBLIC / "app.js").read_text(encoding="ascii")

    assert "policy-bundle apply" in script
    assert "--sha256 ${state.release.policy_sha256}" in script
    assert "Router deployment remains a separate confirmed action." in (
        PUBLIC / "index.html"
    ).read_text(encoding="ascii")
