from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "helpers" / "astrill-lazy-netns"


def test_network_namespace_helper_has_valid_shell_syntax() -> None:
    result = subprocess.run(
        ["/bin/sh", "-n", str(HELPER)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_restricted_profile_is_fail_closed_and_destination_limited() -> None:
    source = HELPER.read_text(encoding="ascii")
    start = source.index("restrict_profile() {")
    end = source.index("\n}\n\nexecute_profile()", start)
    restrict = source[start:end]

    assert 'valid_port_list "$ports"' in restrict
    assert 'valid_ipv4 "$address"' in restrict
    assert '-m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT' in restrict
    assert '-d "$server" -p udp --dport 53 -j ACCEPT' in restrict
    assert '-d "$address" -p tcp --dport "$port" -j ACCEPT' in restrict
    assert 'iptables -w 5 -P OUTPUT DROP' in restrict
    assert restrict.index('-d "$address" -p tcp --dport "$port" -j ACCEPT') < (
        restrict.index("iptables -w 5 -P OUTPUT DROP")
    )


def test_profile_hosts_are_validated_and_private_to_the_namespace() -> None:
    source = HELPER.read_text(encoding="ascii")
    start = source.index("pin_profile_hosts() {")
    end = source.index("\n}\n\nrestrict_profile()", start)
    pin_hosts = source[start:end]

    assert 'valid_domain "$domain"' in pin_hosts
    assert 'valid_ipv4 "$address"' in pin_hosts
    assert 'valid_host_name "$host_name"' in pin_hosts
    assert '"$hosts_dir/hosts"' in pin_hosts
    assert 'printf \'%s %s\\n\' "$address" "$domain"' in pin_hosts
