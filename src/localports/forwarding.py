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
        threads = [threading.Thread(target=self._watch, args=(i, f), daemon=True)
                   for i, f in enumerate(forwards)]
        for t in threads:
            t.start()
        try:
            while any(t.is_alive() for t in threads):
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
        finally:
            self.shutdown()
            for t in threads:
                t.join(timeout=3)

    def shutdown(self) -> None:
        self.stop.set()
        with self.lock:
            procs = list(self.procs.values())
        for p in procs:
            if p.poll() is None:
                p.send_signal(signal.SIGTERM)
        for p in procs:
            try:
                p.wait(timeout=3)
            except subprocess.TimeoutExpired:
                p.kill()
        print("\nAlle Port-Forwards beendet.")
