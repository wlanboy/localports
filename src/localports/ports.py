"""Port-Planung: welcher Service-Port landet auf welchem lokalen Port."""

from __future__ import annotations

import socket
from dataclasses import dataclass, field

PRIVILEGED_OFFSET = 8000

HTTPS_PREFIXES = {"https", "tls"}
HTTP_PREFIXES = {"http", "http2", "h2c", "web", "metrics", "status"}
NON_HTTP_PREFIXES = {"grpc", "tcp", "udp", "dns", "sctp", "ssh", "smtp", "ldap",
                     "postgres", "postgresql", "mysql", "mariadb", "mssql", "oracle",
                     "mongo", "mongodb", "redis", "cassandra",
                     "kafka", "zookeeper", "zk", "amqp", "amqps", "mqtt", "mqtts",
                     "stomp", "nats"}
HTTPS_PORTS = {443, 6443, 8443, 9443}
NON_HTTP_PORTS = {
    22, 25, 53, 389, 636,                     # ssh, smtp, dns, ldap
    1433, 1521, 3306, 5432, 9042, 26257,      # mssql, oracle, mysql, postgres, cassandra, cockroach
    6379, 11211, 27017,                       # redis, memcached, mongodb
    5671, 5672, 1883, 8883, 61613, 61616,     # amqp(s), mqtt(s), stomp, activemq
    4222, 6222,                               # nats
    9092, 9093, 9094, 2181, 2888, 3888,       # kafka, zookeeper
}


@dataclass
class PortMap:
    remote: int
    name: str = ""
    scheme: str | None = None  # "http", "https" oder None für Nicht-HTTP
    local: int = 0             # wird erst nach der Auswahl vergeben


@dataclass
class Forward:
    namespace: str
    service: str
    ports: list[PortMap] = field(default_factory=list)


def guess_scheme(port: int, name: str, app_protocol: str) -> str | None:
    """Rät das Protokoll aus appProtocol, Port-Name (Istio-Konvention) und Portnummer."""
    app = app_protocol.lower()
    if app:
        if "https" in app or "tls" in app:
            return "https"
        if "http" in app or "h2c" in app:
            return "http"
        return None
    prefix = name.lower().split("-", 1)[0]
    if prefix in HTTPS_PREFIXES:
        return "https"
    if prefix in HTTP_PREFIXES:
        return "http"
    if prefix in NON_HTTP_PREFIXES:
        return None
    if port in HTTPS_PORTS:
        return "https"
    if port in NON_HTTP_PORTS:
        return None
    return "http"


# (Namespace, Service bzw. Service:Port, Grund)
Skipped = tuple[str, str, str]


def port_free(port: int, address: str) -> bool:
    """Prüft, ob sich der Port auf allen Adressen binden lässt.

    `address` ist wie bei `port-forward --address` eine kommagetrennte Liste,
    `localhost` steht für 127.0.0.1 und ::1. Nicht auflösbare Einträge werden
    übersprungen, den Fehler meldet dann `port-forward` selbst. Fehlt die
    Berechtigung (privilegierter Port), wird PermissionError weitergereicht.
    """
    for host in address.split(","):
        try:
            infos = socket.getaddrinfo(host.strip(), port, type=socket.SOCK_STREAM,
                                       flags=socket.AI_PASSIVE)
        except socket.gaierror:
            continue
        for family, type_, proto, _, sockaddr in infos:
            with socket.socket(family, type_, proto) as s:
                try:
                    s.bind(sockaddr)
                except PermissionError:
                    raise
                except OSError:
                    return False
    return True


def pick_local_port(wanted: int, taken: set[int], address: str) -> int:
    """Nimmt den gewünschten Port oder den nächsten freien.

    Ob ein Port unter 1024 erlaubt ist, entscheidet ein Bind-Versuch und nicht
    die UID: Unter Linux kann ip_unprivileged_port_start gesenkt sein, unter
    Windows gibt es keine UID. Erst bei PermissionError wird PRIVILEGED_OFFSET
    addiert.
    """
    port = wanted
    while True:
        if port not in taken:
            try:
                if port_free(port, address):
                    break
            except PermissionError:
                if port < 1024:
                    port += PRIVILEGED_OFFSET
                    continue
        port += 1
        if port > 65535:
            port = 10000
    taken.add(port)
    return port


def plan(services: dict[str, list[dict]]) -> tuple[list[Forward], list[Skipped]]:
    """Ermittelt die weiterleitbaren Service-Ports, noch ohne lokale Ports."""
    forwards: list[Forward] = []
    skipped: list[Skipped] = []
    for ns, items in services.items():
        for svc in items:
            name = svc["metadata"]["name"]
            spec = svc.get("spec", {})
            if spec.get("type") == "ExternalName":
                skipped.append((ns, name, "ExternalName"))
                continue
            if not spec.get("selector"):
                skipped.append((ns, name, "kein Selector"))
                continue
            fwd = Forward(ns, name)
            for p in spec.get("ports", []):
                if p.get("protocol", "TCP") != "TCP":
                    skipped.append((ns, f"{name}:{p['port']}", p.get("protocol", "?")))
                    continue
                name_ = p.get("name", "")
                scheme = guess_scheme(p["port"], name_, p.get("appProtocol", ""))
                fwd.ports.append(PortMap(p["port"], name_, scheme))
            if fwd.ports:
                forwards.append(fwd)
    return forwards, skipped


def assign_local_ports(forwards: list[Forward], address: str) -> None:
    """Vergibt die lokalen Ports. Erst nach der Auswahl aufrufen, damit nicht
    gewählte Services keine Ports blockieren."""
    taken: set[int] = set()
    for f in forwards:
        for p in f.ports:
            p.local = pick_local_port(p.remote, taken, address)
