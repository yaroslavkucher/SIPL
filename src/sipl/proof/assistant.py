"""Асистент доведення коректності циклічних SIPL-програм.

Для програми зі специфікацією (перед-/післяумова, інваріанти, за бажанням
варіанти циклів) асистент:

1. генерує умови перевірки (:mod:`.vcgen`);
2. кожну умову пробує довести символьно (:mod:`.prover`);
3. якщо не вдалося - шукає контрприклад: перебором малих цілих значень та
   випадковими пробами;
4. окремо "проганяє" програму на всіх малих початкових станах, що
   задовольняють передумову, і стежить за інваріантом, варіантом та
   післяумовою (динамічна перевірка на семантичному термі програми).

Статуси умови: ``proved`` - є доказ; ``refuted`` - є контрприклад;
``tested`` - доказу немає, але й контрприкладу серед перевірених станів не
знайдено (це **не** доведення).
"""

from __future__ import annotations

import itertools
import random
from dataclasses import dataclass, field

from .. import ast
from ..lexer import Annotation
from ..semantics.evaluate import Undefined, evaluate, format_state
from ..semantics.terms import WH, Term, build_term
from .logic import EvalError, eval_cond, eval_expr, free_vars
from .prover import prove
from .vcgen import VC, Spec, extract_spec, generate_vcs

_KIND_UA = {
    "entry": "база", "preserve": "крок", "exit": "вихід",
    "variant_bound": "варіант ≥ 0", "variant_decrease": "варіант спадає",
}
_STATUS_UA = {
    "proved": "ДОВЕДЕНО (символьно)",
    "refuted": "СПРОСТОВАНО (знайдено контрприклад)",
    "tested": "НЕ ДОВЕДЕНО, контрприкладів не знайдено",
}


@dataclass
class Options:
    exhaustive_budget: int = 40_000  # станів на одну умову при повному переборі
    random_samples: int = 3_000
    dynamic_budget: int = 20_000  # початкових станів для динамічної перевірки
    max_steps: int = 2_000  # ліміт кроків одного запуску
    seed: int = 0


@dataclass
class VCResult:
    vc: VC
    status: str
    proof: list[str] = field(default_factory=list)
    counterexample: dict[str, int] | None = None
    tested: int = 0
    note: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.vc.id, "kind": self.vc.kind, "loop": self.vc.loop,
            "title": self.vc.title,
            "hypotheses": [ast.cond_to_source(h) for h in self.vc.hypotheses],
            "goal": ast.cond_to_source(self.vc.goal),
            "formula": self.vc.text(),
            "span": self.vc.span.to_dict() if self.vc.span else None,
            "status": self.status, "proof": self.proof,
            "counterexample": self.counterexample, "tested_states": self.tested,
            "note": self.note,
        }


@dataclass
class Violation:
    kind: str  # invariant | variant | post
    message: str
    initial: dict[str, int]
    state: dict[str, int]
    loop: int | None = None
    iteration: int | None = None

    def to_dict(self) -> dict:
        return {"kind": self.kind, "message": self.message, "initial_state": self.initial,
                "state": self.state, "loop": self.loop, "iteration": self.iteration}


@dataclass
class DynamicResult:
    runs: int = 0
    bound: int = 0
    violations: list[Violation] = field(default_factory=list)
    step_limited: dict[str, int] | None = None  # приклад входу, де вичерпано ліміт

    def to_dict(self) -> dict:
        return {"runs": self.runs, "bound": self.bound,
                "violations": [v.to_dict() for v in self.violations],
                "step_limit_example": self.step_limited}


@dataclass
class Report:
    spec: Spec
    results: list[VCResult]
    dynamic: DynamicResult
    verdict: str  # proved | tested | failed | refuted
    summary: str
    total: bool  # чи перевірялась завершуваність усіх циклів

    @property
    def ok(self) -> bool:
        return self.verdict in ("proved", "tested")

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict, "summary": self.summary,
            "correctness": "total" if self.total else "partial",
            "spec": {
                "pre": ast.cond_to_source(self.spec.pre),
                "post": ast.cond_to_source(self.spec.post),
                "loops": [{
                    "index": l.index, "span": l.loop.span.to_dict(),
                    "condition": ast.cond_to_source(l.loop.cond),
                    "invariant": ast.cond_to_source(l.invariant) if l.invariant else None,
                    "variant": ast.expr_to_source(l.variant) if l.variant else None,
                } for l in self.spec.loops],
            },
            "conditions": [r.to_dict() for r in self.results],
            "dynamic": self.dynamic.to_dict(),
        }


# -- пошук контрприкладу до формули ---------------------------------------------------
def _ordered(bound: int) -> list[int]:
    """0, 1, -1, 2, -2, ... - щоб перший знайдений контрприклад був малим."""
    out = [0]
    for i in range(1, bound + 1):
        out += [i, -i]
    return out


def _box_bound(count: int, budget: int, top: int = 10) -> int:
    if count == 0:
        return 0
    return next((b for b in range(top, 0, -1) if (2 * b + 1) ** count <= budget), 0)


def find_counterexample(vc: VC, options: Options) -> tuple[dict[str, int] | None, int]:
    """Стан, на якому гіпотези істинні, а ціль хибна; та кількість перевірених станів."""
    names = free_vars(*vc.hypotheses, vc.goal)
    tested = 0

    def falsifies(state: dict[str, int]) -> bool:
        nonlocal tested
        try:
            if not all(eval_cond(h, state) for h in vc.hypotheses):
                return False
            tested += 1
            return not eval_cond(vc.goal, state)
        except EvalError:
            return False

    if not names:
        return ({}, 1) if falsifies({}) else (None, tested)
    bound = _box_bound(len(names), options.exhaustive_budget)
    if bound:
        for values in itertools.product(_ordered(bound), repeat=len(names)):
            state = dict(zip(names, values))
            if falsifies(state):
                return state, tested
    rng = random.Random(options.seed)
    for i in range(options.random_samples):
        limit = (20, 100, 1000)[i % 3]
        state = {n: rng.randint(-limit, limit) for n in names}
        if falsifies(state):
            return state, tested
    return None, tested


def check_vc(vc: VC, options: Options) -> VCResult:
    attempt = prove(vc.hypotheses, vc.goal)
    if attempt.proved:
        return VCResult(vc, "proved", attempt.steps)
    counterexample, tested = find_counterexample(vc, options)
    if counterexample is not None:
        return VCResult(vc, "refuted", counterexample=counterexample, tested=tested,
                        note=attempt.reason)
    return VCResult(vc, "tested", tested=tested, note=attempt.reason)


# -- динамічна перевірка ---------------------------------------------------------------
def dynamic_check(program: ast.Program, spec: Spec, options: Options,
                  term: Term | None = None) -> DynamicResult:
    """Виконує програму на малих початкових станах із передумовою та
    перевіряє інваріанти, варіанти й післяумову на реальних обчисленнях."""
    term = term or build_term(program)
    loops = {l.loop.span.start: l for l in spec.loops}
    names = free_vars(program, spec.pre, spec.post,
                      *[l.invariant for l in spec.loops if l.invariant is not None])
    result = DynamicResult(bound=_box_bound(len(names), options.dynamic_budget, top=6))
    seen_kinds: set[tuple[str, int | None]] = set()

    def report(violation: Violation) -> None:
        key = (violation.kind, violation.loop)
        if key not in seen_kinds:  # одного прикладу на кожен вид порушення досить
            seen_kinds.add(key)
            result.violations.append(violation)

    def run(initial: dict[str, int]) -> None:
        iterations: dict[int, int] = {}
        last_variant: dict[int, int] = {}

        def observer(event: str, wh: Term, state: dict[str, int]) -> None:
            assert isinstance(wh, WH) and wh.origin is not None
            ls = loops[wh.origin.start]
            if event == "loop_exit":
                iterations.pop(ls.index, None)
                last_variant.pop(ls.index, None)
                return
            k = iterations.get(ls.index, 0)
            iterations[ls.index] = k + 1
            # Порушення фіксуємо, але виконання продовжуємо: треба ще з'ясувати,
            # чи порушується післяумова (тобто чи хибна сама програма).
            if ls.invariant is not None and not eval_cond(ls.invariant, state):
                report(Violation(
                    "invariant",
                    f"інваріант циклу №{ls.index} хибний перед ітерацією {k + 1}",
                    initial, dict(state), ls.index, k + 1))
            if ls.variant is not None and eval_cond(ls.loop.cond, state):
                value = eval_expr(ls.variant, state)
                previous = last_variant.get(ls.index)
                if value < 0 or (previous is not None and value >= previous):
                    why = (f"від'ємний ({value})" if value < 0
                           else f"не зменшився ({previous} → {value})")
                    report(Violation(
                        "variant", f"варіант циклу №{ls.index} {why} на ітерації {k + 1}",
                        initial, dict(state), ls.index, k + 1))
                last_variant[ls.index] = value

        try:
            outcome = evaluate(term, initial, max_steps=options.max_steps, trace=False,
                               observer=observer)
        except EvalError:
            return
        if not outcome.defined:
            if isinstance(outcome.reason, Undefined) and outcome.reason.kind == "step_limit" \
                    and result.step_limited is None:
                result.step_limited = dict(initial)
            return
        try:
            holds = eval_cond(spec.post, outcome.state or {})
        except EvalError:
            return
        if not holds:
            report(Violation("post", "післяумова хибна у кінцевому стані", initial,
                             dict(outcome.state or {})))

    for values in itertools.product(_ordered(result.bound), repeat=len(names)):
        initial = dict(zip(names, values))
        try:
            if not eval_cond(spec.pre, initial):
                continue
        except EvalError:
            continue
        result.runs += 1
        run(initial)
        if ("post", None) in seen_kinds:
            break
    return result


# -- головна функція ----------------------------------------------------------------------
def verify(program: ast.Program, annotations: list[Annotation], source: str, *,
           pre: str | None = None, post: str | None = None,
           invariants: list[str] | None = None, variants: list[str] | None = None,
           options: Options | None = None) -> Report:
    """Повний цикл перевірки. Кидає :class:`sipl.proof.vcgen.SpecError`,
    якщо специфікація некоректна або неповна."""
    options = options or Options()
    spec = extract_spec(program, annotations, source, pre=pre, post=post,
                        invariants=invariants, variants=variants)
    results = [check_vc(vc, options) for vc in generate_vcs(program, spec)]
    dynamic = dynamic_check(program, spec, options)
    total = bool(spec.loops) and all(l.variant is not None for l in spec.loops) or not spec.loops
    kind = "повну" if total else "часткову"

    refuted = [r for r in results if r.status == "refuted"]
    untested = [r for r in results if r.status == "tested"]
    real = [v for v in dynamic.violations]
    if any(v.kind == "post" for v in real):
        verdict, summary = "refuted", (
            "Програма НЕ відповідає специфікації: знайдено початковий стан, що задовольняє "
            "передумову, але кінцевий стан порушує післяумову.")
    elif real:
        verdict, summary = "failed", (
            "Запропоноване індуктивне твердження хибне: воно порушується під час реального "
            "виконання програми (див. динамічну перевірку).")
    elif refuted:
        ids = ", ".join(r.vc.id for r in refuted)
        verdict, summary = "failed", (
            f"Доведення не проходить: умови {ids} мають контрприклади. На реальних "
            "обчисленнях порушень не знайдено, тож імовірно інваріант надто слабкий "
            "(не індуктивний) - його слід підсилити.")
    elif untested:
        ids = ", ".join(r.vc.id for r in untested)
        verdict, summary = "tested", (
            f"Контрприкладів не знайдено, але умови {ids} не доведено символьно - їх треба "
            f"довести вручну, щоб отримати {kind} коректність.")
    else:
        verdict, summary = "proved", (
            f"Усі умови перевірки доведено: встановлено {kind} коректність програми відносно "
            "специфікації." + ("" if total else " Завершуваність не перевірялась - для неї "
                               "задайте варіант циклу (//@ variant: ...)."))
    if not spec.has_post:
        summary += " Увага: післяумову не задано, тому перевірено лише інваріанти."
    return Report(spec, results, dynamic, verdict, summary, total)


def format_report(report: Report) -> str:
    """Текстовий протокол доведення."""
    spec = report.spec
    out = ["СПЕЦИФІКАЦІЯ",
           f"  передумова  P: {ast.cond_to_source(spec.pre)}",
           f"  післяумова  Q: {ast.cond_to_source(spec.post)}"]
    for l in spec.loops:
        out.append(f"  цикл №{l.index} (рядок {l.loop.span.line}): "
                   f"while {ast.cond_to_source(l.loop.cond)}")
        out.append(f"    інваріант I{l.index}: {ast.cond_to_source(l.invariant)}")
        if l.variant is not None:
            out.append(f"    варіант   V{l.index}: {ast.expr_to_source(l.variant)}")
    out += ["", "УМОВИ ПЕРЕВІРКИ (індукція за кількістю ітерацій циклу)"]
    for r in report.results:
        out.append(f"  {r.vc.id} [{_KIND_UA[r.vc.kind]}] {r.vc.title}")
        for hyp in r.vc.hypotheses:
            out.append(f"      дано:    {ast.cond_to_source(hyp)}")
        out.append(f"      довести: {ast.cond_to_source(r.vc.goal)}")
        out.append(f"      статус:  {_STATUS_UA[r.status]}")
        for line in r.proof:
            out.append(f"        · {line}")
        if r.counterexample is not None:
            out.append(f"        контрприклад: {format_state(r.counterexample)}")
        if r.status == "tested":
            out.append(f"        перевірено станів: {r.tested}")
        if r.note and r.status != "proved":
            out.append(f"        ({r.note})")
    d = report.dynamic
    out += ["", "ДИНАМІЧНА ПЕРЕВІРКА",
            f"  запусків: {d.runs} (усі початкові стани зі значеннями змінних у "
            f"[-{d.bound}, {d.bound}], що задовольняють передумову)"]
    if not d.violations:
        out.append("  порушень інваріантів, варіантів і післяумови не знайдено")
    for v in d.violations:
        out.append(f"  ПОРУШЕННЯ: {v.message}")
        out.append(f"    початковий стан: {format_state(v.initial)}")
        out.append(f"    поточний стан:   {format_state(v.state)}")
    if d.step_limited is not None:
        out.append(f"  увага: на вході {format_state(d.step_limited)} виконання не завершилось "
                   "у межах ліміту кроків (можлива незавершуваність)")
    out += ["", f"ВИСНОВОК: {report.summary}"]
    return "\n".join(out)
