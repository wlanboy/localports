"""Startet und überwacht die port-forward-Prozesse."""

from __future__ import annotations

import signal
import subprocess
import sys
import threading
import time

from .ports import Forward

MAX_BACKOFF = 30.0
STABLE_AFTER = 30.0

# Signale, bei denen die Forwards sauber beendet werden. Die Kinder laufen in
# einer eigenen Session und bekommen SIGINT/SIGHUP vom Terminal nicht mit.
STOP_SIGNALS = [s for s in (signal.SIGINT, getattr(signal, "SIGTERM", None),
                            getattr(signal, "SIGHUP", None)) if s is not None]


class _Interrupted(Exception):
    def __init__(self, signum: int):
        super().__init__(signum)
        self.signum = signum


def _ignore_signal(_signum: int, _frame: object) -> None:
    # Bewusst nicht SIG_IGN: Das würde an gerade startende Kinder vererbt, die
    # dann SIGTERM ignorieren. Python-Handler setzt exec auf den Standard zurück.
    pass


def _interrupt(signum: int, _frame: object) -> None:
    # Weitere Signale sofort stummschalten. Sonst bricht ein zweites das
    # Aufräumen ab, etwa wenn SIGHUP direkt und zusätzlich über `uv run` ankommt.
    for s in STOP_SIGNALS:
        signal.signal(s, _ignore_signal)
    raise _Interrupted(signum)


def _terminate(procs: list[subprocess.Popen]) -> None:
    for p in procs:
        if p.poll() is None:
            p.send_signal(signal.SIGTERM)
    for p in procs:
        try:
            p.wait(timeout=3)
        except subprocess.TimeoutExpired:
            p.kill()


class Supervisor:
    def __init__(self, cli: str, address: str):
        self.cli = cli
        self.address = address
        self.stop = threading.Event()
        self.procs: dict[int, subprocess.Popen] = {}
        self.lock = threading.Lock()

    def _cmd(self, f: Forward) -> list[str]:
        return [self.cli, "port-forward", "-n", f.namespace, f"svc/{f.service}",
                "--address", self.address,
                *(f"{p.local}:{p.remote}" for p in f.ports)]

    def _watch(self, idx: int, f: Forward) -> None:
        label = f"{f.namespace}/{f.service}"
        delay = 1.0
        while not self.stop.is_set():
            started = time.monotonic()
            proc = subprocess.Popen(self._cmd(f), stdout=subprocess.DEVNULL,
                                    stderr=subprocess.PIPE, text=True,
                                    start_new_session=True)
            with self.lock:
                self.procs[idx] = proc
                # shutdown() setzt stop vor dem Kopieren der Liste. Ist stop hier
                # gesetzt, hat es diesen Prozess womöglich nicht mehr gesehen.
                late = self.stop.is_set()
            if late:
                _terminate([proc])
                return
            assert proc.stderr is not None
            for line in proc.stderr:
                line = line.strip()
                if line and not self.stop.is_set():
                    print(f"[{label}] {line}", file=sys.stderr)
            proc.wait()
            if self.stop.is_set():
                return
            # Lief der Forward eine Weile stabil, Backoff zurücksetzen.
            stable = time.monotonic() - started > STABLE_AFTER
            delay = 1.0 if stable else min(delay * 2, MAX_BACKOFF)
            print(f"[{label}] beendet (Code {proc.returncode}), Neustart in {delay:.0f}s",
                  file=sys.stderr)
            self.stop.wait(delay)

    def run(self, forwards: list[Forward]) -> None:
        # Handler vor dem ersten Popen setzen, sonst bliebe ein früh startender
        # Forward bei SIGTERM/SIGHUP verwaist.
        previous = {s: signal.signal(s, _interrupt) for s in STOP_SIGNALS}
        threads = [threading.Thread(target=self._watch, args=(i, f), daemon=True)
                   for i, f in enumerate(forwards)]
        exit_code = 0
        try:
            for t in threads:
                t.start()
            while any(t.is_alive() for t in threads):
                time.sleep(0.5)
        except _Interrupted as err:
            if err.signum != signal.SIGINT:
                exit_code = 128 + err.signum  # Shell-Konvention, z.B. 143 für SIGTERM
        finally:
            for s in STOP_SIGNALS:
                signal.signal(s, _ignore_signal)
            self.shutdown()
            for t in threads:
                t.join(timeout=3)
            for s, handler in previous.items():
                signal.signal(s, handler)
        if exit_code:
            raise SystemExit(exit_code)

    def shutdown(self) -> None:
        self.stop.set()
        with self.lock:
            procs = list(self.procs.values())
        _terminate(procs)
        try:
            print("\nAlle Port-Forwards beendet.")
        except OSError:
            pass  # Terminal schon geschlossen (SIGHUP)
