import errno
import os
import socket

import pytest

from localports import ports
from localports.ports import PortMap, guess_scheme, pick_local_port, plan


@pytest.mark.parametrize(("port", "name", "app", "expected"), [
    # appProtocol hat Vorrang vor Name und Portnummer
    (8080, "grpc", "https", "https"),
    (443, "", "kubernetes.io/h2c", "http"),
    (8080, "", "kubernetes.io/ws", "http"),
    (8080, "", "kubernetes.io/wss", "https"),
    (8080, "wss-events", "", "https"),
    (8080, "ws", "", "http"),
    (80, "http", "grpc", None),
    # Port-Name nach Istio-Konvention
    (8080, "https-web", "", "https"),
    (9000, "metrics", "", "http"),
    (8080, "grpc-api", "", None),
    (443, "HTTP-legacy", "", "http"),
    # Portnummer als letzte Quelle
    (8443, "", "", "https"),
    (5432, "", "", None),
    (8080, "", "", "http"),
    (12345, "unknown", "", "http"),
])
def test_guess_scheme(port, name, app, expected):
    assert guess_scheme(port, name, app) == expected


def _svc(name, ports, selector=True, type_="ClusterIP"):
    spec = {"type": type_, "ports": ports}
    if selector:
        spec["selector"] = {"app": name}
    return {"metadata": {"name": name}, "spec": spec}


def test_plan_skips_unforwardable_services():
    services = {"ns": [
        _svc("web", [{"port": 80, "name": "http"}, {"port": 53, "protocol": "UDP"}]),
        _svc("ext", [{"port": 80}], type_="ExternalName"),
        _svc("headless", [{"port": 80}], selector=False),
        _svc("udp-only", [{"port": 53, "protocol": "UDP"}]),
    ]}
    forwards, skipped = plan(services)

    assert [(f.namespace, f.service) for f in forwards] == [("ns", "web")]
    assert forwards[0].ports == [PortMap(80, "http", "http")]
    assert skipped == [
        ("ns", "web:53", "UDP"),
        ("ns", "ext", "ExternalName"),
        ("ns", "headless", "kein Selector"),
        ("ns", "udp-only:53", "UDP"),
    ]


def _fake_port_free(busy=(), privileged_denied=True):
    def port_free(port, _address):
        if privileged_denied and port < 1024:
            raise PermissionError(port)
        return port not in busy
    return port_free


def test_pick_local_port_takes_wanted_port(monkeypatch):
    monkeypatch.setattr(ports, "port_free", _fake_port_free())
    taken: set[int] = set()
    assert pick_local_port(8080, taken, "127.0.0.1") == 8080
    assert taken == {8080}


def test_pick_local_port_skips_busy_and_taken(monkeypatch):
    monkeypatch.setattr(ports, "port_free", _fake_port_free(busy={8080}))
    taken = {8081}
    assert pick_local_port(8080, taken, "127.0.0.1") == 8082


def test_pick_local_port_offsets_on_permission_error(monkeypatch):
    monkeypatch.setattr(ports, "port_free", _fake_port_free())
    taken: set[int] = set()
    assert pick_local_port(80, taken, "127.0.0.1") == 8080
    assert pick_local_port(80, taken, "127.0.0.1") == 8081
    assert pick_local_port(443, taken, "127.0.0.1") == 8443


def test_pick_local_port_keeps_privileged_port_when_allowed(monkeypatch):
    monkeypatch.setattr(ports, "port_free", _fake_port_free(privileged_denied=False))
    assert pick_local_port(80, set(), "127.0.0.1") == 80


def test_pick_local_port_wraps_around(monkeypatch):
    monkeypatch.setattr(ports, "port_free", _fake_port_free(busy={65535}))
    assert pick_local_port(65535, set(), "127.0.0.1") == 10000


def test_plan_multiple_namespaces_and_port_details():
    services = {
        "a": [_svc("web", [{"port": 8443, "appProtocol": "https"}, {"port": 9090}])],
        "b": [_svc("empty", []), _svc("db", [{"port": 5432, "protocol": "TCP"}])],
    }
    forwards, skipped = plan(services)

    assert [(f.namespace, f.service) for f in forwards] == [("a", "web"), ("b", "db")]
    assert forwards[0].ports == [PortMap(8443, "", "https"), PortMap(9090, "", "http")]
    assert forwards[1].ports == [PortMap(5432, "", None)]
    # Service ohne Ports wird weder weitergeleitet noch als übersprungen gemeldet.
    assert skipped == []


def test_plan_handles_service_without_spec():
    forwards, skipped = plan({"ns": [{"metadata": {"name": "bare"}}]})
    assert forwards == []
    assert skipped == [("ns", "bare", "kein Selector")]


def test_assign_local_ports_shares_taken_ports(monkeypatch):
    monkeypatch.setattr(ports, "port_free", _fake_port_free(busy={9000}))
    forwards = [
        ports.Forward("a", "web", [PortMap(8080), PortMap(80)]),
        ports.Forward("b", "web", [PortMap(8080), PortMap(9000)]),
    ]
    ports.assign_local_ports(forwards, "127.0.0.1")

    assert [[p.local for p in f.ports] for f in forwards] == [[8080, 8081], [8082, 9001]]


def test_port_free_detects_listening_socket():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen()
        port = s.getsockname()[1]
        assert ports.port_free(port, "127.0.0.1") is False


def test_port_free_true_for_unused_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    assert ports.port_free(port, "127.0.0.1") is True


@pytest.mark.parametrize("address", ["myhost", "127.0.0.1,", "127.0.0.1,nope"])
def test_port_free_rejects_non_ip(address):
    with pytest.raises(ports.AddressError, match="keine IP-Adresse"):
        ports.port_free(0, address)


def test_port_free_rejects_non_local_address():
    # TEST-NET-1 (RFC 5737), auf keinem Rechner konfiguriert
    with pytest.raises(ports.AddressError, match="lässt sich nicht binden"):
        ports.port_free(0, "192.0.2.1")


def _fake_try_bind(results):
    """Liefert je IP das Ergebnis aus `results` (None = frei, sonst errno)."""
    seen = []

    def try_bind(ip, _port):
        seen.append(ip)
        code = results.get(ip)
        return None if code is None else OSError(code, os.strerror(code))
    return try_bind, seen


def test_port_free_localhost_needs_only_one_address(monkeypatch):
    # IPv6 abgeschaltet: ::1 lässt sich nicht binden, 127.0.0.1 reicht.
    try_bind, seen = _fake_try_bind({"::1": errno.EADDRNOTAVAIL})
    monkeypatch.setattr(ports, "_try_bind", try_bind)
    assert ports.port_free(8080, "localhost") is True
    assert seen == ["127.0.0.1", "::1"]


def test_port_free_localhost_fails_when_both_fail(monkeypatch):
    code = errno.EADDRNOTAVAIL
    try_bind, _ = _fake_try_bind({"127.0.0.1": code, "::1": code})
    monkeypatch.setattr(ports, "_try_bind", try_bind)
    with pytest.raises(ports.AddressError):
        ports.port_free(8080, "localhost")


def test_port_free_busy_on_any_address(monkeypatch):
    try_bind, _ = _fake_try_bind({"::1": errno.EADDRINUSE})
    monkeypatch.setattr(ports, "_try_bind", try_bind)
    assert ports.port_free(8080, "localhost") is False


def test_port_free_checks_every_address_in_list(monkeypatch):
    try_bind, seen = _fake_try_bind({})
    monkeypatch.setattr(ports, "_try_bind", try_bind)
    assert ports.port_free(8080, "127.0.0.1, localhost") is True
    assert seen == ["127.0.0.1", "127.0.0.1", "::1"]


def test_try_bind_reports_missing_address_family(monkeypatch):
    # IPv6 komplett aus dem Kernel entfernt: schon socket() schlägt fehl.
    def no_socket(*_a):
        raise OSError(errno.EAFNOSUPPORT, "Address family not supported")

    monkeypatch.setattr(ports.socket, "socket", no_socket)
    err = ports._try_bind("::1", 8080)
    assert isinstance(err, OSError) and err.errno == errno.EAFNOSUPPORT


def test_pick_local_port_gives_up_when_nothing_free(monkeypatch):
    monkeypatch.setattr(ports, "port_free", lambda _p, _a: False)
    with pytest.raises(ports.AddressError, match="kein freier lokaler Port"):
        pick_local_port(8080, set(), "127.0.0.1")


def test_check_address_accepts_loopback():
    ports.check_address("127.0.0.1")


def test_port_free_propagates_permission_error(monkeypatch):
    class DeniedSocket:
        def __init__(self, *_a):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

        def bind(self, _addr):
            raise PermissionError("denied")

    monkeypatch.setattr(ports.socket, "socket", DeniedSocket)
    with pytest.raises(PermissionError):
        ports.port_free(80, "127.0.0.1")
