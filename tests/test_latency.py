from __future__ import annotations

import socket
import threading

import pytest
from astrill_lazy.latency import LatencyTarget, probe_endpoint_latencies


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
