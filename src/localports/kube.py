"""Aufrufe von oc/kubectl: CLI erkennen, Namespaces und Services lesen."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys

DEFAULT_CLIS = ("oc", "kubectl")


def detect_cli(preferred: str | None) -> str:
    candidates = [preferred] if preferred else list(DEFAULT_CLIS)
    for name in candidates:
        if shutil.which(name):
            return name
    sys.exit(f"Fehler: {' oder '.join(candidates)} nicht im PATH gefunden.")


def run(cli: str, *args: str) -> str:
    res = subprocess.run([cli, *args], capture_output=True, text=True)
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
        if shutil.which("oc"):
            try:
                return sorted(run("oc", "projects", "-q").split())
            except RuntimeError:
                pass
        sys.exit(f"Namespaces konnten nicht geladen werden:\n{err}")


def list_services(cli: str, namespace: str) -> list[dict]:
    return json.loads(run(cli, "get", "services", "-n", namespace, "-o", "json"))["items"]
