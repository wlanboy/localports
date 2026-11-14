import pytest

from localports import app


def test_parse_args_defaults():
    args = app.parse_args([])
    assert args.namespace is None
    assert args.cli is None
    assert args.address == "127.0.0.1"
    assert args.yes is False


def test_parse_args_multiple_namespaces():
    args = app.parse_args(["-n", "a", "--namespace", "b", "--cli", "kubectl", "-y",
                           "--address", "0.0.0.0"])
    assert args.namespace == ["a", "b"]
    assert args.cli == "kubectl"
    assert args.yes is True
    assert args.address == "0.0.0.0"


def test_parse_args_rejects_unknown_cli():
    with pytest.raises(SystemExit):
        app.parse_args(["--cli", "helm"])


def _svc(name, port=80):
    return {"metadata": {"name": name},
            "spec": {"selector": {"app": name}, "ports": [{"port": port}]}}


def _must_not_be_called(*_a, **_k):
    raise AssertionError("darf nicht aufgerufen werden")


@pytest.fixture
def env(monkeypatch):
    """Ersetzt kube, Prompts und Supervisor. Liefert die Aufzeichnungen zurück."""
    rec = {"services": {"ns1": [_svc("web")], "ns2": [_svc("db", 5432)]},
           "runs": []}

    def list_services(_cli, ns):
        result = rec["services"][ns]
        if isinstance(result, Exception):
            raise result
        return result

    class FakeSupervisor:
        def __init__(self, cli, address):
            self.cli, self.address = cli, address

        def run(self, forwards):
            rec["runs"].append((self.cli, self.address, forwards))

    def assign(forwards, _address):
        for f in forwards:
            for p in f.ports:
                p.local = p.remote

    monkeypatch.setattr(app.kube, "detect_cli", lambda _preferred: "oc")
    monkeypatch.setattr(app.kube, "current_context", lambda _cli: "ctx")
    monkeypatch.setattr(app.kube, "list_namespaces", lambda _cli: ["ns1", "ns2"])
    monkeypatch.setattr(app.kube, "list_services", list_services)
    monkeypatch.setattr(app, "Supervisor", FakeSupervisor)
    monkeypatch.setattr(app, "assign_local_ports", assign)
    return rec


def _forwarded(rec):
    (_cli, _address, forwards), = rec["runs"]
    return [(f.namespace, f.service, [p.local for p in f.ports]) for f in forwards]


def test_run_with_namespaces_and_yes_skips_prompts(env, monkeypatch, capsys):
    monkeypatch.setattr(app.prompts, "ask_namespaces", _must_not_be_called)
    monkeypatch.setattr(app.prompts, "ask_forwards", _must_not_be_called)

    app.main(["-n", "ns1", "-n", "ns2", "-y", "--address", "0.0.0.0"])

    assert _forwarded(env) == [("ns1", "web", [80]), ("ns2", "db", [5432])]
    assert env["runs"][0][:2] == ("oc", "0.0.0.0")
    assert "Kontext: ctx" in capsys.readouterr().out


def test_run_asks_for_namespaces_and_forwards(env, monkeypatch):
    asked = {}

    def ask_namespaces(namespaces):
        asked["namespaces"] = namespaces
        return ["ns1", "ns2"]

    def ask_forwards(forwards):
        asked["forwards"] = [f.service for f in forwards]
        return forwards[1:]

    monkeypatch.setattr(app.prompts, "ask_namespaces", ask_namespaces)
    monkeypatch.setattr(app.prompts, "ask_forwards", ask_forwards)

    app.main([])

    assert asked == {"namespaces": ["ns1", "ns2"], "forwards": ["web", "db"]}
    assert _forwarded(env) == [("ns2", "db", [5432])]


def test_run_continues_when_namespace_unreadable(env, capsys):
    env["services"]["ns1"] = RuntimeError("forbidden")

    app.main(["-n", "ns1", "-n", "ns2", "-y"])

    assert _forwarded(env) == [("ns2", "db", [5432])]
    assert "Services in ns1 nicht lesbar: forbidden" in capsys.readouterr().err


def test_run_exits_without_forwardable_services(env):
    env["services"]["ns1"] = []

    with pytest.raises(SystemExit, match="Keine weiterleitbaren Services"):
        app.main(["-n", "ns1", "-y"])
    assert env["runs"] == []


def test_main_exits_130_on_keyboard_interrupt(monkeypatch, capsys):
    def interrupted(_args):
        raise KeyboardInterrupt

    monkeypatch.setattr(app, "_run", interrupted)
    with pytest.raises(SystemExit) as exc:
        app.main([])
    assert exc.value.code == 130
    assert "Abgebrochen" in capsys.readouterr().err
