"""Einstiegspunkt: Argumente parsen und den Ablauf steuern."""

from __future__ import annotations

import argparse
import sys

from . import kube, prompts
from .forwarding import Supervisor
from .ports import assign_local_ports, plan


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="localports",
        description="Leitet Services ausgewählter Namespaces per port-forward "
                    "auf localhost weiter.")
    parser.add_argument("-n", "--namespace", action="append",
                        help="Namespace ohne Rückfrage verwenden (mehrfach möglich)")
    parser.add_argument("--cli", choices=list(kube.DEFAULT_CLIS),
                        help="zu verwendendes CLI (Standard: oc, sonst kubectl)")
    parser.add_argument("--address", default="127.0.0.1",
                        help="lokale Bind-Adresse (Standard: 127.0.0.1)")
    parser.add_argument("-y", "--yes", action="store_true",
                        help="alle Services ohne Rückfrage weiterleiten")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    try:
        _run(parse_args(argv))
    except KeyboardInterrupt:
        # Strg+C in einem Prompt oder während oc/kubectl läuft. Im Supervisor
        # fängt ein eigener Signal-Handler SIGINT ab und räumt auf.
        print("\nAbgebrochen.", file=sys.stderr)
        sys.exit(130)


def _run(args: argparse.Namespace) -> None:
    cli = kube.detect_cli(args.cli)
    print(f"CLI: {cli}   Kontext: {kube.current_context(cli)}")

    namespaces = args.namespace or prompts.ask_namespaces(kube.list_namespaces(cli))

    services: dict[str, list[dict]] = {}
    for ns in namespaces:
        try:
            services[ns] = kube.list_services(cli, ns)
        except RuntimeError as err:
            print(f"Services in {ns} nicht lesbar: {err}", file=sys.stderr)

    forwards, skipped = plan(services)
    prompts.print_table(forwards, skipped)
    if not forwards:
        sys.exit("\nKeine weiterleitbaren Services gefunden.")

    if not args.yes:
        forwards = prompts.ask_forwards(forwards)
    assign_local_ports(forwards, args.address)

    prompts.print_urls(forwards, args.address)
    Supervisor(cli, args.address).run(forwards)
