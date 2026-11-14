import os
import signal
import subprocess
import sys
from typing import Any, cast

import pytest

from localports import forwarding
from localports.forwarding import Supervisor
from localports.ports import Forward, PortMap


def _as_popen(proc: "FakeProc") -> subprocess.Popen[Any]:
    return cast("subprocess.Popen[Any]", proc)


class FakeProc:
    def __init__(self, lines=(), returncode=1, running=True, hangs=False):
        self.stderr = iter(lines)
        self.returncode = returncode
        self.running = running
        self.hangs = hangs
        self.signals: list[int] = []
        self.killed = False

    def poll(self):
        return None if self.running else self.returncode

    def send_signal(self, signum):
        self.signals.append(signum)

    def wait(self, timeout: float | None = None):
        if self.hangs:
            raise subprocess.TimeoutExpired("fake", timeout or 0)
        self.running = False
        return self.returncode

    def kill(self):
        self.killed = True


def _forward():
    return Forward("ns", "web", [PortMap(80, local=8080), PortMap(443, local=8443)])


def test_cmd():
    assert Supervisor("oc", "127.0.0.1")._cmd(_forward()) == [
        "oc", "port-forward", "-n", "ns", "svc/web", "--address", "127.0.0.1",
        "8080:80", "8443:443",
    ]


def test_terminate_signals_only_running_and_kills_hanging():
    running = FakeProc()
    finished = FakeProc(running=False)
    hanging = FakeProc(hangs=True)
    forwarding._terminate([_as_popen(p) for p in (running, finished, hanging)])

    assert running.signals == [signal.SIGTERM]
    assert finished.signals == []
    assert hanging.signals == [signal.SIGTERM]
    assert not running.killed
    assert hanging.killed


def test_interrupt_silences_further_signals(monkeypatch):
    installed = {}
    monkeypatch.setattr(forwarding.signal, "signal",
                        lambda s, h: installed.__setitem__(s, h))
    with pytest.raises(forwarding._Interrupted) as exc:
        forwarding._interrupt(signal.SIGTERM, None)

    assert exc.value.signum == signal.SIGTERM
    assert installed == {s: forwarding._ignore_signal for s in forwarding.STOP_SIGNALS}


def test_shutdown_terminates_all_procs(capsys):
    sup = Supervisor("oc", "127.0.0.1")
    fakes = [FakeProc(), FakeProc()]
    sup.procs = {i: _as_popen(p) for i, p in enumerate(fakes)}
    sup.shutdown()

    assert sup.stop.is_set()
    assert all(p.signals == [signal.SIGTERM] for p in fakes)
    assert "Alle Port-Forwards beendet" in capsys.readouterr().out


def _fake_popen(monkeypatch, make_proc):
    """Ersetzt Popen, sammelt die Kommandos und liefert `make_proc()`."""
    cmds = []

    def popen(cmd, **_kwargs):
        cmds.append(cmd)
        return make_proc()

    monkeypatch.setattr(forwarding.subprocess, "Popen", popen)
    return cmds


def _stop_after_waits(monkeypatch, sup, count):
    """Ersetzt stop.wait, sammelt die Backoff-Zeiten und stoppt nach `count`."""
    delays = []

    def wait(delay):
        delays.append(delay)
        if len(delays) == count:
            sup.stop.set()

    monkeypatch.setattr(sup.stop, "wait", wait)
    return delays


def _fake_clock(monkeypatch, durations):
    """Jeder Lauf dauert der Reihe nach so viele Sekunden wie in `durations`."""
    ticks = []
    for d in durations:
        ticks += [0.0, float(d)]
    it = iter(ticks)
    monkeypatch.setattr(forwarding.time, "monotonic", lambda: next(it))


def test_watch_restarts_with_exponential_backoff(monkeypatch, capsys):
    sup = Supervisor("oc", "127.0.0.1")
    cmds = _fake_popen(monkeypatch, lambda: FakeProc(lines=["  boom \n", "\n"]))
    delays = _stop_after_waits(monkeypatch, sup, 6)
    _fake_clock(monkeypatch, [0] * 6)

    sup._watch(0, _forward())

    assert delays == [2, 4, 8, 16, 30, 30]
    assert len(cmds) == 6
    err = capsys.readouterr().err
    assert "[ns/web] boom\n" in err
    assert "[ns/web] beendet (Code 1), Neustart in 2s" in err


def test_watch_resets_backoff_after_stable_run(monkeypatch):
    sup = Supervisor("oc", "127.0.0.1")
    _fake_popen(monkeypatch, FakeProc)
    delays = _stop_after_waits(monkeypatch, sup, 4)
    _fake_clock(monkeypatch, [0, 0, forwarding.STABLE_AFTER + 1, 0])

    sup._watch(0, _forward())

    assert delays == [2, 4, 1, 2]


def test_watch_stops_without_restart_when_stopped_during_run(monkeypatch):
    sup = Supervisor("oc", "127.0.0.1")

    def make_proc():
        proc = FakeProc(running=False)
        original_wait = proc.wait

        def wait(timeout=None):
            sup.stop.set()  # shutdown() während der Prozess läuft
            return original_wait(timeout)

        proc.wait = wait
        return proc

    cmds = _fake_popen(monkeypatch, make_proc)
    delays = _stop_after_waits(monkeypatch, sup, 99)

    sup._watch(0, _forward())

    assert len(cmds) == 1
    assert delays == []


def test_watch_terminates_proc_started_after_shutdown(monkeypatch):
    sup = Supervisor("oc", "127.0.0.1")
    procs = []

    def make_proc():
        # shutdown() hat stop gesetzt, nachdem der Thread die Schleife betreten hat
        sup.stop.set()
        procs.append(FakeProc())
        return procs[-1]

    _fake_popen(monkeypatch, make_proc)
    sup._watch(0, _forward())

    assert procs[0].signals == [signal.SIGTERM]
    assert sup.procs == {0: procs[0]}


def test_run_without_forwards_restores_handlers(capsys):
    before = {s: signal.getsignal(s) for s in forwarding.STOP_SIGNALS}
    Supervisor("oc", "127.0.0.1").run([])

    assert {s: signal.getsignal(s) for s in forwarding.STOP_SIGNALS} == before
    assert "Alle Port-Forwards beendet" in capsys.readouterr().out


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX-Signale")
@pytest.mark.parametrize(("signum", "exit_code"), [
    (signal.SIGINT, None),
    (signal.SIGTERM, 128 + signal.SIGTERM),
])
def test_run_exit_code_on_signal(monkeypatch, capsys, signum, exit_code):
    sup = Supervisor("oc", "127.0.0.1")
    before = {s: signal.getsignal(s) for s in forwarding.STOP_SIGNALS}

    def fake_watch(_idx, _f):
        os.kill(os.getpid(), signum)
        sup.stop.wait(5)

    monkeypatch.setattr(sup, "_watch", fake_watch)
    if exit_code is None:
        sup.run([_forward()])
    else:
        with pytest.raises(SystemExit) as exc:
            sup.run([_forward()])
        assert exc.value.code == exit_code

    assert sup.stop.is_set()
    assert {s: signal.getsignal(s) for s in forwarding.STOP_SIGNALS} == before
