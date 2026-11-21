import subprocess

import pytest

from localports import kube


def _completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


@pytest.mark.parametrize(("preferred", "available", "expected"), [
    (None, {"oc", "kubectl"}, "oc"),
    (None, {"kubectl"}, "kubectl"),
    ("kubectl", {"oc", "kubectl"}, "kubectl"),
])
def test_detect_cli(monkeypatch, preferred, available, expected):
    monkeypatch.setattr(kube.shutil, "which", lambda name: name if name in available else None)
    assert kube.detect_cli(preferred) == expected


def test_detect_cli_exits_without_cli(monkeypatch):
    monkeypatch.setattr(kube.shutil, "which", lambda _name: None)
    with pytest.raises(SystemExit, match="oc oder kubectl"):
        kube.detect_cli(None)


def test_detect_cli_does_not_fall_back_from_preferred(monkeypatch):
    monkeypatch.setattr(kube.shutil, "which", lambda name: name if name == "oc" else None)
    with pytest.raises(SystemExit, match="kubectl nicht im PATH"):
        kube.detect_cli("kubectl")


def test_run_passes_request_timeout_and_returns_stdout(monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return _completed(stdout="out\n")

    monkeypatch.setattr(kube.subprocess, "run", fake_run)
    assert kube.run("oc", "get", "pods") == "out\n"
    cmd, kwargs = calls[0]
    assert cmd == ["oc", "--request-timeout=10s", "get", "pods"]
    assert kwargs["timeout"] == kube.TIMEOUT


def test_run_raises_with_stderr(monkeypatch):
    monkeypatch.setattr(kube.subprocess, "run",
                        lambda *_a, **_k: _completed(1, stderr="  forbidden \n"))
    with pytest.raises(RuntimeError, match="^forbidden$"):
        kube.run("oc", "get", "pods")


def test_run_raises_generic_message_without_stderr(monkeypatch):
    monkeypatch.setattr(kube.subprocess, "run", lambda *_a, **_k: _completed(1))
    with pytest.raises(RuntimeError, match="oc get pods fehlgeschlagen"):
        kube.run("oc", "get", "pods")


def test_run_converts_timeout(monkeypatch):
    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])

    monkeypatch.setattr(kube.subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match="keine Antwort"):
        kube.run("oc", "get", "pods")


def test_current_context(monkeypatch):
    monkeypatch.setattr(kube, "run", lambda *_a: "my-cluster\n")
    assert kube.current_context("oc") == "my-cluster"


def test_current_context_falls_back_on_error(monkeypatch):
    def fail(*_a):
        raise RuntimeError("no context")

    monkeypatch.setattr(kube, "run", fail)
    assert kube.current_context("oc") == "?"


def test_list_namespaces_sorted(monkeypatch):
    monkeypatch.setattr(kube, "run", lambda *_a: "prod dev  test\n")
    assert kube.list_namespaces("kubectl") == ["dev", "prod", "test"]


def _run_failing_namespaces(projects: str | None):
    """`get namespaces` schlägt fehl, `oc projects` liefert `projects` oder auch Fehler."""
    def fake_run(cli, *args):
        if args[:2] == ("get", "namespaces"):
            raise RuntimeError("forbidden")
        if (cli, *args) == ("oc", "projects", "-q") and projects is not None:
            return projects
        raise RuntimeError("unexpected")
    return fake_run


def test_list_namespaces_falls_back_to_oc_projects(monkeypatch):
    monkeypatch.setattr(kube, "run", _run_failing_namespaces("b a\n"))
    assert kube.list_namespaces("oc") == ["a", "b"]


def test_list_namespaces_no_oc_fallback_for_kubectl(monkeypatch):
    # oc wäre vorhanden und lieferte Projekte, wird bei kubectl aber nicht genutzt.
    monkeypatch.setattr(kube, "run", _run_failing_namespaces("a\n"))
    monkeypatch.setattr(kube.shutil, "which", lambda name: name)
    with pytest.raises(SystemExit, match="forbidden"):
        kube.list_namespaces("kubectl")


def test_list_namespaces_exits_when_projects_fail_too(monkeypatch):
    monkeypatch.setattr(kube, "run", _run_failing_namespaces(None))
    # Gemeldet wird der ursprüngliche Fehler, nicht der von `oc projects`.
    with pytest.raises(SystemExit, match="forbidden"):
        kube.list_namespaces("oc")


def test_list_services_returns_items(monkeypatch):
    calls = []

    def fake_run(*args):
        calls.append(args)
        return '{"items": [{"metadata": {"name": "web"}}]}'

    monkeypatch.setattr(kube, "run", fake_run)
    assert kube.list_services("oc", "ns") == [{"metadata": {"name": "web"}}]
    assert calls == [("oc", "get", "services", "-n", "ns", "-o", "json")]
