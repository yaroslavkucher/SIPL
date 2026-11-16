"""Асистент доведення коректності циклічних програм (SEM-04)."""

import pytest

from conftest import EXAMPLES, read
from sipl import ast, parse_assertion
from sipl.parser import parse_with_annotations
from sipl.proof import SpecError, extract_spec, format_report, generate_vcs, prove, verify
from sipl.proof.assistant import Options, find_counterexample
from sipl.proof.logic import eval_cond, subst_cond
from sipl.proof.vcgen import VC

MULTIPLY = read(EXAMPLES / "multiply.sipl")


def check(source: str, **overrides):
    program, annotations = parse_with_annotations(source)
    return verify(program, annotations, source, **overrides)


def P(text: str) -> ast.Cond:
    return parse_assertion(text)


# -- логіка -------------------------------------------------------------------------
def test_substitution_and_evaluation():
    cond = subst_cond(P("R + X * Y = A and Y >= 0"), "Y", ast.BinOp("-", ast.Var("Y"), ast.Num(1)))
    assert ast.cond_to_source(cond) == "R + X * (Y - 1) = A and Y - 1 >= 0"
    assert eval_cond(P("X^2 > 3 -> X != 1"), {"X": 2})
    assert not eval_cond(P("X <= 1 and true"), {"X": 2})


# -- генерація умов --------------------------------------------------------------------
def test_verification_conditions_for_a_loop():
    program, annotations = parse_with_annotations(MULTIPLY)
    spec = extract_spec(program, annotations, MULTIPLY)
    vcs = generate_vcs(program, spec)
    assert [vc.kind for vc in vcs] == [
        "entry", "preserve", "exit", "variant_bound", "variant_decrease"]
    assert [vc.id for vc in vcs] == ["VC1", "VC2", "VC3", "VC4", "VC5"]
    entry, preserve, exit_, _, decrease = vcs
    assert ast.cond_to_source(entry.goal) == "0 + X * Y = A * B and Y >= 0"
    assert ast.cond_to_source(preserve.goal) == "R + X + X * (Y - 1) = A * B and Y - 1 >= 0"
    assert [ast.cond_to_source(h) for h in exit_.hypotheses] == [
        "R + X * Y = A * B and Y >= 0", "not (Y > 0)"]
    assert ast.cond_to_source(decrease.goal) == "Y - 1 < v0_1"
    assert preserve.loop == 1 and preserve.span.line == 8


def test_wp_of_conditional_without_loops():
    report = check(read(EXAMPLES / "max.sipl"))
    (result,) = report.results
    assert result.vc.kind == "entry" and result.status == "proved"
    assert "X > Y ->" in result.vc.text()


def test_frame_rule_keeps_unmodified_facts_across_inner_loop():
    report = check(read(EXAMPLES / "nested.sipl"))
    assert report.verdict == "proved" and report.total
    assert {r.vc.loop for r in report.results} == {None, 1, 2}


# -- символьний доводжувач ---------------------------------------------------------------
@pytest.mark.parametrize("hyps, goal", [
    ([], "X + 1 > X"),
    ([], "(X + Y) * (X + Y) = X * X + 2 * X * Y + Y * Y"),
    (["X > 0", "Y > 0"], "X + Y > 1"),
    (["X = A", "Y = B"], "X * Y = A * B"),
    (["X >= 0", "not (X > 0)"], "X = 0"),
    (["A = Q * B + R", "R >= B"], "A = (Q + 1) * B + (R - B)"),
    (["X > 0", "Y > 0"], "X * Y > 0"),
    (["2 * S = I * (I + 1)"], "2 * (S + I + 1) = (I + 1) * (I + 2)"),
    (["X > 0 or X < 0"], "X != 0"),
    (["R * X^Y = A^B", "Y > 0"], "R * X * X^(Y - 1) = A^B"),
    (["R * X^Y = P", "Y = 0"], "R = P"),
])
def test_prover_proves_valid_formulas(hyps, goal):
    attempt = prove([P(h) for h in hyps], P(goal))
    assert attempt.proved, attempt.reason
    assert attempt.steps


@pytest.mark.parametrize("hyps, goal", [
    ([], "X > 0"),
    (["X > 0"], "X > 1"),
    (["X * Y = 0"], "X = 0"),
    (["Y >= 0"], "R = A * B"),
    (["R * X^Y = P"], "R = P"),  # без Y = 0 хибне
])
def test_prover_is_sound_on_invalid_formulas(hyps, goal):
    """Невалідну формулу не можна "довести", а перебір знаходить контрприклад."""
    hypotheses, target = [P(h) for h in hyps], P(goal)
    attempt = prove(hypotheses, target)
    assert not attempt.proved and attempt.reason
    state, tested = find_counterexample(VC("VC", "entry", "", hypotheses, target), Options())
    assert state is not None and tested > 0
    assert all(eval_cond(h, state) for h in hypotheses) and not eval_cond(target, state)


def test_prover_soundness_on_random_formulas():
    """Усе, що доводжувач оголосив доведеним, має бути істинним на всіх
    станах малого куба (перевірка повним перебором)."""
    import itertools
    import random

    rng = random.Random(2026)
    names = ["X", "Y", "Z"]

    def expr(depth: int) -> ast.Expr:
        if depth == 0 or rng.random() < 0.3:
            return ast.Var(rng.choice(names)) if rng.random() < 0.6 else ast.Num(rng.randint(-2, 3))
        return ast.BinOp(rng.choice("++-*"), expr(depth - 1), expr(depth - 1))

    def cond(depth: int) -> ast.Cond:
        roll = rng.random()
        if depth == 0 or roll < 0.45:
            return ast.Compare(rng.choice(["=", ">", "<", ">=", "<=", "!="]), expr(1), expr(1))
        if roll < 0.6:
            return ast.Not(cond(depth - 1))
        return ast.BoolOp(rng.choice(["and", "or", "->"]), cond(depth - 1), cond(depth - 1))

    proved = 0
    for _ in range(500):
        hyps, goal = [cond(1) for _ in range(rng.randint(0, 3))], cond(2)
        if not prove(hyps, goal).proved:
            continue
        proved += 1
        for values in itertools.product(range(-3, 4), repeat=3):
            state = dict(zip(names, values))
            if all(eval_cond(h, state) for h in hyps):
                assert eval_cond(goal, state), (
                    [ast.cond_to_source(h) for h in hyps], ast.cond_to_source(goal), state)
    assert proved > 20


# -- повна перевірка ----------------------------------------------------------------------
@pytest.mark.parametrize("name", ["multiply", "power", "sum", "division", "gcd", "nested",
                                  "max", "abs", "swap"])
def test_annotated_examples_are_proved(name):
    report = check(read(EXAMPLES / f"{name}.sipl"))
    assert report.verdict == "proved", format_report(report)
    assert report.ok and report.total
    assert all(r.status == "proved" and r.proof for r in report.results)
    assert report.dynamic.runs > 0 and not report.dynamic.violations


def test_partial_correctness_without_variant():
    report = check(MULTIPLY.replace("  //@ variant: Y\n", ""))
    assert report.verdict == "proved" and not report.total
    assert [r.vc.kind for r in report.results] == ["entry", "preserve", "exit"]
    assert "часткову" in report.summary and "Завершуваність не перевірялась" in report.summary


def test_weak_invariant_is_reported_as_not_inductive():
    report = check(MULTIPLY, invariants=["Y >= 0"])
    assert report.verdict == "failed" and not report.ok
    bad = [r for r in report.results if r.status == "refuted"]
    assert [r.vc.kind for r in bad] == ["exit"]
    state = bad[0].counterexample
    assert eval_cond(P("Y >= 0 and not (Y > 0)"), state) and not eval_cond(P("R = A * B"), state)
    assert not report.dynamic.violations  # на реальних обчисленнях Y >= 0 таки істинне
    assert "підсилити" in report.summary


def test_wrong_invariant_is_caught_on_a_real_execution():
    report = check(MULTIPLY, invariants=["R = X * Y and Y >= 0"])
    assert report.verdict == "failed"
    (violation,) = report.dynamic.violations
    assert violation.kind == "invariant" and violation.loop == 1
    assert eval_cond(P("Y >= 0 and X = A and Y = B"), violation.initial)
    assert not eval_cond(P("R = X * Y and Y >= 0"), violation.state)


def test_wrong_program_is_refuted_with_input_counterexample():
    report = check(MULTIPLY.replace("R := 0;", "R := 1;"))
    assert report.verdict == "refuted"
    post = [v for v in report.dynamic.violations if v.kind == "post"]
    assert post and not eval_cond(P("R = A * B"), post[0].state)
    assert any(r.status == "refuted" and r.vc.kind == "entry" for r in report.results)


def test_wrong_variant_is_detected():
    report = check(MULTIPLY, variants=["X + R"])
    assert report.verdict == "failed"
    kinds = {r.vc.kind for r in report.results if r.status == "refuted"}
    assert "variant_decrease" in kinds
    assert any(v.kind == "variant" for v in report.dynamic.violations)


def test_missing_precondition_breaks_the_base_case():
    report = check(MULTIPLY, pre="X = A and Y = B")
    entry = report.results[0]
    assert entry.status == "refuted" and entry.counterexample["Y"] < 0


def test_unprovable_but_true_condition_is_only_tested():
    # X^4 - X^2 + 1 > 0 істинне для всіх цілих X, але метод цього не виводить
    source = "//@ post: S > 0\nbegin\n  S := X * X * X * X - X * X + 1\nend"
    report = check(source)
    assert report.verdict == "tested" and report.ok
    (only,) = report.results
    assert only.status == "tested" and only.tested > 0 and only.note
    assert "не доведено символьно" in report.summary


def test_report_text_and_json(power_source):
    report = check(read(EXAMPLES / "power.sipl"))
    text = format_report(report)
    for fragment in ("СПЕЦИФІКАЦІЯ", "інваріант I1: R * X^Y = A^B and Y >= 0", "VC2 [крок]",
                     "ДОВЕДЕНО (символьно)", "властивість степеня", "ДИНАМІЧНА ПЕРЕВІРКА",
                     "ВИСНОВОК"):
        assert fragment in text
    data = report.to_dict()
    assert data["verdict"] == "proved" and data["correctness"] == "total"
    assert data["spec"]["loops"][0]["variant"] == "Y"
    assert {c["status"] for c in data["conditions"]} == {"proved"}
    assert data["conditions"][1]["span"]["line"] == 8


# -- помилки специфікації ------------------------------------------------------------------
def spec_error(source: str, **overrides):
    with pytest.raises(SpecError) as info:
        check(source, **overrides)
    return info.value.diagnostic


def test_missing_invariant_is_a_located_error():
    diag = spec_error("begin\n  while X > 0 do X := X - 1\nend")
    assert diag.code == "P006" and (diag.span.line, diag.span.column) == (2, 9)
    assert "//@ inv:" in diag.hint


def test_annotation_syntax_error_points_into_the_comment():
    diag = spec_error("begin\n  //@ inv: X >\n  while X > 0 do X := X - 1\nend")
    assert diag.code == "E002" and diag.span.line == 2 and diag.span.column >= 14


def test_other_spec_errors():
    assert spec_error("//@ ensures: X > 0\nbegin skip end").code == "P002"
    assert spec_error("begin skip\n//@ inv: X > 0\nend").code == "P003"
    assert spec_error("begin skip end", invariants=["X > 0"]).code == "P005"
    assert spec_error("begin skip end", post="X >").code == "P001"
