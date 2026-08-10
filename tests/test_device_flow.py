from __future__ import annotations

from typing import Any

import pytest
from astrill_lazy.catalog import load_catalog
from astrill_lazy.device_flow import (
    DeviceFlowSpec,
    compile_device_flow,
    put_device_flow,
    remove_device_flow,
    summarize_device_flow,
)


class FakeRouter:
    def __init__(self, overlays: list[dict[str, Any]] | None = None) -> None:
        self.status: dict[str, Any] = {"health": "healthy", "overlays": overlays or []}
        self.put_calls: list[tuple[object, ...]] = []
        self.remove_calls: list[tuple[object, ...]] = []

    def effective_status(self) -> dict[str, Any]:
        return self.status

    def overlay_put(
        self,
        owner: str,
        expected_generation: int,
        source: str,
        rules_tsv: str,
        *,
        expected_source: str | None = None,
        expected_mac: str | None = None,
    ) -> dict[str, Any]:
        self.put_calls.append(
            (
                owner,
                expected_generation,
                source,
                rules_tsv,
                expected_source,
                expected_mac,
            )
        )
        return {
            "health": "healthy",
            "overlays": [
                {
                    "owner": owner,
                    "generation": expected_generation + 1,
                    "source": source,
                    "mac": expected_mac,
                    "rows": 4,
                    "bytes": len(rules_tsv.encode("ascii")),
                    "hash": "md5:" + "0" * 32,
                }
            ],
        }

    def overlay_remove(
        self, owner: str, expected_generation: int
    ) -> dict[str, Any]:
        self.remove_calls.append((owner, expected_generation))
        return {"health": "healthy", "overlays": []}


def play_spec() -> DeviceFlowSpec:
    return DeviceFlowSpec.create(
        owner="echomind-play-mi10pro",
        source="192.168.1.132",
        mac="A2-04-FE-76-F4-17",
        domains=["play.googleapis.com", "PLAY.GOOGLEAPIS.COM."],
        target="vpn",
    )


def test_device_flow_is_exact_host_mac_domain_and_https_protocols() -> None:
    spec = play_spec()

    assert spec.source == "192.168.1.132/32"
    assert spec.mac == "a2:04:fe:76:f4:17"
    assert spec.domains == ("play.googleapis.com",)
    assert tuple(item.value for item in spec.protocols) == ("tcp", "udp")

    payload = compile_device_flow(spec, load_catalog())
    rows = [line.split("\t") for line in payload.splitlines() if not line.startswith("#")]
    assert len(rows) == 2
    assert {row[3] for row in rows} == {"domain"}
    assert {row[4] for row in rows} == {"play.googleapis.com"}
    assert {row[5] for row in rows} == {"vpn"}
    assert {row[6] for row in rows} == {"tcp", "udp"}
    assert {row[7] for row in rows} == {"443"}


@pytest.mark.parametrize(
    ("source", "domains"),
    [
        ("192.168.1.0/24", ["play.googleapis.com"]),
        ("0.0.0.0", ["play.googleapis.com"]),
        ("192.168.1.132", ["*.googleapis.com"]),
        ("192.168.1.132", []),
    ],
)
def test_device_flow_rejects_broad_or_empty_scope(
    source: str, domains: list[str]
) -> None:
    with pytest.raises(ValueError):
        DeviceFlowSpec.create(
            owner="play-phone",
            source=source,
            mac="a2:04:fe:76:f4:17",
            domains=domains,
            target="vpn",
        )


def test_put_uses_generation_compare_and_swap_and_expected_binding() -> None:
    router = FakeRouter(
        [
            {
                "owner": "echomind-play-mi10pro",
                "generation": 3,
                "source": "192.168.1.132/32",
                "mac": "a2:04:fe:76:f4:17",
            }
        ]
    )

    status = put_device_flow(router, play_spec(), load_catalog())

    assert router.put_calls[0][0:3] == (
        "echomind-play-mi10pro",
        3,
        "192.168.1.132/32",
    )
    assert router.put_calls[0][4:] == (
        "192.168.1.132/32",
        "a2:04:fe:76:f4:17",
    )
    assert summarize_device_flow(status, "echomind-play-mi10pro") == {
        "owner": "echomind-play-mi10pro",
        "generation": 4,
        "source": "192.168.1.132/32",
        "mac": "a2:04:fe:76:f4:17",
        "rows": 4,
        "bytes": status["overlays"][0]["bytes"],
        "hash": "md5:" + "0" * 32,
    }


def test_remove_is_idempotent_and_owner_scoped() -> None:
    absent = FakeRouter()
    removed, status = remove_device_flow(absent, "echomind-play-mi10pro")
    assert removed is False
    assert status is absent.status
    assert absent.remove_calls == []

    present = FakeRouter(
        [{"owner": "echomind-play-mi10pro", "generation": 2}]
    )
    removed, status = remove_device_flow(present, "echomind-play-mi10pro")
    assert removed is True
    assert status["overlays"] == []
    assert present.remove_calls == [("echomind-play-mi10pro", 2)]
