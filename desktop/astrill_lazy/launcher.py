from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

from .models import MatchKind, Rule

PROFILE_RE = re.compile(r"^[a-z0-9]{1,10}$")


class LaunchError(RuntimeError):
    pass


class ApplicationLauncher:
    def __init__(self, helper: Path | None = None) -> None:
        self.helper = helper or find_helper()

    def prepare(self, rule: Rule) -> str:
        profile = profile_for_rule(rule)
        parent = default_interface()
        result = subprocess.run(
            ["pkexec", str(self.helper), "prepare", profile, parent],
            check=False,
            capture_output=True,
            text=True,
            timeout=40,
        )
        if result.returncode != 0:
            raise LaunchError(_process_error(result, "could not prepare app network"))
        try:
            document = json.loads(_last_json_line(result.stdout))
            address = str(document["address"])
        except (KeyError, ValueError, json.JSONDecodeError) as exc:
            raise LaunchError("network helper returned an invalid address") from exc
        rule.metadata["profile_id"] = profile
        rule.metadata["namespace_ip"] = address
        rule.metadata["parent_interface"] = parent
        return address

    def launch(self, rule: Rule) -> subprocess.Popen[str]:
        if rule.match_kind is not MatchKind.PROCESS:
            raise ValueError("only application rules can be launched")
        profile = profile_for_rule(rule)
        arguments = [str(item) for item in rule.metadata.get("arguments", [])]
        return subprocess.Popen(
            [
                "pkexec",
                str(self.helper),
                "launch",
                profile,
                str(os.getuid()),
                str(os.getpid()),
                rule.selector,
                *arguments,
            ],
            text=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

    def cleanup(self, rule: Rule) -> None:
        profile = profile_for_rule(rule)
        result = subprocess.run(
            ["pkexec", str(self.helper), "cleanup", profile],
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
        if result.returncode != 0:
            raise LaunchError(_process_error(result, "could not remove app network"))


def find_helper() -> Path:
    package_file = Path(__file__).resolve()
    candidates = (
        package_file.parents[2] / "helpers" / "astrill-lazy-netns",
        Path(sys.prefix) / "libexec" / "astrill-lazy-netns",
        Path("/usr/local/libexec/astrill-lazy-netns"),
        Path.home() / ".local" / "libexec" / "astrill-lazy-netns",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    searched = ", ".join(str(path) for path in candidates)
    raise FileNotFoundError(f"application network helper was not found in: {searched}")


def default_interface() -> str:
    result = subprocess.run(
        ["ip", "-4", "route", "show", "default"],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )
    if result.returncode != 0:
        raise LaunchError("could not discover the default network interface")
    fields = result.stdout.split()
    try:
        return fields[fields.index("dev") + 1]
    except (ValueError, IndexError) as exc:
        raise LaunchError("the default route has no network interface") from exc


def profile_for_rule(rule: Rule) -> str:
    configured = str(rule.metadata.get("profile_id", ""))
    if configured and PROFILE_RE.fullmatch(configured):
        return configured
    compact = re.sub(r"[^a-z0-9]", "", rule.id.casefold())
    profile = compact[-10:] or "app"
    rule.metadata["profile_id"] = profile
    return profile


def parse_command(command: str) -> tuple[str, list[str]]:
    values = shlex.split(command)
    if not values:
        raise ValueError("application command is empty")
    executable = str(Path(values[0]).expanduser().resolve())
    if not Path(executable).is_file():
        raise ValueError(f"application was not found: {executable}")
    if not os.access(executable, os.X_OK):
        raise ValueError(f"application is not executable: {executable}")
    return executable, values[1:]


def _last_json_line(output: str) -> str:
    for line in reversed(output.splitlines()):
        if line.strip().startswith("{"):
            return line.strip()
    raise ValueError("output did not contain JSON")


def _process_error(result: subprocess.CompletedProcess[Any], fallback: str) -> str:
    message = result.stderr.strip() or result.stdout.strip()
    if "dismissed" in message.casefold() or result.returncode == 126:
        return "administrator authorization was cancelled"
    return message or fallback
