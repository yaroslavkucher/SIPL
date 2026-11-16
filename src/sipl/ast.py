"""Типізоване абстрактне синтаксичне дерево SIPL.

Вузли незмінні; ``span`` не бере участі в порівнянні, тому ``==`` означає
структурну рівність дерев незалежно від форматування тексту.

Вузли ``BoolOp('and' | '->')``, ``BoolConst``, ``BinOp('^')`` та порівняння
``<, >=, <=, !=`` належать лише мові тверджень (перед-/післяумови,
інваріанти) і в програмах SIPL не з'являються.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from typing import Iterator, Union

from .source import SourceSpan

_NO_SPAN = SourceSpan(0, 0, 1, 1, 1, 1)


def _span_field():
    return field(default=_NO_SPAN, compare=False, repr=False, kw_only=True)


@dataclass(frozen=True)
class Node:
    span: SourceSpan = _span_field()

    def children(self) -> Iterator["Node"]:
        """Безпосередні дочірні вузли у порядку запису."""
        for f in fields(self):
            if f.name == "span":
                continue
            value = getattr(self, f.name)
            if isinstance(value, Node):
                yield value
            elif isinstance(value, tuple):
                for item in value:
                    if isinstance(item, Node):
                        yield item

    def walk(self) -> Iterator["Node"]:
        yield self
        for child in self.children():
            yield from child.walk()

    def to_dict(self) -> dict:
        """Стабільне JSON-подання: ``type`` + поля вузла + ``span``."""
        out: dict = {"type": type(self).__name__}
        for f in fields(self):
            if f.name == "span":
                continue
            value = getattr(self, f.name)
            if isinstance(value, Node):
                value = value.to_dict()
            elif isinstance(value, tuple):
                value = [v.to_dict() if isinstance(v, Node) else v for v in value]
            out[f.name] = value
        out["span"] = self.span.to_dict()
        return out


# -- арифметичні вирази ---------------------------------------------------
@dataclass(frozen=True)
class Num(Node):
    value: int


@dataclass(frozen=True)
class Var(Node):
    name: str


@dataclass(frozen=True)
class BinOp(Node):
    op: str  # '+', '-', '*'  (та '^' у мові тверджень)
    left: "Expr"
    right: "Expr"


Expr = Union[Num, Var, BinOp]


# -- умови ----------------------------------------------------------------
@dataclass(frozen=True)
class Compare(Node):
    op: str  # '=', '>'  (та '<', '>=', '<=', '!=' у мові тверджень)
    left: Expr
    right: Expr


@dataclass(frozen=True)
class BoolOp(Node):
    op: str  # 'or'  (та 'and', '->' у мові тверджень)
    left: "Cond"
    right: "Cond"


@dataclass(frozen=True)
class Not(Node):
    operand: "Cond"


@dataclass(frozen=True)
class BoolConst(Node):
    value: bool


Cond = Union[Compare, BoolOp, Not, BoolConst]


# -- оператори ------------------------------------------------------------
@dataclass(frozen=True)
class Assign(Node):
    target: Var
    value: Expr


@dataclass(frozen=True)
class Seq(Node):
    """Послідовне виконання ``S1; S2; ...; Sn`` (n >= 2), вирівняне у список."""

    statements: tuple["Stmt", ...]


@dataclass(frozen=True)
class If(Node):
    cond: Cond
    then: "Stmt"
    orelse: "Stmt"


@dataclass(frozen=True)
class While(Node):
    cond: Cond
    body: "Stmt"


@dataclass(frozen=True)
class Block(Node):
    """``begin S end`` як оператор."""

    body: "Stmt"


@dataclass(frozen=True)
class Skip(Node):
    pass


Stmt = Union[Assign, Seq, If, While, Block, Skip]


@dataclass(frozen=True)
class Program(Node):
    body: Stmt


# -- запис назад у текст --------------------------------------------------
_EXPR_PREC = {"+": 1, "-": 1, "*": 2, "^": 3}
_BOOL_PREC = {"->": 1, "or": 2, "and": 3}


def expr_to_source(e: Expr, parent_prec: int = 0, right_side: bool = False) -> str:
    if isinstance(e, Num):
        return str(e.value) if e.value >= 0 or parent_prec == 0 else f"({e.value})"
    if isinstance(e, Var):
        return e.name
    prec = _EXPR_PREC[e.op]
    if e.op == "^":
        text = f"{expr_to_source(e.left, prec, True)}^{expr_to_source(e.right, prec, False)}"
    else:
        text = f"{expr_to_source(e.left, prec, False)} {e.op} {expr_to_source(e.right, prec, True)}"
    if prec < parent_prec or (prec == parent_prec and right_side):
        return f"({text})"
    return text


def cond_to_source(c: Cond, parent_prec: int = 0, right_side: bool = False) -> str:
    if isinstance(c, BoolConst):
        return "true" if c.value else "false"
    if isinstance(c, Compare):
        return f"{expr_to_source(c.left)} {c.op} {expr_to_source(c.right)}"
    if isinstance(c, Not):
        inner = cond_to_source(c.operand, 0)
        return f"not ({inner})" if not isinstance(c.operand, (BoolConst, Not)) else f"not {inner}"
    prec = _BOOL_PREC[c.op]
    if c.op == "->":
        text = f"{cond_to_source(c.left, prec, True)} -> {cond_to_source(c.right, prec, False)}"
    else:
        text = f"{cond_to_source(c.left, prec, False)} {c.op} {cond_to_source(c.right, prec, True)}"
    if prec < parent_prec or (prec == parent_prec and right_side):
        return f"({text})"
    return text


def stmt_to_source(s: Stmt, indent: int = 0) -> str:
    pad = "  " * indent
    if isinstance(s, Assign):
        return f"{pad}{s.target.name} := {expr_to_source(s.value)}"
    if isinstance(s, Skip):
        return f"{pad}skip"
    if isinstance(s, Seq):
        return ";\n".join(stmt_to_source(x, indent) for x in s.statements)
    if isinstance(s, Block):
        return f"{pad}begin\n{stmt_to_source(s.body, indent + 1)}\n{pad}end"
    if isinstance(s, If):
        return (
            f"{pad}if {cond_to_source(s.cond)} then\n{_single(s.then, indent + 1)}\n"
            f"{pad}else\n{_single(s.orelse, indent + 1)}"
        )
    if isinstance(s, While):
        return f"{pad}while {cond_to_source(s.cond)} do\n{_single(s.body, indent + 1)}"
    raise TypeError(f"невідомий вузол: {type(s).__name__}")


def _single(s: Stmt, indent: int) -> str:
    """Гілка if / тіло while - один оператор, тож послідовність беремо в блок."""
    return stmt_to_source(Block(s) if isinstance(s, Seq) else s, indent)


def program_to_source(p: Program) -> str:
    return f"begin\n{stmt_to_source(p.body, 1)}\nend\n"
