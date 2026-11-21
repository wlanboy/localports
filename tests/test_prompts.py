import pytest

from localports import prompts
from localports.ports import Forward, PortMap
from localports.prompts import parse_selection, url_host

ITEMS = ["a", "b", "c", "d", "e"]


@pytest.mark.parametrize(("text", "expected"), [
    ("1", ["a"]),
    ("1,3", ["a", "c"]),
    ("1 3", ["a", "c"]),
    ("2-4", ["b", "c", "d"]),
    ("4-9", ["d", "e"]),
    ("c,a", ["c", "a"]),
    ("1,1,1-2", ["a", "b"]),
    ("*", ITEMS),
    ("alle", ITEMS),
    ("5-3", []),
    ("", []),
])
def test_parse_selection(text, expected):
    assert parse_selection(text, ITEMS) == expected


@pytest.mark.parametrize("text", ["0", "6", "x", "1-x", "-2"])
def test_parse_selection_rejects_unknown(text):
    with pytest.raises(ValueError):
        parse_selection(text, ITEMS)


def test_ask_forwards_repeats_on_empty_selection(monkeypatch, capsys):
    forwards = [Forward("ns", "a"), Forward("ns", "b"), Forward("ns", "c")]
    answers = iter(["3-1", "2"])
    monkeypatch.setattr(prompts, "_input", lambda: next(answers))

    assert prompts.ask_forwards(forwards) == [forwards[1]]
    assert "Leere Auswahl" in capsys.readouterr().out


@pytest.mark.parametrize(("address", "expected"), [
    ("127.0.0.1", "localhost"),
    ("0.0.0.0", "localhost"),
    ("::", "localhost"),
    ("localhost", "localhost"),
    ("192.168.1.10", "192.168.1.10"),
    ("::1", "[::1]"),
    ("fd00::5", "[fd00::5]"),
    ("10.0.0.1,::1", "10.0.0.1"),
])
def test_url_host(address, expected):
    assert url_host(address) == expected


def test_print_urls_brackets_ipv6(capsys):
    fwd = Forward("ns", "web", [PortMap(80, "http", "http", local=8080),
                                PortMap(5432, "pg", None, local=5432)])
    prompts.print_urls([fwd], "::1")
    out = capsys.readouterr().out
    assert "http://[::1]:8080/" in out
    assert "[::1]:5432" in out


NAMESPACES = ["dev", "prod", "prod-eu"]


def _answers(monkeypatch, *answers):
    it = iter(answers)
    monkeypatch.setattr(prompts, "_input", lambda: next(it))


def test_ask_namespaces_by_number_and_name(monkeypatch):
    _answers(monkeypatch, "1,prod-eu")
    assert prompts.ask_namespaces(NAMESPACES) == ["dev", "prod-eu"]


def test_ask_namespaces_filter_renumbers(monkeypatch, capsys):
    _answers(monkeypatch, "/PROD", "2")
    assert prompts.ask_namespaces(NAMESPACES) == ["prod-eu"]
    # Nach dem Filter steht prod-eu an Position 2.
    assert "2) prod-eu" in capsys.readouterr().out


def test_ask_namespaces_filter_without_match_shows_all(monkeypatch, capsys):
    _answers(monkeypatch, "/xyz", "1")
    assert prompts.ask_namespaces(NAMESPACES) == ["dev"]
    assert "Kein Namespace enthält 'xyz', zeige alle." in capsys.readouterr().out


def test_ask_namespaces_repeats_on_empty_invalid_and_empty_selection(monkeypatch, capsys):
    _answers(monkeypatch, "", "staging", "3-1", "*")
    assert prompts.ask_namespaces(NAMESPACES) == NAMESPACES
    out = capsys.readouterr().out
    assert "Unbekannte Auswahl: staging" in out
    assert "Leere Auswahl, bitte erneut wählen." in out


def test_ask_forwards_enter_selects_all(monkeypatch):
    forwards = [Forward("ns", "a"), Forward("ns", "b")]
    _answers(monkeypatch, "")
    assert prompts.ask_forwards(forwards) is forwards


def test_ask_forwards_repeats_on_invalid(monkeypatch, capsys):
    forwards = [Forward("ns", "a"), Forward("ns", "b")]
    _answers(monkeypatch, "3", "2,1")
    assert prompts.ask_forwards(forwards) == [forwards[1], forwards[0]]
    assert "Unbekannte Auswahl: 3" in capsys.readouterr().out


def test_input_strips(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _prompt: "  1,2  ")
    assert prompts._input() == "1,2"


def test_input_exits_on_eof(monkeypatch):
    def eof(_prompt):
        raise EOFError

    monkeypatch.setattr("builtins.input", eof)
    with pytest.raises(SystemExit) as exc:
        prompts._input()
    assert exc.value.code == 1


def test_print_table(capsys):
    forwards = [
        Forward("ns", "web", [PortMap(80, "http"), PortMap(443, "https")]),
        Forward("other", "db", [PortMap(5432)]),
    ]
    prompts.print_table(forwards, [("ns", "dns:53", "UDP")])

    lines = capsys.readouterr().out.splitlines()
    assert lines[1:5] == [
        "  #  NAMESPACE  SERVICE  PORT",
        "  1  ns         web      80 (http)",
        "                         443 (https)",
        "  2  other      db       5432",
    ]
    assert "Übersprungen (nicht per port-forward erreichbar):" in lines
    assert "  ns/dns:53: UDP" in lines


def test_print_table_without_skipped(capsys):
    prompts.print_table([Forward("ns", "web", [PortMap(80)])], [])
    assert "Übersprungen" not in capsys.readouterr().out


def test_print_urls_schemes_and_labels(capsys):
    forwards = [
        Forward("ns", "web", [PortMap(443, "https", "https", local=8443),
                              PortMap(9090, "", "http", local=9090)]),
        Forward("ns", "db", [PortMap(5432, "pg", None, local=5432)]),
    ]
    prompts.print_urls(forwards, "127.0.0.1")
    lines = capsys.readouterr().out.splitlines()
    assert lines[2:5] == [
        "  ns/web  https://localhost:8443/  https",
        "          http://localhost:9090/",
        "  ns/db   localhost:5432           pg",
    ]
