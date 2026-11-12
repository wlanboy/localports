"""Port-Planung: welcher Service-Port landet auf welchem lokalen Port."""

from __future__ import annotations

import os
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
    local: int
    remote: int
    name: str = ""
    scheme: str | None = None  # "http", "https" oder None für Nicht-HTTP


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


def port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def pick_local_port(wanted: int, taken: set[int]) -> int:
    port = wanted
    if port < 1024 and os.geteuid() != 0:
        port += PRIVILEGED_OFFSET
    while port in taken or not port_free(port):
        port += 1
        if port > 65535:
            port = 10000
    taken.add(port)
    return port


def plan(services: dict[str, list[dict]]) -> tuple[list[Forward], list[Skipped]]:
    forwards: list[Forward] = []
    skipped: list[Skipped] = []
    taken: set[int] = set()
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
                local = pick_local_port(p["port"], taken)
                name_ = p.get("name", "")
                scheme = guess_scheme(p["port"], name_, p.get("appProtocol", ""))
                fwd.ports.append(PortMap(local, p["port"], name_, scheme))
            if fwd.ports:
                forwards.append(fwd)
    return forwards, skipped
