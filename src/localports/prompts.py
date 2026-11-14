"""Interaktion mit dem User: Auswahl-Prompts und Tabellenausgabe."""

from __future__ import annotations

import sys

from .ports import Forward, Skipped


def parse_selection(text: str, items: list[str]) -> list[str]:
    """Erlaubt '1,3,5-7', Namen oder '*'/'all'. Unbekanntes führt zu ValueError."""
    text = text.strip()
    if text in ("*", "all", "alle"):
        return list(items)
    chosen: list[str] = []
    for token in text.replace(" ", ",").split(","):
        if not token:
            continue
        if token in items:
            picks = [token]
        elif "-" in token and all(p.isdigit() for p in token.split("-", 1)):
            a, b = (int(p) for p in token.split("-", 1))
            picks = [items[i - 1] for i in range(a, b + 1) if 1 <= i <= len(items)]
        elif token.isdigit() and 1 <= int(token) <= len(items):
            picks = [items[int(token) - 1]]
        else:
            raise ValueError(token)
        chosen += [p for p in picks if p not in chosen]
    return chosen


def _input() -> str:
    try:
        return input("> ").strip()
    except EOFError:
        sys.exit(1)


def ask_namespaces(namespaces: list[str]) -> list[str]:
    shown = namespaces
    while True:
        print()
        width = len(str(len(shown)))
        for i, ns in enumerate(shown, 1):
            print(f"  {i:>{width}}) {ns}")
        print("\nNamespaces wählen (z.B. 1,3,5-7 oder Namen), '/text' filtert, '*' = alle:")
        answer = _input()
        if answer.startswith("/"):
            term = answer[1:].lower()
            shown = [n for n in namespaces if term in n.lower()] or namespaces
            continue
        if not answer:
            continue
        try:
            selected = parse_selection(answer, shown)
        except ValueError as err:
            print(f"Unbekannte Auswahl: {err}")
            continue
        if selected:
            return selected


def ask_forwards(forwards: list[Forward]) -> list[Forward]:
    print("\nWelche Services weiterleiten? (Enter = alle, z.B. 1,3,5-7)")
    keys = [str(i) for i in range(1, len(forwards) + 1)]
    while True:
        answer = _input()
        if not answer:
            return forwards
        try:
            chosen = parse_selection(answer, keys)
        except ValueError as err:
            print(f"Unbekannte Auswahl: {err}")
            continue
        if not chosen:
            print("Leere Auswahl, bitte erneut wählen.")
            continue
        return [forwards[int(k) - 1] for k in chosen]


def print_table(forwards: list[Forward], skipped: list[Skipped]) -> None:
    rows = [("#", "NAMESPACE", "SERVICE", "PORT")]
    for i, f in enumerate(forwards, 1):
        for j, p in enumerate(f.ports):
            port = f"{p.remote}" + (f" ({p.name})" if p.name else "")
            first = j == 0
            rows.append((str(i) if first else "", f.namespace if first else "",
                         f.service if first else "", port))
    print()
    _print_rows(rows)
    if skipped:
        print("\nÜbersprungen (nicht per port-forward erreichbar):")
        for ns, name, why in skipped:
            print(f"  {ns}/{name}: {why}")


def _print_rows(rows: list[tuple[str, ...]]) -> None:
    widths = [max(len(r[c]) for r in rows) for c in range(len(rows[0]))]
    for r in rows:
        print("  " + "  ".join(cell.ljust(w) for cell, w in zip(r, widths, strict=True)).rstrip())


def url_host(address: str) -> str:
    """Host für die angezeigten Adressen, abgeleitet aus `--address`.

    Bei einer Liste zählt der erste Eintrag. Wildcard- und Loopback-Adressen
    werden als `localhost` gezeigt, andere IPv6-Adressen in eckigen Klammern.
    """
    host = address.split(",", 1)[0].strip()
    if host in ("127.0.0.1", "0.0.0.0", "::", "localhost", ""):
        return "localhost"
    if ":" in host:
        return f"[{host}]"
    return host


def print_urls(forwards: list[Forward], address: str) -> None:
    """Listet die erreichbaren Adressen, http(s) als URL zum Strg+Klick im Terminal."""
    host = url_host(address)
    rows: list[tuple[str, ...]] = []
    for f in forwards:
        for j, p in enumerate(f.ports):
            target = f"{p.scheme}://{host}:{p.local}/" if p.scheme else f"{host}:{p.local}"
            label = f"{f.namespace}/{f.service}" if j == 0 else ""
            rows.append((label, target, p.name))
    print("\nPort-Forwards laufen:")
    _print_rows(rows)
    print("\nBeenden mit Strg+C.")
