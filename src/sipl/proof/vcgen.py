"""Специфікація програми та генерація умов перевірки (verification conditions).

Доведення коректності циклу - це індукція за кількістю ітерацій, де
індуктивним твердженням є інваріант ``I``:

* **база** - інваріант істинний перед першою ітерацією;
* **крок** - якщо ``I`` та умова циклу ``b`` істинні перед ітерацією, то
  ``I`` істинний після тіла;
* **вихід** - з ``I`` та ``¬b`` випливає те, що потрібно після циклу.

Щоб звести це до формул без програм, використовується найслабша передумова
``wp`` (правила логіки Гоара):

* ``wp(x := e, Q) = Q[e/x]``
* ``wp(S1; S2, Q) = wp(S1, wp(S2, Q))``
* ``wp(if b then S1 else S2, Q) = (b → wp(S1, Q)) ∧ (¬b → wp(S2, Q))``
* ``wp(while b do S, Q) = I`` + побічні умови "крок" і "вихід"
* ``wp(skip, Q) = Q``

Правило рамки: якщо ``Q = Q' ∧ F`` і ``F`` не містить змінних, яким цикл
присвоює, то ``wp(while, Q) = I ∧ F``, а умова виходу має ціль ``Q'``.

Для повної коректності додається варіант ``V`` (обмежувальна функція):
``I ∧ b → V ≥ 0`` та ``I ∧ b ∧ V = v0 → wp(S, V < v0)``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .. import ast
from ..lexer import Annotation, Lexer
from ..parser import Parser
from ..source import Diagnostic, SiplError, SiplSyntaxError, SourceSpan
from .logic import TRUE, assigned_vars, conj, conjuncts, free_vars, subst_cond, subst_expr


class SpecError(SiplError):
    """Помилка у специфікації (анотаціях)."""


@dataclass
class LoopSpec:
    index: int  # номер циклу в порядку запису, з 1
    loop: ast.While
    invariant: ast.Cond | None = None
    variant: ast.Expr | None = None


@dataclass
class Spec:
    pre: ast.Cond
    post: ast.Cond
    loops: list[LoopSpec]
    has_post: bool = True

    def loop_spec(self, loop: ast.While) -> LoopSpec:
        for spec in self.loops:
            if spec.loop is loop:
                return spec
        raise KeyError("цикл не належить програмі")


@dataclass
class VC:
    """Умова перевірки: з усіх ``hypotheses`` має випливати ``goal``
    на довільному стані (усі змінні - цілі числа)."""

    id: str
    kind: str  # entry | preserve | exit | variant_bound | variant_decrease
    title: str
    hypotheses: list[ast.Cond]
    goal: ast.Cond
    loop: int | None = None
    span: SourceSpan | None = None

    @property
    def formula(self) -> ast.Cond:
        hyp = conj(*self.hypotheses)
        return self.goal if hyp == TRUE else ast.BoolOp("->", hyp, self.goal)

    def text(self) -> str:
        return ast.cond_to_source(self.formula)


# -- розбір анотацій ---------------------------------------------------------------
def _parse_fragment(source: str, span: SourceSpan, kind: str):
    """Розбирає фрагмент тексту так, щоб позиції в діагностиці вказували на
    його справжнє місце у файлі: усе перед фрагментом замінюється пробілами."""
    masked = "".join("\n" if ch == "\n" else " " for ch in source[: span.start])
    parser = Parser(Lexer(masked + source[span.start:span.end]).tokenize(), extended=True)
    from ..ast_builder import build_condition, build_expression

    if kind == "expr":
        tree = parser.expression()
        parser.finish("виразу")
        return build_expression(tree)
    tree = parser.condition()
    parser.finish("твердження")
    return build_condition(tree)


def _parse_option(text: str, kind: str, what: str):
    span = SourceSpan(0, len(text), 1, 1, 1, len(text) + 1)
    try:
        return _parse_fragment(text, span, kind)
    except SiplSyntaxError as err:
        raise SpecError(Diagnostic("P001", f"{what}: {err.diagnostic.message}", None,
                                   hint=err.diagnostic.hint)) from None


_KEYS = {"pre": "pre", "post": "post", "inv": "inv", "invariant": "inv",
         "variant": "variant", "var": "variant"}


def extract_spec(program: ast.Program, annotations: list[Annotation], source: str, *,
                 pre: str | None = None, post: str | None = None,
                 invariants: list[str] | None = None,
                 variants: list[str] | None = None) -> Spec:
    """Збирає специфікацію з анотацій ``//@ pre|post|inv|variant: ...``.

    Анотації ``inv`` та ``variant`` стосуються найближчого наступного
    ``while``. Параметри ``pre``/``post``/``invariants``/``variants``
    (текст) замінюють анотації; списки зіставляються з циклами за порядком.
    """
    loops = [LoopSpec(i + 1, node) for i, node in enumerate(
        sorted((n for n in program.walk() if isinstance(n, ast.While)),
               key=lambda n: n.span.start))]
    pre_parts: list[ast.Cond] = []
    post_parts: list[ast.Cond] = []
    for ann in annotations:
        key = _KEYS.get(ann.key)
        if key is None:
            raise SpecError(Diagnostic(
                "P002", f"невідома анотація '//@ {ann.key}:'", ann.comment_span,
                hint="підтримуються: pre, post, inv (invariant), variant"))
        try:
            parsed = _parse_fragment(source, ann.span, "expr" if key == "variant" else "cond")
        except SiplSyntaxError as err:
            raise SpecError(err.diagnostic) from None
        if key == "pre":
            pre_parts.append(parsed)
        elif key == "post":
            post_parts.append(parsed)
        else:
            target = next((l for l in loops if l.loop.span.start > ann.comment_span.start), None)
            if target is None:
                raise SpecError(Diagnostic(
                    "P003", f"анотація '{ann.key}' не стосується жодного циклу", ann.comment_span,
                    hint="розмістіть її безпосередньо перед відповідним while"))
            if key == "inv":
                target.invariant = parsed if target.invariant is None else conj(
                    target.invariant, parsed)
            elif target.variant is not None:
                raise SpecError(Diagnostic(
                    "P004", f"для циклу в рядку {target.loop.span.line} задано два варіанти",
                    ann.comment_span))
            else:
                target.variant = parsed

    if pre is not None:
        pre_parts = [_parse_option(pre, "cond", "передумова")]
    if post is not None:
        post_parts = [_parse_option(post, "cond", "післяумова")]
    for texts, attr, kind, what in ((invariants, "invariant", "cond", "інваріант"),
                                    (variants, "variant", "expr", "варіант")):
        if texts:
            if len(texts) > len(loops):
                raise SpecError(Diagnostic(
                    "P005", f"задано {len(texts)} значень '{what}', а циклів у програмі "
                    f"{len(loops)}", None))
            for loop_spec, text in zip(loops, texts):
                setattr(loop_spec, attr, _parse_option(text, kind, what))

    for loop_spec in loops:
        if loop_spec.invariant is None:
            raise SpecError(Diagnostic(
                "P006",
                f"для циклу №{loop_spec.index} не задано інваріант",
                loop_spec.loop.cond.span,
                hint="додайте перед циклом рядок '//@ inv: <твердження>' або параметр --inv; "
                     "інваріант - це індуктивне твердження, асистент його перевіряє, "
                     "але не вигадує",
            ))
    return Spec(conj(*pre_parts), conj(*post_parts), loops, has_post=bool(post_parts))


# -- генерація умов перевірки -------------------------------------------------------
@dataclass
class VCGenerator:
    spec: Spec
    vcs: list[VC] = field(default_factory=list)

    def _add(self, kind: str, title: str, hyps: list[ast.Cond], goal: ast.Cond,
             loop: int | None, span: SourceSpan | None) -> None:
        hyps = [h for h in hyps if h != TRUE]
        for existing in self.vcs:
            if (existing.kind, existing.loop, existing.hypotheses, existing.goal) == (
                    kind, loop, hyps, goal):
                return
        self.vcs.append(VC(f"VC{len(self.vcs) + 1}", kind, title, hyps, goal, loop, span))

    def wp(self, stmt: ast.Stmt, post: ast.Cond, context: str = "") -> ast.Cond:
        if isinstance(stmt, ast.Skip):
            return post
        if isinstance(stmt, ast.Assign):
            return subst_cond(post, stmt.target.name, stmt.value)
        if isinstance(stmt, ast.Block):
            return self.wp(stmt.body, post, context)
        if isinstance(stmt, ast.Seq):
            for part in reversed(stmt.statements):
                post = self.wp(part, post, context)
            return post
        if isinstance(stmt, ast.If):
            then = self.wp(stmt.then, post, context)
            orelse = self.wp(stmt.orelse, post, context)
            if then == orelse:
                return then
            return conj(ast.BoolOp("->", stmt.cond, then),
                        ast.BoolOp("->", ast.Not(stmt.cond), orelse))
        if isinstance(stmt, ast.While):
            return self._loop(stmt, post, context)
        raise TypeError(f"невідомий оператор: {type(stmt).__name__}")

    def _loop(self, loop: ast.While, post: ast.Cond, context: str) -> ast.Cond:
        ls = self.spec.loop_spec(loop)
        inv = ls.invariant
        assert inv is not None
        n, span, cond = ls.index, loop.cond.span, loop.cond
        self._add("preserve",
                  f"Крок індукції для циклу №{n}: тіло зберігає інваріант",
                  [inv, cond], self.wp(loop.body, inv, f" у тілі циклу №{n}"), n, span)
        # Правило рамки: частини післяумови, що не згадують змінених у циклі
        # змінних, цикл зберігає автоматично - їх не треба дублювати в інваріанті.
        changed = set(assigned_vars(loop))
        frame = [c for c in conjuncts(post) if not changed & set(free_vars(c))]
        rest = conj(*[c for c in conjuncts(post) if changed & set(free_vars(c))])
        if rest != TRUE:
            self._add("exit",
                      f"Вихід із циклу №{n}: інваріант і хибність умови дають потрібне "
                      f"твердження{context}",
                      [inv, ast.Not(cond)], rest, n, span)
        if ls.variant is not None:
            used = set(free_vars(loop, inv, ls.variant, post, self.spec.pre))
            ghost = f"v0_{n}"
            while ghost in used:
                ghost = "_" + ghost
            v0 = ast.Var(ghost)
            self._add("variant_bound",
                      f"Завершуваність циклу №{n}: варіант невід'ємний, поки цикл триває",
                      [inv, cond], ast.Compare(">=", ls.variant, ast.Num(0)), n, span)
            decreased = self.wp(loop.body, ast.Compare("<", ls.variant, v0),
                                f" (спадання варіанта циклу №{n})")
            self._add("variant_decrease",
                      f"Завершуваність циклу №{n}: кожна ітерація зменшує варіант",
                      [inv, cond, ast.Compare("=", ls.variant, v0)], decreased, n, span)
        return conj(inv, *frame)


def generate_vcs(program: ast.Program, spec: Spec) -> list[VC]:
    """Усі умови перевірки для програми зі специфікацією."""
    gen = VCGenerator(spec)
    start = gen.wp(program.body, spec.post)
    first = min((l.index for l in spec.loops), default=None)
    title = ("База індукції: передумова забезпечує інваріант на вході в цикл"
             if first is not None else "Передумова забезпечує післяумову")
    entry = VC("VC0", "entry", title, [h for h in [spec.pre] if h != TRUE], start, None,
               program.span)
    vcs = [entry] + gen.vcs
    for i, vc in enumerate(vcs, 1):
        vc.id = f"VC{i}"
    return vcs


__all__ = ["LoopSpec", "Spec", "SpecError", "VC", "extract_spec", "generate_vcs", "subst_expr"]
