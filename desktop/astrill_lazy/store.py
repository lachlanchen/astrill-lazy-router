from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from .models import MatchKind, RouteTarget, Rule

SCHEMA_VERSION = 1


class ConfigStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (
            Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
            / "astrill-lazy"
            / "config.json"
        )
        self.router_host = "astrill-router"
        self.rules: list[Rule] = []
        self.active_region = "active-astrill"
        self.enabled_extensions = ["core-catalog"]
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            self.rules = [default_uu_rule()]
            return
        with self.path.open("r", encoding="utf-8") as handle:
            document = json.load(handle)
        if document.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("unsupported desktop configuration schema")
        self.router_host = str(document.get("router_host", "astrill-router"))
        self.active_region = str(document.get("active_region", "active-astrill"))
        self.enabled_extensions = [
            str(item) for item in document.get("enabled_extensions", ["core-catalog"])
        ]
        if "core-catalog" not in self.enabled_extensions:
            self.enabled_extensions.insert(0, "core-catalog")
        self.rules = [Rule.from_dict(item) for item in document.get("rules", [])]

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        document: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "router_host": self.router_host,
            "active_region": self.active_region,
            "enabled_extensions": self.enabled_extensions,
            "rules": [rule.to_dict() for rule in self.rules],
        }
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".config.", suffix=".tmp", dir=self.path.parent
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(document, handle, ensure_ascii=True, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary_path, 0o600)
            os.replace(temporary_path, self.path)
        finally:
            temporary_path.unlink(missing_ok=True)


def default_uu_rule() -> Rule:
    return Rule(
        id="uu-remote-direct",
        name="UU Remote",
        match_kind=MatchKind.SERVICE,
        selector="uu-remote",
        target=RouteTarget.DIRECT,
        region="direct",
        enabled=True,
        priority=100,
    )
