from __future__ import annotations

import hashlib
import tomllib
import xml.etree.ElementTree as ET
from pathlib import Path

from astrill_lazy import __version__
from astrill_lazy.installer import build_router_package, find_router_root

ROOT = Path(__file__).resolve().parents[1]


def test_application_release_identity_is_consistent() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="ascii"))
    metadata = ET.parse(
        ROOT / "data" / "io.github.lachlanchen.AstrillLazyRouter.metainfo.xml"
    )
    releases = metadata.findall("./releases/release")

    assert __version__ == "0.3.0"
    assert project["project"]["version"] == __version__
    assert releases[0].attrib == {"version": __version__, "date": "2026-07-31"}
    assert "## 0.3.0 - 2026-07-31" in (ROOT / "CHANGELOG.md").read_text(
        encoding="utf-8"
    )


def test_router_release_archive_matches_the_documented_identity() -> None:
    router_root = find_router_root()
    archive = build_router_package(router_root)

    assert (router_root / "VERSION").read_text(encoding="ascii").strip() == "0.2.12"
    assert len(archive) == 18_347
    assert (
        hashlib.md5(archive, usedforsecurity=False).hexdigest()
        == "62084ec42351966c633697d452ea1629"
    )
    assert (
        hashlib.sha256(archive).hexdigest()
        == "f8bc8ea8ec0231150f8ad6891f061674fadb8899624388211e65a3df08bee897"
    )
