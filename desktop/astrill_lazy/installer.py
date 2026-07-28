from __future__ import annotations

import base64
import gzip
import hashlib
import re
import shlex
import sys
import tarfile
from collections.abc import Iterable
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from .router import RouterClient, RouterError

PACKAGE_FILES = ("alctl", "alapi", "alpage", "VERSION")
CHUNK_SIZE = 1800
STARTUP_LINE = "nvram get astrill_lazy_bootstrap | sh;"
PAGE_COMMANDS = (
    "/tmp/astrill-lazy/alpage",
    "/tmp/astrill-lazy/alapi",
)


@dataclass(frozen=True)
class InstallResult:
    version: str
    package_bytes: int
    package_sha256: str
    nvram_chunks: int
    policy_page: int
    api_page: int
    status: dict


class RouterInstaller:
    def __init__(self, client: RouterClient) -> None:
        self.client = client
        self.router_root = find_router_root()

    def install(self) -> InstallResult:
        archive = build_router_package(self.router_root)
        encoded = base64.b64encode(archive).decode("ascii")
        chunks = tuple(_chunks(encoded, CHUNK_SIZE))
        version = (self.router_root / "VERSION").read_text(encoding="ascii").strip()
        md5 = hashlib.md5(archive, usedforsecurity=False).hexdigest()
        sha256 = hashlib.sha256(archive).hexdigest()

        old_count = _integer(self._nvram_get("astrill_lazy_pkg_count"))
        old_installed = self._nvram_get("astrill_lazy_installed") == "1"
        startup = self._nvram_get("rc_startup")
        pages = self._nvram_get("mypage_scripts").split()
        if not old_installed:
            previous_startup = startup
            previous_pages = " ".join(pages)
        else:
            previous_startup = self._nvram_get("astrill_lazy_previous_rc_startup")
            previous_pages = self._nvram_get("astrill_lazy_previous_mypage_scripts")

        if STARTUP_LINE not in startup:
            startup = f"{startup.rstrip()}\n{STARTUP_LINE}".lstrip()
        for command in PAGE_COMMANDS:
            if command not in pages:
                pages.append(command)

        bootstrap = (self.router_root / "bootstrap.sh").read_text(encoding="ascii")
        assignments: list[tuple[str, str]] = [
            ("astrill_lazy_installed", "1"),
            ("astrill_lazy_version", version),
            ("astrill_lazy_pkg_count", str(len(chunks))),
            ("astrill_lazy_pkg_md5", md5),
            ("astrill_lazy_bootstrap", bootstrap),
            ("astrill_lazy_previous_rc_startup", previous_startup),
            ("astrill_lazy_previous_mypage_scripts", previous_pages),
            ("rc_startup", startup),
            ("mypage_scripts", " ".join(pages)),
        ]
        assignments.extend(
            (f"astrill_lazy_pkg_{index}", chunk) for index, chunk in enumerate(chunks)
        )

        script = ["set -e"]
        script.extend(_nvram_set_command(key, value) for key, value in assignments)
        for index in range(len(chunks), old_count):
            script.append(f"nvram unset {shlex.quote(f'astrill_lazy_pkg_{index}')}")
        script.extend(
            [
                "nvram commit >/dev/null",
                "nvram get astrill_lazy_bootstrap | sh",
            ]
        )
        self.client.run_script("\n".join(script) + "\n", timeout=90)
        status = self.client.status()
        if (
            status.get("version") != version
            or status.get("health") != "healthy"
            or not status.get("jump_installed")
            or not status.get("watchdog")
        ):
            raise RouterError(
                "router package installed but policy runtime is not healthy"
            )

        policy_page = pages.index(PAGE_COMMANDS[0]) + 1
        api_page = pages.index(PAGE_COMMANDS[1]) + 1
        return InstallResult(
            version=version,
            package_bytes=len(archive),
            package_sha256=sha256,
            nvram_chunks=len(chunks),
            policy_page=policy_page,
            api_page=api_page,
            status=status,
        )

    def uninstall(self) -> None:
        count = _integer(self._nvram_get("astrill_lazy_pkg_count"))
        startup = self._nvram_get("rc_startup")
        startup = startup.replace(f"\n{STARTUP_LINE}", "").replace(STARTUP_LINE, "")
        pages = [
            value
            for value in self._nvram_get("mypage_scripts").split()
            if value not in PAGE_COMMANDS
        ]
        keys = [
            "astrill_lazy_installed",
            "astrill_lazy_version",
            "astrill_lazy_pkg_count",
            "astrill_lazy_pkg_md5",
            "astrill_lazy_bootstrap",
            "astrill_lazy_previous_rc_startup",
            "astrill_lazy_previous_mypage_scripts",
            "astrill_lazy_rules",
            "astrill_lazy_rules_previous",
        ]
        keys.extend(f"astrill_lazy_pkg_{index}" for index in range(count))

        script = [
            "set -e",
            "[ ! -x /tmp/astrill-lazy/alctl ] || /tmp/astrill-lazy/alctl stop",
            _nvram_set_command("rc_startup", startup.strip()),
            _nvram_set_command("mypage_scripts", " ".join(pages)),
        ]
        script.extend(f"nvram unset {shlex.quote(key)}" for key in keys)
        script.extend(
            [
                "nvram commit >/dev/null",
                "rm -f /tmp/astrill-lazy/alctl /tmp/astrill-lazy/alapi /tmp/astrill-lazy/alpage /tmp/astrill-lazy/VERSION",
            ]
        )
        self.client.run_script("\n".join(script) + "\n", timeout=45)

    def _nvram_get(self, key: str) -> str:
        if not re.fullmatch(r"[a-zA-Z0-9_]+", key):
            raise ValueError(f"invalid NVRAM key: {key!r}")
        return self.client.raw(["nvram", "get", key]).rstrip("\n")


def find_router_root() -> Path:
    package_file = Path(__file__).resolve()
    candidates = (
        package_file.parents[2] / "router",
        Path(sys.prefix) / "share" / "astrill-lazy" / "router",
        Path("/usr/local/share/astrill-lazy/router"),
        Path.home() / ".local" / "share" / "astrill-lazy" / "router",
    )
    for candidate in candidates:
        if all(
            (candidate / name).is_file() for name in (*PACKAGE_FILES, "bootstrap.sh")
        ):
            return candidate
    searched = ", ".join(str(path) for path in candidates)
    raise FileNotFoundError(f"router package files were not found in: {searched}")


def build_router_package(root: Path) -> bytes:
    output = BytesIO()
    with (
        gzip.GzipFile(
            fileobj=output, mode="wb", compresslevel=9, mtime=0
        ) as compressed,
        tarfile.open(fileobj=compressed, mode="w") as archive,
    ):
        for name in PACKAGE_FILES:
            path = root / name
            data = path.read_bytes()
            info = tarfile.TarInfo(f"astrill-lazy/{name}")
            info.size = len(data)
            info.mode = 0o700 if name != "VERSION" else 0o600
            info.uid = 0
            info.gid = 0
            info.uname = "root"
            info.gname = "root"
            info.mtime = 0
            archive.addfile(info, BytesIO(data))
    return output.getvalue()


def _chunks(value: str, size: int) -> Iterable[str]:
    for offset in range(0, len(value), size):
        yield value[offset : offset + size]


def _integer(value: str) -> int:
    try:
        return max(0, int(value))
    except ValueError:
        return 0


def _nvram_set_command(key: str, value: str) -> str:
    if not re.fullmatch(r"[a-zA-Z0-9_]+", key):
        raise ValueError(f"invalid NVRAM key: {key!r}")
    return f"nvram set {shlex.quote(f'{key}={value}')}"
