"""Семантичні терми: побудова (SEM-01) та обчислення на стані (SEM-02)."""

import json

import pytest

from conftest import GOLDEN, VALID, read
from sipl import parse
from sipl.semantics import (AS, IF, WH, Apply, Comp, Const, Deref, Id, build_term, evaluate,
                            explain_construction, format_trace, from_dict, render, to_dict)


def term_of(source: str):
    return build_term(parse(source))


# -- побудова --------------------------------------------------------------------
def test_power_program_term(power_source):
    term = term_of(power_source)
    assert term == Comp((
        AS("R", Const(1)),
        WH(Apply("gr", (Deref("Y"), Const(0))),
           Comp((AS("R", Apply("mult", (Deref("R"), Deref("X")))),
                 AS("Y", Apply("sub", (Deref("Y"), Const(1))))))),
    ))
    assert render(term, "ascii") == (
        "AS^R(const(1)) * WH(S^2(gr, Y=>, const(0)), "
        "AS^R(S^2(mult, R=>, X=>)) * AS^Y(S^2(sub, Y=>, const(1))))")


@pytest.mark.parametrize("source, expected", [
    ("begin skip end", "id"),
    ("begin X := Y end", "AS^X(Y=>)"),
    ("begin X := 2 + Y * 3 end", "AS^X(S^2(add, const(2), S^2(mult, Y=>, const(3))))"),
    ("begin if X = 1 or not Y > 2 then skip else X := 0 end",
     "IF(S^2(or, S^2(eq, X=>, const(1)), S^1(neg, S^2(gr, Y=>, const(2)))), id, "
     "AS^X(const(0)))"),
    ("begin begin begin X := 1 end end end", "AS^X(const(1))"),
    ("begin X := 1; begin Y := 2; Z := 3 end end",
     "AS^X(const(1)) * AS^Y(const(2)) * AS^Z(const(3))"),
    ("begin while X > 0 do begin X := X - 1; skip end end",
     "WH(S^2(gr, X=>, const(0)), AS^X(S^2(sub, X=>, const(1))) * id)"),
])
def test_all_basic_constructs(source, expected):
    assert render(term_of(source), "ascii") == expected


def test_notations():
    term = term_of("begin X := X + 1 end")
    assert render(term) == "AS^X(S^2(add, X⇒, 1̅))"
    assert render(term, "latex") == r"AS^{X}(S^{2}(add, X{\Rightarrow}, \bar{1}))"
    assert "\n" in render(term_of("begin X := 1; Y := 2 end"), pretty=True)
    with pytest.raises(ValueError):
        render(term, "html")


def test_construction_is_recursive_over_ast(power_source):
    term, steps = explain_construction(parse(power_source))
    assert steps[-1].term == term and steps[-1].rule.startswith("sem_P")
    rules = [s.rule for s in steps]
    assert "sem_S(while b do S) = WH(sem_B(b), sem_S(S))" in rules
    assert "sem_S(S1 ; S2) = sem_S(S1) • sem_S(S2)" in rules
    # правило для підвиразу завжди передує правилу для виразу, що його містить
    positions = {id(s.term): i for i, s in enumerate(steps)}
    for i, step in enumerate(steps):
        if isinstance(step.term, AS):
            assert positions[id(step.term.value)] < i


def test_terms_keep_origin_spans(power_source):
    term = term_of(power_source)
    loop = term.parts[1]
    assert power_source[loop.origin.start:loop.origin.end].startswith("while Y > 0 do")


def test_json_roundtrip(power_source):
    term = term_of(power_source)
    restored = from_dict(json.loads(json.dumps(to_dict(term))))
    assert restored == term and restored.parts[1].origin == term.parts[1].origin
    with pytest.raises(ValueError):
        from_dict({"kind": "superposition", "func": "add", "args": []})


@pytest.mark.parametrize("path", VALID, ids=lambda p: p.stem)
def test_golden_terms(path):
    """Еталонні терми для всіх прикладів (tests/golden/<приклад>.term)."""
    expected = read(GOLDEN / f"{path.stem}.term").rstrip("\n")
    assert render(term_of(read(path)), "ascii", pretty=True) == expected


# -- обчислення ---------------------------------------------------------------------
def run(source: str, **state):
    return evaluate(term_of(source), state)


def test_power_evaluation(power_source):
    result = run(power_source, X=2, Y=10)
    assert result.defined and result.state == {"X": 2, "Y": 0, "R": 1024}


@pytest.mark.parametrize("name, state, expected", [
    ("multiply", {"X": 6, "Y": 7}, {"R": 42}),
    ("factorial", {"N": 5}, {"F": 120}),
    ("sum", {"N": 10}, {"S": 55}),
    ("division", {"A": 17, "B": 5}, {"Q": 3, "R": 2}),
    ("gcd", {"A": 48, "B": 18}, {"A": 6, "B": 6}),
    ("max", {"X": 3, "Y": 9}, {"M": 9}),
    ("abs", {"X": -4}, {"R": 4}),
    ("swap", {"X": 1, "Y": 2}, {"X": 2, "Y": 1}),
    ("nested", {"A": 3, "B": 4}, {"P": 12}),
    ("fibonacci", {"N": 10}, {"A": 55}),
    ("precedence", {}, {"A": 3, "B": -3, "C": -5}),
])
def test_examples_compute_expected_values(name, state, expected):
    from conftest import EXAMPLES

    result = run(read(EXAMPLES / f"{name}.sipl"), **state)
    assert result.defined
    assert {k: result.state[k] for k in expected} == expected


def test_initial_state_is_not_mutated():
    state = {"X": 1}
    evaluate(term_of("begin X := X + 1 end"), state)
    assert state == {"X": 1}


def test_undefined_variable_gives_undefined_result():
    result = run("begin\n  X := Y + 1\nend")
    assert not result.defined and result.state is None
    assert result.reason.kind == "undefined_variable"
    assert (result.reason.span.line, result.reason.span.column) == (2, 8)
    assert "'Y'" in result.reason.message


def test_non_terminating_loop_hits_step_limit():
    term = term_of("begin while X > 0 do X := X + 1 end")
    result = evaluate(term, {"X": 1}, max_steps=100)
    assert not result.defined and result.reason.kind == "step_limit"
    assert result.transitions == 101
    assert evaluate(term, {"X": 0}, max_steps=100).state == {"X": 0}


def test_trace_lists_applied_rules(power_source):
    result = run(power_source, X=3, Y=2)
    rules = [s.rule for s in result.steps]
    assert rules == ["AS", "WH-true", "AS", "AS", "WH-true", "AS", "AS", "WH-false"]
    assert result.transitions == len(rules)
    first = result.steps[0]
    assert first.state == {"X": 3, "Y": 2} and first.result == {"X": 3, "Y": 2, "R": 1}
    assert result.steps[2].depth == 1 and result.steps[-1].result == result.state
    text = format_trace(result)
    assert "ітерація 2" in text and "R ↦ 9" in text
    assert json.dumps(result.to_dict())  # слід серіалізується


def test_detailed_trace_includes_expression_steps():
    result = evaluate(term_of("begin X := (X + 1) * 2 end"), {"X": 4}, detail=True)
    assert [s.rule for s in result.steps] == ["deref", "const", "S", "const", "S", "AS"]
    assert [s.result for s in result.steps[:5]] == [4, 1, 5, 2, 10]


def test_if_branches_and_boolean_functions():
    source = "begin if not X > 0 or X = 5 then R := 1 else R := 2 end"
    assert run(source, X=-1).state["R"] == 1
    assert run(source, X=5).state["R"] == 1
    assert run(source, X=3).state["R"] == 2
    assert [s.rule for s in run(source, X=3).steps] == ["IF-false", "AS"]


def test_evaluating_a_hand_built_term():
    term = Comp((AS("X", Const(7)), IF(Apply("gr", (Deref("X"), Const(5))), Id(),
                                       AS("X", Const(0)))))
    assert evaluate(term).state == {"X": 7}
    with pytest.raises(ValueError):
        evaluate(AS("X", Apply("gr", (Const(1), Const(2)))))
