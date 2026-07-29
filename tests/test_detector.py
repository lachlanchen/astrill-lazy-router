from __future__ import annotations

from astrill_lazy.catalog import load_catalog
from astrill_lazy.detector import RouteProbe, detect_rules, recommend_target
from astrill_lazy.models import MatchKind, RouteTarget, Rule


class ProbeRouter:
    def run_script(self, script: str, *, timeout: int) -> str:
        assert "uuyc.163.com" in script
        assert "/round-trip/ { print $4; exit }" in script
        assert "/^rtt / { print $5; exit }" in script
        assert timeout == 20
        return "uuyc.163.com\t59.111.44.89\t97.6\t344.0\n"


def test_detection_uses_service_source_and_pins_minimum_bypass() -> None:
    rule = Rule(
        id="uu-remote-direct",
        name="UU Remote",
        match_kind=MatchKind.SERVICE,
        selector="uu-remote",
        target=RouteTarget.VPN,
    )

    recommendations = detect_rules(
        ProbeRouter(),  # type: ignore[arg-type]
        [rule],
        load_catalog(),
    )

    assert recommendations[0].target is RouteTarget.DIRECT
    assert recommendations[0].reason == "Minimum bypass"
    assert recommendations[0].probe.direct_ms == 97.6
    assert recommendations[0].probe.astrill_ms == 344.0


def test_route_recommendation_requires_a_meaningful_improvement() -> None:
    probe = RouteProbe("example.com", "203.0.113.1", 171.8, 175.9)
    target, reason = recommend_target(probe, current=RouteTarget.DIRECT)
    assert target is RouteTarget.DIRECT
    assert reason == "Paths are effectively equal"

    faster = RouteProbe("example.com", "203.0.113.1", 35.0, 220.0)
    target, reason = recommend_target(faster, current=RouteTarget.VPN)
    assert target is RouteTarget.DIRECT
    assert reason == "Direct is faster"


def test_route_recommendation_keeps_current_when_paths_cannot_be_compared() -> None:
    direct_only = RouteProbe("example.com", "203.0.113.1", 35.0, None)
    assert recommend_target(direct_only, current=RouteTarget.VPN) == (
        RouteTarget.VPN,
        "Paths could not be compared",
    )

    no_probe = RouteProbe("example.com", "", None, None)
    assert recommend_target(no_probe, current=RouteTarget.VPN) == (
        RouteTarget.VPN,
        "No reliable probe",
    )


def test_service_profile_prevents_latency_only_access_regression() -> None:
    google_like = RouteProbe("example.com", "203.0.113.1", 35.0, 220.0)
    assert recommend_target(
        google_like,
        current=RouteTarget.VPN,
        preferred=RouteTarget.VPN,
    ) == (RouteTarget.VPN, "Astrill service profile")
