from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path

import pytest
from astrill_lazy.astrill import AstrillEndpoint, AstrillNode, AstrillServer
from astrill_lazy.endpoint_probe import EndpointProbeResult, EndpointProbeStatus
from astrill_lazy.endpoint_probe_store import (
    FUTURE_TOLERANCE_SECONDS,
    STALE_AFTER_SECONDS,
    SavedEndpointProbe,
    SavedProbeState,
    assess_saved_endpoint_probe,
    endpoint_probe_cache_path,
    load_endpoint_probe_cache,
    save_endpoint_probe_cache,
)


def _server(
    server_id: int = 1,
    *,
    address: str = "67.43.53.5",
    mode: int = 1,
) -> AstrillServer:
    return AstrillServer(
        id=server_id,
        name=f"Server {server_id}",
        nodes=(
            AstrillNode(
                id=server_id + 100,
                weight=1,
                endpoints=(
                    AstrillEndpoint(
                        encoded_ip=536872392 + server_id,
                        port="443",
                        mode=mode,
                        protocol_code=6,
                        port_index=0,
                        resolved_ip=address,
                    ),
                ),
            ),
        ),
    )


def _saved_result(
    server_id: int,
    status: EndpointProbeStatus,
    *,
    checked_at: int = 1_800_000_000,
) -> SavedEndpointProbe:
    has_target = status is not EndpointProbeStatus.UNAVAILABLE
    return SavedEndpointProbe(
        result=EndpointProbeResult(
            server_id=server_id,
            server_name=f"Server {server_id}",
            selected_protocol=1,
            tested_protocol=1 if has_target else None,
            address="67.43.53.5" if has_target else None,
            port=443 if has_target else None,
            status=status,
            latency_ms=42.5 if status is EndpointProbeStatus.REACHABLE else None,
            detail="saved test result",
        ),
        checked_at=checked_at,
    )


def test_endpoint_probe_cache_round_trip_and_permissions(tmp_path: Path) -> None:
    path = endpoint_probe_cache_path(tmp_path / "config.json")
    cache = {
        (1, 1): _saved_result(1, EndpointProbeStatus.REACHABLE),
        (2, 1): _saved_result(2, EndpointProbeStatus.UNREACHABLE),
        (3, 1): _saved_result(3, EndpointProbeStatus.UNAVAILABLE),
    }

    save_endpoint_probe_cache(path, cache)

    assert load_endpoint_probe_cache(path) == cache
    assert json.loads(path.read_text(encoding="utf-8"))["schema_version"] == 1
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600
    assert not list(tmp_path.glob(".endpoint-latency.*.tmp"))


def test_empty_cache_removes_saved_results(tmp_path: Path) -> None:
    path = tmp_path / "endpoint-latency.json"
    save_endpoint_probe_cache(
        path,
        {(1, 1): _saved_result(1, EndpointProbeStatus.REACHABLE)},
    )

    save_endpoint_probe_cache(path, {})

    assert not path.exists()


@pytest.mark.parametrize(
    "document",
    [
        "{not-json",
        '{"schema_version":99,"results":[]}',
        '{"schema_version":1,"results":NaN}',
        "[]",
    ],
)
def test_damaged_or_unknown_cache_never_blocks_startup(
    tmp_path: Path, document: str
) -> None:
    path = tmp_path / "endpoint-latency.json"
    path.write_text(document, encoding="utf-8")

    assert load_endpoint_probe_cache(path) == {}


def test_invalid_records_are_skipped_but_valid_records_survive(
    tmp_path: Path,
) -> None:
    path = tmp_path / "endpoint-latency.json"
    valid = _saved_result(1, EndpointProbeStatus.REACHABLE)
    save_endpoint_probe_cache(path, {(1, 1): valid})
    document = json.loads(path.read_text(encoding="utf-8"))
    invalid = {**document["results"][0], "server_id": True}
    document["results"] = [invalid, document["results"][0]]
    path.write_text(json.dumps(document), encoding="utf-8")

    assert load_endpoint_probe_cache(path) == {(1, 1): valid}


def test_nonfinite_latency_is_never_saved(tmp_path: Path) -> None:
    path = tmp_path / "endpoint-latency.json"
    saved = _saved_result(1, EndpointProbeStatus.REACHABLE)
    invalid = replace(
        saved,
        result=replace(saved.result, latency_ms=float("nan")),
    )

    with pytest.raises(ValueError, match="latency_ms"):
        save_endpoint_probe_cache(path, {(1, 1): invalid})

    assert not path.exists()


def test_saved_probe_age_and_endpoint_fingerprint_are_checked() -> None:
    now = 1_800_000_000
    server = _server()
    saved = _saved_result(1, EndpointProbeStatus.REACHABLE, checked_at=now)

    assert (
        assess_saved_endpoint_probe(saved, server, 1, now=now)
        is SavedProbeState.CURRENT
    )
    assert (
        assess_saved_endpoint_probe(
            replace(saved, checked_at=now - STALE_AFTER_SECONDS - 1),
            server,
            1,
            now=now,
        )
        is SavedProbeState.STALE
    )
    assert (
        assess_saved_endpoint_probe(
            replace(saved, checked_at=now + FUTURE_TOLERANCE_SECONDS + 1),
            server,
            1,
            now=now,
        )
        is SavedProbeState.STALE
    )
    assert (
        assess_saved_endpoint_probe(
            saved,
            _server(address="67.43.53.6"),
            1,
            now=now,
        )
        is SavedProbeState.ENDPOINT_CHANGED
    )


def test_repeated_cache_key_uses_the_last_valid_record(tmp_path: Path) -> None:
    path = tmp_path / "endpoint-latency.json"
    first = _saved_result(1, EndpointProbeStatus.REACHABLE)
    second = replace(first, checked_at=first.checked_at + 10)
    save_endpoint_probe_cache(path, {(1, 1): first})
    document = json.loads(path.read_text(encoding="utf-8"))
    later = {**document["results"][0], "checked_at": second.checked_at}
    document["results"].append(later)
    path.write_text(json.dumps(document), encoding="utf-8")

    assert load_endpoint_probe_cache(path) == {(1, 1): second}
