"""Aufrufe von oc/kubectl: CLI erkennen, Namespaces und Services lesen."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys

DEFAULT_CLIS = ("oc", "kubectl")

# Obergrenze für einzelne oc/kubectl-Aufrufe (nicht für port-forward). Fängt
# auch Hänger ab, die --request-timeout nicht erfasst, etwa ein Login-Plugin.
TIMEOUT = 20


def detect_cli(preferred: str | None) -> str:
    candidates = [preferred] if preferred else list(DEFAULT_CLIS)
    for name in candidates:
        if shutil.which(name):
            return name
    sys.exit(f"Fehler: {' oder '.join(candidates)} nicht im PATH gefunden.")


def run(cli: str, *args: str) -> str:
    cmd = [cli, "--request-timeout=10s", *args]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"{cli} {' '.join(args)}: keine Antwort nach {TIMEOUT}s "
                           "(Cluster erreichbar, Login gültig?)") from None
    if res.returncode != 0:
        raise RuntimeError(res.stderr.strip() or f"{cli} {' '.join(args)} fehlgeschlagen")
    return res.stdout


def current_context(cli: str) -> str:
    try:
        return run(cli, "config", "current-context").strip()
    except RuntimeError:
        return "?"


def list_namespaces(cli: str) -> list[str]:
    try:
        out = run(cli, "get", "namespaces", "-o", "jsonpath={.items[*].metadata.name}")
        return sorted(out.split())
    except RuntimeError as err:
        # OpenShift: normale User dürfen oft keine Namespaces listen, aber Projekte.
        # Nur mit oc, wer --cli kubectl wählt, bekommt kein oc untergeschoben.
        if cli == "oc":
            try:
                return sorted(run(cli, "projects", "-q").split())
            except RuntimeError:
                pass
        sys.exit(f"Namespaces konnten nicht geladen werden:\n{err}")


def list_services(cli: str, namespace: str) -> list[dict]:
    return json.loads(run(cli, "get", "services", "-n", namespace, "-o", "json"))["items"]
