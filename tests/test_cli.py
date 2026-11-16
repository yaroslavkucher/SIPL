"""Інтеграційні тести командного рядка: коди завершення та JSON-контракт."""

import json
import subprocess
import sys

import pytest

from conftest import EXAMPLES
from sipl.cli import main, parse_state

ENVELOPE = {"tool", "version", "command", "ok", "diagnostics", "result"}


def run(capsys, *argv):
    code = main([str(a) for a in argv])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def run_json(capsys, *argv):
    code, out, _ = run(capsys, *argv, "--json")
    data = json.loads(out)
    assert set(data) == ENVELOPE and data["tool"] == "sipl"
    return code, data


def test_check_ok_and_error(capsys):
    code, out, _ = run(capsys, "check", EXAMPLES / "power.sipl")
    assert code == 0 and "синтаксично коректна" in out
    code, out, err = run(capsys, "check", EXAMPLES / "invalid" / "02_trailing_semicolon.sipl")
    assert code == 1 and out == ""
    assert "02_trailing_semicolon.sipl:3:9: помилка [E004]" in err and "^" in err


def test_check_json(capsys):
    code, data = run_json(capsys, "check", EXAMPLES / "gcd.sipl")
    assert code == 0 and data["ok"] and data["diagnostics"] == []
    assert data["result"]["variables"] == ["A", "B"]
    code, data = run_json(capsys, "check", EXAMPLES / "invalid" / "08_less_than.sipl")
    assert code == 1 and not data["ok"] and data["result"] is None
    (diag,) = data["diagnostics"]
    assert diag["code"] == "E003" and diag["severity"] == "error" and diag["hint"]
    assert (diag["span"]["line"], diag["span"]["column"]) == (3, 11)


def test_json_flag_before_command(capsys):
    code = main(["--json", "check", str(EXAMPLES / "skip.sipl")])
    assert code == 0 and json.loads(capsys.readouterr().out)["command"] == "check"


def test_parse_formats(capsys):
    code, data = run_json(capsys, "parse", EXAMPLES / "max.sipl")
    assert code == 0 and data["result"]["ast"]["body"]["type"] == "If"
    code, out, _ = run(capsys, "parse", EXAMPLES / "max.sipl", "--format", "source")
    assert code == 0 and out.startswith("begin\n  if X > Y then")
    code, out, _ = run(capsys, "parse", EXAMPLES / "max.sipl")
    assert out.splitlines()[0].startswith("Program")


@pytest.mark.parametrize("fmt, marker", [
    ("text", "<програма>"), ("json", '"label": "<програма>"'), ("dot", "digraph sipl {"),
    ("svg", "<svg xmlns"), ("html", "<!doctype html>"),
])
def test_tree_formats(capsys, fmt, marker):
    code, out, _ = run(capsys, "tree", EXAMPLES / "sum.sipl", "--format", fmt)
    assert code == 0 and marker in out


def test_tree_output_file_and_views(capsys, tmp_path):
    target = tmp_path / "tree.html"
    code, out, _ = run(capsys, "tree", EXAMPLES / "sum.sipl", "--view", "both", "--format",
                       "html", "-o", target)
    assert code == 0 and "записано" in out
    assert target.read_text(encoding="utf-8").count("<svg") == 2
    code, _, err = run(capsys, "tree", EXAMPLES / "sum.sipl", "--view", "both", "--format", "svg")
    assert code == 2 and "--view" in err
    code, data = run_json(capsys, "tree", EXAMPLES / "sum.sipl", "--view", "both")
    assert set(data["result"]) == {"parse_tree", "ast"}


def test_term(capsys):
    code, out, _ = run(capsys, "term", EXAMPLES / "power.sipl", "--style", "ascii", "--explain")
    assert code == 0
    assert out.splitlines()[0].startswith("AS^R(const(1)) * WH(S^2(gr, Y=>, const(0))")
    assert "sem_S(while b do S) = WH(sem_B(b), sem_S(S))" in out
    code, data = run_json(capsys, "term", EXAMPLES / "power.sipl")
    assert data["result"]["term"]["kind"] == "composition"
    assert set(data["result"]["notation"]) == {"unicode", "ascii", "latex"}
    assert data["result"]["construction"][-1]["rule"].startswith("sem_P")


def test_eval(capsys):
    code, out, _ = run(capsys, "eval", EXAMPLES / "power.sipl", "--state", "X=2, Y=5", "--trace")
    assert code == 0 and "R ↦ 32" in out and "[WH-true]" in out
    code, data = run_json(capsys, "eval", EXAMPLES / "power.sipl", "--state", "X=2,Y=5")
    assert data["result"]["defined"] and data["result"]["state"] == {"R": 32, "X": 2, "Y": 0}
    assert data["result"]["trace"][0]["rule"] == "AS"


def test_eval_undefined_results(capsys):
    code, out, err = run(capsys, "eval", EXAMPLES / "power.sipl", "--state", "X=2")
    assert code == 1 and "НЕ ВИЗНАЧЕНО" in out and "[R001]" in err and "'Y'" in err
    code, data = run_json(capsys, "eval", EXAMPLES / "loop_forever.sipl", "--state", "X=1",
                          "--max-steps", "25")
    assert code == 1 and not data["ok"] and data["result"]["state"] is None
    assert data["result"]["undefined"]["kind"] == "step_limit"
    assert data["diagnostics"][0]["code"] == "R002"


def test_eval_from_saved_term_and_state_file(capsys, tmp_path):
    _, out, _ = run(capsys, "term", EXAMPLES / "gcd.sipl", "--json")
    term_file = tmp_path / "gcd.term.json"
    term_file.write_text(out, encoding="utf-8")
    state_file = tmp_path / "state.json"
    state_file.write_text('{"A": 21, "B": 14}', encoding="utf-8")
    code, data = run_json(capsys, "eval", term_file, "--from-term", "--state-file", state_file)
    assert code == 0 and data["result"]["state"] == {"A": 7, "B": 7}


def test_prove(capsys):
    code, out, _ = run(capsys, "prove", EXAMPLES / "division.sipl")
    assert code == 0 and "встановлено повну коректність" in out
    code, data = run_json(capsys, "prove", EXAMPLES / "multiply.sipl", "--inv", "Y >= 0")
    assert code == 1 and not data["ok"] and data["result"]["verdict"] == "failed"
    refuted = [c for c in data["result"]["conditions"] if c["status"] == "refuted"]
    assert refuted and refuted[0]["counterexample"] is not None
    code, _, err = run(capsys, "prove", EXAMPLES / "fibonacci.sipl")
    assert code == 1 and "[P006]" in err and "інваріант" in err


def test_usage_errors(capsys):
    code, _, err = run(capsys, "check", "no_such_file.sipl")
    assert code == 2 and "не вдалося прочитати" in err
    code, data = run_json(capsys, "check", "no_such_file.sipl")
    assert code == 2 and data["diagnostics"][0]["code"] == "U001"
    code, _, err = run(capsys, "eval", EXAMPLES / "power.sipl", "--state", "X=two")
    assert code == 2 and "X=two" in err
    assert run(capsys, "eval", EXAMPLES / "power.sipl", "--max-steps", "0")[0] == 2
    assert run(capsys, "frobnicate")[0] == 2
    assert run(capsys, "--version")[0] == 0


def test_parse_state():
    assert parse_state("X=1, Y = -2;Z=3", None) == {"X": 1, "Y": -2, "Z": 3}
    assert parse_state(None, None) == {}


def test_module_entry_point_handles_utf8():
    done = subprocess.run(
        [sys.executable, "-m", "sipl", "term", str(EXAMPLES / "unicode.sipl")],
        capture_output=True)
    assert done.returncode == 0
    assert "⇒" in done.stdout.decode("utf-8")
