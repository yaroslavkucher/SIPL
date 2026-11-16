"""Невеликий символьний доводжувач для умов перевірки над цілими числами.

Метод - спростування: щоб довести ``H1 ∧ ... ∧ Hn → G``, показуємо, що
``H1 ∧ ... ∧ Hn ∧ ¬G`` не має розв'язків.

1. Усі порівняння зводяться до поліноміальних атомів ``p = 0`` та ``p ≥ 1``
   (над цілими ``a > b`` означає ``a - b ≥ 1``), формула - до ДНФ.
2. У кожному кон'юнкті рівності виду ``x = вираз`` (``x`` входить лінійно з
   коефіцієнтом ±1) використовуються як підстановки.
3. Суперечність шукається як невід'ємна цілочисельна комбінація атомів:
   якщо ``Σ k_i·q_i + Σ m_j·e_j`` тотожно дорівнює сталій ``c``, а з
   ``q_i ≥ 1`` та ``e_j = 0`` випливає, що сума ``≥ Σ k_i > c``, то кон'юнкт
   суперечливий. За потреби до ``q_i`` додаються їхні попарні добутки.

Метод **коректний, але неповний**: "доведено" означає справжній доказ, а
відмова ("не вдалося") нічого не говорить про істинність формули - тоді
асистент переходить до пошуку контрприкладів.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field

from .. import ast

Monomial = tuple[tuple[str, int], ...]
Poly = dict[Monomial, int]

_DNF_LIMIT = 256
_COMBO_LIMIT = 60_000


class Unsupported(Exception):
    """Формула поза фрагментом, який уміє доводжувач."""


# -- поліноми ---------------------------------------------------------------------
def p_const(c: int) -> Poly:
    return {(): c} if c else {}


def p_var(name: str) -> Poly:
    return {((name, 1),): 1}


def p_add(a: Poly, b: Poly, scale: int = 1) -> Poly:
    out = dict(a)
    for mono, coeff in b.items():
        value = out.get(mono, 0) + scale * coeff
        if value:
            out[mono] = value
        else:
            out.pop(mono, None)
    return out


def p_scale(a: Poly, k: int) -> Poly:
    return {m: c * k for m, c in a.items()} if k else {}


def _m_mul(a: Monomial, b: Monomial) -> Monomial:
    powers = dict(a)
    for name, exp in b:
        powers[name] = powers.get(name, 0) + exp
    return tuple(sorted(powers.items()))


def p_mul(a: Poly, b: Poly) -> Poly:
    out: Poly = {}
    for m1, c1 in a.items():
        for m2, c2 in b.items():
            mono = _m_mul(m1, m2)
            value = out.get(mono, 0) + c1 * c2
            if value:
                out[mono] = value
            else:
                out.pop(mono, None)
    return out


def p_is_const(a: Poly) -> bool:
    return all(m == () for m in a)


def p_value(a: Poly) -> int:
    return a.get((), 0)


def p_subst(a: Poly, name: str, repl: Poly) -> Poly:
    out: Poly = {}
    for mono, coeff in a.items():
        exp = dict(mono).get(name, 0)
        rest: Poly = {tuple(p for p in mono if p[0] != name): coeff}
        for _ in range(exp):
            rest = p_mul(rest, repl)
        out = p_add(out, rest)
    return out


def p_str(a: Poly) -> str:
    if not a:
        return "0"
    parts = []
    for mono, coeff in sorted(a.items(), key=lambda kv: (-sum(e for _, e in kv[0]), kv[0])):
        body = "*".join(n if e == 1 else f"{n}^{e}" for n, e in mono)
        mag = abs(coeff)
        text = body if body and mag == 1 else (f"{mag}*{body}" if body else str(mag))
        parts.append(("- " if coeff < 0 else "+ ") + text)
    first = parts[0]
    head = first[2:] if first.startswith("+") else "-" + first[2:]
    return " ".join([head] + parts[1:])


Powers = dict[str, tuple[Poly, Poly]]


def poly_of(e: ast.Expr, powers: Powers | None = None) -> Poly:
    """Поліном виразу. Степінь зі змінним показником стає "непрозорою"
    змінною ``pow(основа, показник)``; такі змінні записуються в ``powers``."""
    if isinstance(e, ast.Num):
        return p_const(e.value)
    if isinstance(e, ast.Var):
        return p_var(e.name)
    if e.op == "+":
        return p_add(poly_of(e.left, powers), poly_of(e.right, powers))
    if e.op == "-":
        return p_add(poly_of(e.left, powers), poly_of(e.right, powers), -1)
    if e.op == "*":
        return p_mul(poly_of(e.left, powers), poly_of(e.right, powers))
    if e.op == "^":
        base, exponent = poly_of(e.left, powers), poly_of(e.right, powers)
        if p_is_const(exponent):
            if not 0 <= p_value(exponent) <= 8:
                raise Unsupported("сталий показник степеня поза межами 0..8")
            out = p_const(1)
            for _ in range(p_value(exponent)):
                out = p_mul(out, base)
            return out
        if powers is None:
            raise Unsupported("степінь зі змінним показником")
        name = f"pow({p_str(base)}, {p_str(exponent)})"
        powers[name] = (base, exponent)
        return p_var(name)
    raise Unsupported(f"операція {e.op}")


# -- зведення до ДНФ з атомів --------------------------------------------------------
Atom = tuple[str, tuple]  # ("eq" | "ge1", поліном у вигляді відсортованого кортежу)


def _freeze(p: Poly) -> tuple:
    return tuple(sorted(p.items()))


def _atom(kind: str, p: Poly) -> list[list[Atom]]:
    return [[(kind, _freeze(p))]]


_NEGATED = {"=": "!=", "!=": "=", ">": "<=", "<=": ">", "<": ">=", ">=": "<"}


def _compare(op: str, left: ast.Expr, right: ast.Expr,
             powers: Powers | None = None) -> list[list[Atom]]:
    diff = p_add(poly_of(left, powers), poly_of(right, powers), -1)  # left - right
    if op == "=":
        return _atom("eq", diff)
    if op == "!=":
        return _atom("ge1", diff) + _atom("ge1", p_scale(diff, -1))
    if op == ">":
        return _atom("ge1", diff)
    if op == "<":
        return _atom("ge1", p_scale(diff, -1))
    if op == ">=":
        return _atom("ge1", p_add(diff, p_const(1)))
    if op == "<=":
        return _atom("ge1", p_add(p_scale(diff, -1), p_const(1)))
    raise Unsupported(f"порівняння {op}")


def _dnf_and(a: list[list[Atom]], b: list[list[Atom]]) -> list[list[Atom]]:
    if len(a) * len(b) > _DNF_LIMIT:
        raise Unsupported("завеликий перебір випадків")
    return [x + y for x in a for y in b]


def dnf(c: ast.Cond, positive: bool = True, powers: Powers | None = None) -> list[list[Atom]]:
    """ДНФ твердження (або його заперечення) як список кон'юнктів атомів."""
    if isinstance(c, ast.BoolConst):
        return [[]] if c.value == positive else []
    if isinstance(c, ast.Compare):
        return _compare(c.op if positive else _NEGATED[c.op], c.left, c.right, powers)
    if isinstance(c, ast.Not):
        return dnf(c.operand, not positive, powers)
    left_pos, right_pos = positive, positive
    op = c.op
    if op == "->":  # a -> b  ==  ¬a ∨ b
        op, left_pos = "or", not positive
    is_or = (op == "or") == positive
    left, right = dnf(c.left, left_pos, powers), dnf(c.right, right_pos, powers)
    if is_or:
        if len(left) + len(right) > _DNF_LIMIT:
            raise Unsupported("завеликий перебір випадків")
        return left + right
    return _dnf_and(left, right)


# -- спростування кон'юнкта --------------------------------------------------------
def _linear_var(eq: Poly) -> tuple[str, int] | None:
    """Змінна, що входить у рівність лінійно з коефіцієнтом ±1 і більше ніде."""
    for mono, coeff in sorted(eq.items()):
        if len(mono) == 1 and mono[0][1] == 1 and coeff in (1, -1):
            name = mono[0][0]
            if all(name not in dict(m) for m in eq if m != mono):
                return name, coeff
    return None


def _refute(atoms: list[Atom], steps: list[str]) -> bool:
    eqs = [dict(p) for kind, p in atoms if kind == "eq"]
    ges = [dict(p) for kind, p in atoms if kind == "ge1"]

    while True:
        for e in eqs:
            if e and p_is_const(e):
                steps.append(f"рівність {p_value(e)} = 0 хибна - суперечність")
                return True
        for q in ges:
            if p_is_const(q) and p_value(q) < 1:
                steps.append(f"нерівність {p_value(q)} ≥ 1 хибна - суперечність")
                return True
        eqs = [e for e in eqs if e]
        ges = [q for q in ges if not p_is_const(q)]
        pick = next(((e, lv) for e in eqs if (lv := _linear_var(e))), None)
        if pick is None:
            # q ≥ 1 та (2 - q) ≥ 1 разом означають q = 1
            pair = next(((a, b) for a, b in itertools.combinations(ges, 2)
                         if p_is_const(s := p_add(a, b)) and p_value(s) == 2), None)
            if pair is None:
                break
            a, b = pair
            derived = p_add(a, p_const(-1))
            steps.append(f"з {p_str(a)} ≥ 1 та {p_str(b)} ≥ 1 випливає {p_str(derived)} = 0")
            ges = [q for q in ges if q is not a and q is not b]
            eqs.append(derived)
            continue
        eq, (name, coeff) = pick
        # coeff*x + rest = 0  =>  x = -rest/coeff
        rest = {m: c for m, c in eq.items() if m != ((name, 1),)}
        repl = p_scale(rest, -coeff)
        steps.append(f"з рівності {p_str(eq)} = 0 підставляємо {name} = {p_str(repl)}")
        eqs = [p_subst(e, name, repl) for e in eqs if e is not eq]
        ges = [p_subst(q, name, repl) for q in ges]

    return _combine(eqs, ges, steps)


def _combine(eqs: list[Poly], ges: list[Poly], steps: list[str]) -> bool:
    """Шукає комбінацію Σ k_i·q_i + Σ m_j·e_j = c зі сталою c < Σ k_i."""
    if not ges:
        return False
    stages = [[(q, p_str(q)) for q in ges]]
    if len(ges) <= 5:
        products = [(p_mul(a, b), f"({p_str(a)})*({p_str(b)})")
                    for a, b in itertools.combinations_with_replacement(ges, 2)]
        stages.append(stages[0] + products)
    eq_ranges = [range(-2, 3)] * len(eqs) if len(eqs) <= 3 else None

    for candidates in stages:
        n = len(candidates)
        top = next((k for k in (3, 2, 1) if (k + 1) ** n * 5 ** len(eqs) <= _COMBO_LIMIT), None)
        if top is None:
            combos = (tuple(1 if i in chosen else 0 for i in range(n))
                      for size in (1, 2, 3)
                      for chosen in itertools.combinations(range(n), size))
        else:
            combos = itertools.product(range(top + 1), repeat=n)
        for ks in combos:
            total_k = sum(ks)
            if total_k == 0:
                continue
            acc: Poly = {}
            for k, (q, _) in zip(ks, candidates):
                if k:
                    acc = p_add(acc, q, k)
            for ms in (itertools.product(*eq_ranges) if eq_ranges else [()]):
                combo = acc
                for m, e in zip(ms, eqs):
                    if m:
                        combo = p_add(combo, e, m)
                if p_is_const(combo) and p_value(combo) < total_k:
                    terms = [f"{k}·[{name} ≥ 1]" for k, (_, name) in zip(ks, candidates) if k]
                    terms += [f"{m:+d}·[{p_str(e)} = 0]" for m, e in zip(ms, eqs) if m]
                    steps.append(
                        "додаємо " + " + ".join(terms) + f": ліва частина тотожно дорівнює "
                        f"{p_value(combo)}, а має бути ≥ {total_k} - суперечність")
                    return True
    return False


@dataclass
class ProofAttempt:
    proved: bool
    steps: list[str] = field(default_factory=list)
    reason: str = ""


def _rewrite_equalities(hyps: list[ast.Cond], goal: ast.Cond,
                        steps: list[str]) -> tuple[list[ast.Cond], ast.Cond]:
    """Гіпотези виду ``x = вираз`` застосовує як підстановки у формулу."""
    from .logic import conjuncts, free_vars, subst_cond

    hyps = [c for h in hyps for c in conjuncts(h)]
    while True:
        for i, h in enumerate(hyps):
            if not (isinstance(h, ast.Compare) and h.op == "="):
                continue
            pair = next(((v, other) for v, other in ((h.left, h.right), (h.right, h.left))
                         if isinstance(v, ast.Var) and v.name not in free_vars(other)), None)
            if pair is None:
                continue
            var, other = pair
            steps.append(f"гіпотеза {ast.cond_to_source(h)}: замінюємо {var.name} на "
                         f"{ast.expr_to_source(other)}")
            hyps = [subst_cond(x, var.name, other) for j, x in enumerate(hyps) if j != i]
            goal = subst_cond(goal, var.name, other)
            break
        else:
            return hyps, goal


def _power_axioms(powers: Powers, with_zero: bool) -> list[tuple[str, list[list[Atom]]]]:
    """Властивості степеня, потрібні для непрозорих ``pow(b, e)``:

    * ``e = 0 → b^e = 1``;
    * ``e ≥ 0 → b^(e + k) = b^e · b^k`` для пар степенів з однаковою основою.
    """
    axioms: list[tuple[str, list[list[Atom]]]] = []
    items = sorted(powers.items())
    if with_zero:
        for name, (_, exp) in items:
            axioms.append((
                f"властивість степеня: {p_str(exp)} = 0 → {name} = 1",
                _atom("ge1", exp) + _atom("ge1", p_scale(exp, -1))
                + _atom("eq", p_add(p_var(name), p_const(-1)))))
    for (small, (base, e_small)), (big, (base2, e_big)) in itertools.permutations(items, 2):
        diff = p_add(e_big, e_small, -1)
        if base != base2 or not p_is_const(diff) or not 1 <= p_value(diff) <= 4:
            continue
        factor = p_const(1)
        for _ in range(p_value(diff)):
            factor = p_mul(factor, base)
        axioms.append((
            f"властивість степеня: {p_str(e_small)} ≥ 0 → {big} = {small} * ({p_str(factor)})",
            _atom("ge1", p_scale(e_small, -1))
            + _atom("eq", p_add(p_var(big), p_mul(p_var(small), factor), -1))))
    return axioms


def prove(hypotheses: list[ast.Cond], goal: ast.Cond) -> ProofAttempt:
    """Пробує довести ``hypotheses → goal`` для всіх цілих значень змінних."""
    steps: list[str] = []
    hyps, goal = _rewrite_equalities(list(hypotheses), goal, steps)
    powers: Powers = {}
    try:
        base_cases: list[list[Atom]] = [[]]
        for hyp in hyps:
            base_cases = _dnf_and(base_cases, dnf(hyp, True, powers))
        base_cases = _dnf_and(base_cases, dnf(goal, False, powers))
    except Unsupported as err:
        return ProofAttempt(False, reason=f"формула поза можливостями символьного методу: {err}")
    if not base_cases:
        return ProofAttempt(True, steps + ["після підстановок заперечення цілі несумісне з "
                                           "гіпотезами вже на рівні логічної структури"])
    # Спершу з усіма властивостями степеня; якщо перебір випадків завеликий - з меншою
    # їх кількістю.
    variants = ([_power_axioms(powers, True), _power_axioms(powers, False), []]
                if powers else [[]])
    failure = ""
    for axioms in variants:
        try:
            cases = base_cases
            for _, alternatives in axioms:
                cases = _dnf_and(cases, alternatives)
        except Unsupported:
            continue
        local_steps = [text for text, _ in axioms]
        grouped: dict[tuple[str, ...], list[int]] = {}  # однакові міркування - один запис
        for i, case in enumerate(cases, 1):
            local: list[str] = []
            if not _refute(case, local):
                shown = "; ".join(
                    f"{p_str(dict(p))} {'= 0' if kind == 'eq' else '≥ 1'}" for kind, p in case)
                failure = f"не вдалося спростувати випадок {i} із {len(cases)}: {shown}"
                break
            grouped.setdefault(tuple(local), []).append(i)
        else:
            if len(cases) > 1:
                local_steps.append(f"розбір випадків (усього {len(cases)}) за диз'юнкціями у "
                                   "гіпотезах та запереченні цілі:")
            for lines, numbers in grouped.items():
                if len(cases) > 1:
                    label = "випадок" if len(numbers) == 1 else "випадки"
                    local_steps.append(f"{label} {', '.join(map(str, numbers))}:")
                local_steps.extend(("  " if len(cases) > 1 else "") + line for line in lines)
            return ProofAttempt(True, steps + local_steps)
    return ProofAttempt(False, reason=failure or "завеликий перебір випадків")
