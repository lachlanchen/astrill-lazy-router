from __future__ import annotations

import socket
import threading

import pytest
from astrill_lazy.latency import (
    LatencyTarget,
    probe_endpoint_latencies,
    sort_endpoint_ids,
)


def test_endpoint_latency_measures_a_tcp_handshake() -> None:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]

    def accept_once() -> None:
        connection, _address = listener.accept()
        connection.close()
        listener.close()

    thread = threading.Thread(target=accept_once)
    thread.start()
    result = probe_endpoint_latencies(
        [LatencyTarget(1109, "127.0.0.1", port)],
        timeout=1,
        workers=1,
    )
    thread.join(timeout=2)

    assert thread.is_alive() is False
    assert result[1109] is not None
    assert 0 <= float(result[1109]) < 1000


def test_endpoint_latency_reports_a_refused_connection_as_no_reply() -> None:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    listener.close()

    result = probe_endpoint_latencies(
        [LatencyTarget(1, "127.0.0.1", port)],
        timeout=0.2,
        workers=1,
    )

    assert result == {1: None}


def test_endpoint_latency_rejects_duplicate_servers() -> None:
    with pytest.raises(ValueError, match="duplicate latency target"):
        probe_endpoint_latencies(
            [
                LatencyTarget(1, "192.0.2.1", 443),
                LatencyTarget(1, "192.0.2.2", 443),
            ]
        )


def test_endpoint_sort_orders_countries_in_both_directions() -> None:
    arguments = {
        "names": {1: "Tokyo 1", 2: "London 1", 3: "Tokyo 2"},
        "countries": {1: "Japan", 2: "United Kingdom", 3: "Japan"},
        "latencies": {},
        "pending": set(),
        "field": "country",
    }

    assert sort_endpoint_ids([2, 3, 1], **arguments) == [1, 3, 2]
    assert sort_endpoint_ids([2, 3, 1], descending=True, **arguments) == [2, 3, 1]


def test_endpoint_sort_orders_measured_ping_and_keeps_other_states_last() -> None:
    arguments = {
        "names": {
            1: "Measured slow",
            2: "No reply",
            3: "Pending",
            4: "Not measured",
            5: "Measured fast",
        },
        "countries": {server_id: "Test" for server_id in range(1, 6)},
        "latencies": {1: 210.0, 2: None, 3: 50.0, 5: 42.0},
        "pending": {3},
        "field": "latency",
    }

    assert sort_endpoint_ids([1, 2, 3, 4, 5], **arguments) == [5, 1, 3, 2, 4]
    assert sort_endpoint_ids([1, 2, 3, 4, 5], descending=True, **arguments) == [
        1,
        5,
        3,
        2,
        4,
    ]


def test_endpoint_sort_preserves_applet_order_by_default() -> None:
    assert sort_endpoint_ids(
        [3, 1, 2],
        names={},
        countries={},
        latencies={},
        pending=set(),
        field="applet",
    ) == [3, 1, 2]
