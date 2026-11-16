"""Обчислення семантичного терму на стані з покроковим поясненням.

Стан - часткове відображення "ім'я змінної -> ціле число". Значення терму
на стані може бути **невизначеним**:

* терм звертається до змінної, якої немає у стані (``x⇒`` не визначена);
* цикл не завершується. Зациклення алгоритмічно нерозв'язне, тому
  обчислювач лише обмежує кількість кроків і чесно повідомляє, що в межах
  ліміту значення не отримано.

Правила переходів (``st`` - стан, ``∇`` - накладання):

* ``n̄(st) = n``;  ``x⇒(st) = st(x)``
* ``S^n(f, g1..gn)(st) = f(g1(st), ..., gn(st))``
* ``AS^x(t)(st) = st ∇ [x ↦ t(st)]``
* ``(f • g)(st) = g(f(st))``
* ``IF(b, f, g)(st) = f(st)``, якщо ``b(st)`` істинне, інакше ``g(st)``
* ``WH(b, f)(st) = st``, якщо ``b(st)`` хибне, інакше ``WH(b, f)(f(st))``
* ``id(st) = st``
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from ..source import SourceSpan
from .terms import AS, IF, WH, Apply, Comp, Const, Deref, Id, Term, render

State = dict[str, int]
Value = int | bool
DEFAULT_MAX_STEPS = 10_000

_FUNCS: dict[str, Callable[..., Value]] = {
    "add": lambda a, b: a + b,
    "sub": lambda a, b: a - b,
    "mult": lambda a, b: a * b,
    "eq": lambda a, b: a == b,
    "gr": lambda a, b: a > b,
    "or": lambda a, b: a or b,
    "neg": lambda a: not a,
}
_ARG_KIND = {"add": int, "sub": int, "mult": int, "eq": int, "gr": int, "or": bool, "neg": bool}


class Undefined(Exception):
    """Значення терму на стані не визначене."""

    def __init__(self, kind: str, message: str, span: SourceSpan | None = None):
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.span = span

    def to_dict(self) -> dict:
        return {"kind": self.kind, "message": self.message,
                "span": self.span.to_dict() if self.span else None}


@dataclass(frozen=True)
class Step:
    """Одне застосування правила."""

    index: int
    depth: int
    rule: str
    term: str
    state: State
    result: Value | State
    note: str
    origin: SourceSpan | None = None

    def to_dict(self) -> dict:
        return {"index": self.index, "depth": self.depth, "rule": self.rule, "term": self.term,
                "state": dict(self.state), "result": self.result, "note": self.note,
                "origin": self.origin.to_dict() if self.origin else None}


@dataclass
class EvalResult:
    defined: bool
    state: State | None
    reason: Undefined | None
    steps: list[Step] = field(default_factory=list)
    transitions: int = 0  # кількість застосувань AS / IF / WH / id

    def to_dict(self) -> dict:
        return {
            "defined": self.defined,
            "state": dict(sorted(self.state.items())) if self.state is not None else None,
            "undefined": self.reason.to_dict() if self.reason else None,
            "transitions": self.transitions,
            "trace": [s.to_dict() for s in self.steps],
        }


def format_state(state: State) -> str:
    if not state:
        return "[]"
    return "[" + ", ".join(f"{k} ↦ {v}" for k, v in sorted(state.items())) + "]"


def _show(value: Value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


class Evaluator:
    def __init__(self, *, max_steps: int = DEFAULT_MAX_STEPS, trace: bool = True,
                 detail: bool = False, style: str = "unicode",
                 observer: Callable[[str, Term, State], None] | None = None):
        self.max_steps = max_steps
        self.trace = trace
        self.detail = detail
        self.style = style
        self.observer = observer
        self.steps: list[Step] = []
        self.transitions = 0

    def _log(self, depth: int, rule: str, term: Term, state: State,
             result: Value | State, note: str) -> None:
        if self.trace:
            snapshot = dict(result) if isinstance(result, dict) else result
            self.steps.append(Step(len(self.steps) + 1, depth, rule, render(term, self.style),
                                   dict(state), snapshot, note, term.origin))

    def _tick(self, term: Term) -> None:
        self.transitions += 1
        if self.transitions > self.max_steps:
            raise Undefined(
                "step_limit",
                f"значення не отримано в межах {self.max_steps} кроків - імовірно, цикл "
                "не завершується на цьому стані (це обмеження обчислювача, а не доведення "
                "зациклення)",
                term.origin,
            )

    # -- числові та логічні терми --------------------------------------------
    def value(self, term: Term, state: State, depth: int = 0) -> Value:
        if isinstance(term, Const):
            if self.detail:
                self._log(depth, "const", term, state, term.value,
                          f"стала: {term.value}")
            return term.value
        if isinstance(term, Deref):
            if term.name not in state:
                raise Undefined(
                    "undefined_variable",
                    f"змінна '{term.name}' не має значення у стані {format_state(state)}",
                    term.origin,
                )
            if self.detail:
                self._log(depth, "deref", term, state, state[term.name],
                          f"{term.name}⇒(st) = {state[term.name]}")
            return state[term.name]
        if isinstance(term, Apply):
            if term.func not in _FUNCS:
                raise ValueError(f"невідома базова функція: {term.func}")
            args = [self.value(a, state, depth + 1) for a in term.args]
            kind = _ARG_KIND[term.func]
            for arg in args:
                if isinstance(arg, bool) != (kind is bool):
                    raise ValueError(
                        f"функція {term.func} застосована до аргументу не того типу: {arg!r}")
            result = _FUNCS[term.func](*args)
            if self.detail:
                shown = ", ".join(_show(a) for a in args)
                self._log(depth, "S", term, state, result,
                          f"{term.func}({shown}) = {_show(result)}")
            return result
        raise ValueError(f"терм не задає значення: {render(term, self.style)}")

    # -- терми-перетворювачі станів ------------------------------------------
    def apply(self, term: Term, state: State, depth: int = 0) -> State:
        if isinstance(term, Comp):
            for part in term.parts:
                state = self.apply(part, state, depth)
            return state
        if isinstance(term, Id):
            self._tick(term)
            self._log(depth, "id", term, state, state, "id(st) = st")
            return state
        if isinstance(term, AS):
            self._tick(term)
            value = self.value(term.value, state, depth + 1)
            if isinstance(value, bool):
                raise ValueError("присвоїти можна лише числове значення")
            new_state = {**state, term.name: value}
            self._log(depth, "AS", term, state, new_state,
                      f"{render(term.value, self.style)}(st) = {value}; "
                      f"st ∇ [{term.name} ↦ {value}]")
            return new_state
        if isinstance(term, IF):
            self._tick(term)
            cond = self.value(term.cond, state, depth + 1)
            branch = term.then if cond else term.orelse
            self._log(depth, "IF-true" if cond else "IF-false", term, state, state,
                      f"умова {render(term.cond, self.style)}(st) = {_show(cond)} → "
                      f"{'перша' if cond else 'друга'} гілка")
            return self.apply(branch, state, depth + 1)
        if isinstance(term, WH):
            iteration = 0
            while True:
                self._tick(term)
                if self.observer:
                    self.observer("loop_head", term, state)
                cond = self.value(term.cond, state, depth + 1)
                shown = f"умова {render(term.cond, self.style)}(st) = {_show(cond)}"
                if not cond:
                    self._log(depth, "WH-false", term, state, state,
                              f"{shown} → WH(b, f)(st) = st, цикл завершено після "
                              f"{iteration} ітерацій")
                    if self.observer:
                        self.observer("loop_exit", term, state)
                    return state
                iteration += 1
                self._log(depth, "WH-true", term, state, state,
                          f"{shown} → WH(b, f)(st) = WH(b, f)(f(st)), ітерація {iteration}")
                state = self.apply(term.body, state, depth + 1)
        raise ValueError(f"терм не є перетворювачем станів: {render(term, self.style)}")


def evaluate(term: Term, state: State | None = None, *, max_steps: int = DEFAULT_MAX_STEPS,
             trace: bool = True, detail: bool = False, style: str = "unicode",
             observer: Callable[[str, Term, State], None] | None = None) -> EvalResult:
    """Застосовує терм програми до стану.

    Повертає кінцевий стан або причину невизначеності разом зі слідом
    застосованих правил. ``detail=True`` додає до сліду кроки обчислення
    виразів (сталі, розіменування, суперпозиції).
    """
    ev = Evaluator(max_steps=max_steps, trace=trace, detail=detail, style=style,
                   observer=observer)
    try:
        final = ev.apply(term, dict(state or {}))
    except Undefined as reason:
        return EvalResult(False, None, reason, ev.steps, ev.transitions)
    return EvalResult(True, final, None, ev.steps, ev.transitions)


def format_trace(result: EvalResult) -> str:
    """Людиночитаний слід обчислення."""
    lines = []
    for step in result.steps:
        pad = "  " * step.depth
        lines.append(f"{step.index:>4}. {pad}[{step.rule}] {step.note}")
        if isinstance(step.result, dict) and step.result != step.state:
            lines.append(f"      {pad}st = {format_state(step.result)}")
    return "\n".join(lines)
