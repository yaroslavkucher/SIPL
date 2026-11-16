"""Семантичні терми SIPL у нотації курсу.

Програмі зіставляється терм композиційної семантики - вираз, складений із
базових функцій та композицій:

==================  ==========================  ===========================
Конструкція         Терм                        Зміст
==================  ==========================  ===========================
число ``n``         ``n̄``                       стала функція
змінна ``x``        ``x⇒``                      розіменування
``e1 + e2``         ``S^2(add, t1, t2)``        суперпозиція (також sub, mult)
``e1 = e2``         ``S^2(eq, t1, t2)``         (також gr для ``>``)
``b1 or b2``        ``S^2(or, t1, t2)``
``not b``           ``S^1(neg, t)``
``x := e``          ``AS^x(t)``                 присвоювання
``S1 ; S2``         ``f1 • f2``                 послідовне виконання
``if``              ``IF(b, f1, f2)``           розгалуження
``while``           ``WH(b, f)``                цикл
``skip``            ``id``                      тотожне перетворення
==================  ==========================  ===========================
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .. import ast
from ..source import SourceSpan

FUNCTIONS = {"+": "add", "-": "sub", "*": "mult", "=": "eq", ">": "gr", "or": "or"}
ARITY = {"add": 2, "sub": 2, "mult": 2, "eq": 2, "gr": 2, "or": 2, "neg": 1}


def _origin():
    return field(default=None, compare=False, repr=False, kw_only=True)


@dataclass(frozen=True)
class Term:
    origin: SourceSpan | None = _origin()


@dataclass(frozen=True)
class Const(Term):
    """``n̄`` - функція, що на будь-якому стані дає ``n``."""

    value: int


@dataclass(frozen=True)
class Deref(Term):
    """``x⇒`` - значення змінної ``x`` у стані."""

    name: str


@dataclass(frozen=True)
class Apply(Term):
    """``S^n(f, g1, ..., gn)`` - суперпозиція n-арної функції ``f``."""

    func: str
    args: tuple[Term, ...]


@dataclass(frozen=True)
class AS(Term):
    """``AS^x(t)`` - присвоювання змінній ``x`` значення терму ``t``."""

    name: str
    value: Term


@dataclass(frozen=True)
class Comp(Term):
    """``f1 • f2 • ... • fn`` - послідовне виконання зліва направо."""

    parts: tuple[Term, ...]


@dataclass(frozen=True)
class IF(Term):
    cond: Term
    then: Term
    orelse: Term


@dataclass(frozen=True)
class WH(Term):
    cond: Term
    body: Term


@dataclass(frozen=True)
class Id(Term):
    pass


# -- побудова з AST ---------------------------------------------------------
@dataclass(frozen=True)
class ConstructionStep:
    """Один крок рекурсивної побудови: яке правило до якого фрагмента."""

    rule: str
    span: SourceSpan
    term: Term


class TermBuilder:
    """Рекурсивно будує терм за AST і записує застосовані правила."""

    def __init__(self) -> None:
        self.steps: list[ConstructionStep] = []

    def _emit(self, rule: str, node: ast.Node, term: Term) -> Term:
        self.steps.append(ConstructionStep(rule, node.span, term))
        return term

    def program(self, node: ast.Program) -> Term:
        return self._emit("sem_P(begin S end) = sem_S(S)", node, self.statement(node.body))

    def statement(self, node: ast.Stmt) -> Term:
        if isinstance(node, ast.Assign):
            term = AS(node.target.name, self.expression(node.value), origin=node.span)
            return self._emit("sem_S(x := e) = AS^x(sem_A(e))", node, term)
        if isinstance(node, ast.Seq):
            parts: list[Term] = []
            for stmt in node.statements:
                part = self.statement(stmt)
                # композиція асоціативна, тому вкладені • вирівнюємо
                parts.extend(part.parts if isinstance(part, Comp) else [part])
            return self._emit("sem_S(S1 ; S2) = sem_S(S1) • sem_S(S2)", node,
                              Comp(tuple(parts), origin=node.span))
        if isinstance(node, ast.If):
            term = IF(self.condition(node.cond), self.statement(node.then),
                      self.statement(node.orelse), origin=node.span)
            return self._emit(
                "sem_S(if b then S1 else S2) = IF(sem_B(b), sem_S(S1), sem_S(S2))", node, term)
        if isinstance(node, ast.While):
            term = WH(self.condition(node.cond), self.statement(node.body), origin=node.span)
            return self._emit("sem_S(while b do S) = WH(sem_B(b), sem_S(S))", node, term)
        if isinstance(node, ast.Block):
            return self._emit("sem_S(begin S end) = sem_S(S)", node, self.statement(node.body))
        if isinstance(node, ast.Skip):
            return self._emit("sem_S(skip) = id", node, Id(origin=node.span))
        raise TypeError(f"невідомий оператор: {type(node).__name__}")

    def expression(self, node: ast.Expr) -> Term:
        if isinstance(node, ast.Num):
            return self._emit("sem_A(n) = n̄", node, Const(node.value, origin=node.span))
        if isinstance(node, ast.Var):
            return self._emit("sem_A(x) = x⇒", node, Deref(node.name, origin=node.span))
        if isinstance(node, ast.BinOp) and node.op in FUNCTIONS:
            func = FUNCTIONS[node.op]
            term = Apply(func, (self.expression(node.left), self.expression(node.right)),
                         origin=node.span)
            return self._emit(
                f"sem_A(e1 {node.op} e2) = S^2({func}, sem_A(e1), sem_A(e2))", node, term)
        raise ValueError(f"вираз не належить базовій SIPL: {ast.expr_to_source(node)}")

    def condition(self, node: ast.Cond) -> Term:
        if isinstance(node, ast.Compare) and node.op in ("=", ">"):
            func = FUNCTIONS[node.op]
            term = Apply(func, (self.expression(node.left), self.expression(node.right)),
                         origin=node.span)
            return self._emit(
                f"sem_B(e1 {node.op} e2) = S^2({func}, sem_A(e1), sem_A(e2))", node, term)
        if isinstance(node, ast.BoolOp) and node.op == "or":
            term = Apply("or", (self.condition(node.left), self.condition(node.right)),
                         origin=node.span)
            return self._emit("sem_B(b1 ∨ b2) = S^2(or, sem_B(b1), sem_B(b2))", node, term)
        if isinstance(node, ast.Not):
            term = Apply("neg", (self.condition(node.operand),), origin=node.span)
            return self._emit("sem_B(¬b) = S^1(neg, sem_B(b))", node, term)
        raise ValueError(f"умова не належить базовій SIPL: {ast.cond_to_source(node)}")


def build_term(node: ast.Node) -> Term:
    """Семантичний терм програми, оператора, виразу або умови."""
    builder = TermBuilder()
    if isinstance(node, ast.Program):
        return builder.program(node)
    if isinstance(node, (ast.Num, ast.Var, ast.BinOp)):
        return builder.expression(node)
    if isinstance(node, (ast.Compare, ast.BoolOp, ast.Not, ast.BoolConst)):
        return builder.condition(node)
    return builder.statement(node)


def explain_construction(program: ast.Program) -> tuple[Term, list[ConstructionStep]]:
    """Терм програми та послідовність правил, якими його побудовано."""
    builder = TermBuilder()
    return builder.program(program), builder.steps


# -- запис терму --------------------------------------------------------------
STYLES = ("unicode", "ascii", "latex")
_OVERLINE = "̅"


def _const(value: int, style: str) -> str:
    if style == "unicode":
        return "".join(ch + _OVERLINE for ch in str(value))
    if style == "latex":
        return rf"\bar{{{value}}}"
    return f"const({value})"


def render(term: Term, style: str = "unicode", pretty: bool = False) -> str:
    """Запис терму. ``pretty=True`` розкладає композиції та цикли по рядках."""
    if style not in STYLES:
        raise ValueError(f"невідомий стиль {style!r}; доступні: {', '.join(STYLES)}")
    return _render(term, style, pretty, 0, False)


def _render(t: Term, style: str, pretty: bool, level: int, nested: bool) -> str:
    def sub(x: Term, lvl: int = level, nest: bool = True) -> str:
        return _render(x, style, pretty, lvl, nest)

    if isinstance(t, Const):
        return _const(t.value, style)
    if isinstance(t, Deref):
        return {"unicode": f"{t.name}⇒", "latex": rf"{t.name}{{\Rightarrow}}"}.get(
            style, f"{t.name}=>")
    if isinstance(t, Id):
        return "id"
    if isinstance(t, Apply):
        head = f"S^{{{len(t.args)}}}" if style == "latex" else f"S^{len(t.args)}"
        return f"{head}({t.func}, {', '.join(sub(a) for a in t.args)})"
    if isinstance(t, AS):
        head = f"AS^{{{t.name}}}" if style == "latex" else f"AS^{t.name}"
        return f"{head}({sub(t.value)})"
    bullet = {"unicode": "•", "latex": r"\bullet"}.get(style, "*")
    if isinstance(t, Comp):
        if pretty:
            pad = "  " * level
            text = f" {bullet}\n{pad}".join(sub(p) for p in t.parts)
        else:
            text = f" {bullet} ".join(sub(p) for p in t.parts)
        return f"({text})" if nested and not pretty else text
    if isinstance(t, (IF, WH)):
        name = "IF" if isinstance(t, IF) else "WH"
        args = [t.cond, t.then, t.orelse] if isinstance(t, IF) else [t.cond, t.body]
        if pretty:
            pad = "  " * (level + 1)
            inner = ",\n".join(pad + _render(a, style, pretty, level + 1, False) for a in args)
            return f"{name}(\n{inner}\n{'  ' * level})"
        return f"{name}({', '.join(_render(a, style, pretty, level, False) for a in args)})"
    raise TypeError(f"невідомий терм: {type(t).__name__}")


# -- JSON ----------------------------------------------------------------------
def to_dict(t: Term) -> dict:
    out: dict
    if isinstance(t, Const):
        out = {"kind": "const", "value": t.value}
    elif isinstance(t, Deref):
        out = {"kind": "deref", "name": t.name}
    elif isinstance(t, Apply):
        out = {"kind": "superposition", "func": t.func, "args": [to_dict(a) for a in t.args]}
    elif isinstance(t, AS):
        out = {"kind": "AS", "name": t.name, "value": to_dict(t.value)}
    elif isinstance(t, Comp):
        out = {"kind": "composition", "parts": [to_dict(p) for p in t.parts]}
    elif isinstance(t, IF):
        out = {"kind": "IF", "cond": to_dict(t.cond), "then": to_dict(t.then),
               "else": to_dict(t.orelse)}
    elif isinstance(t, WH):
        out = {"kind": "WH", "cond": to_dict(t.cond), "body": to_dict(t.body)}
    elif isinstance(t, Id):
        out = {"kind": "id"}
    else:
        raise TypeError(f"невідомий терм: {type(t).__name__}")
    out["origin"] = t.origin.to_dict() if t.origin else None
    return out


def from_dict(data: dict) -> Term:
    """Обернене до :func:`to_dict` (терм можна зберегти й обчислити пізніше)."""
    origin = SourceSpan.from_dict(data["origin"]) if data.get("origin") else None
    kind = data.get("kind")
    if kind == "const":
        return Const(int(data["value"]), origin=origin)
    if kind == "deref":
        return Deref(data["name"], origin=origin)
    if kind == "superposition":
        func = data["func"]
        args = tuple(from_dict(a) for a in data["args"])
        if func not in ARITY or ARITY[func] != len(args):
            raise ValueError(f"некоректна суперпозиція: {func} з {len(args)} аргументами")
        return Apply(func, args, origin=origin)
    if kind == "AS":
        return AS(data["name"], from_dict(data["value"]), origin=origin)
    if kind == "composition":
        return Comp(tuple(from_dict(p) for p in data["parts"]), origin=origin)
    if kind == "IF":
        return IF(from_dict(data["cond"]), from_dict(data["then"]), from_dict(data["else"]),
                  origin=origin)
    if kind == "WH":
        return WH(from_dict(data["cond"]), from_dict(data["body"]), origin=origin)
    if kind == "id":
        return Id(origin=origin)
    raise ValueError(f"невідомий вид терму: {kind!r}")
