"""Операції над твердженнями: підстановка, вільні змінні, обчислення на стані."""

from __future__ import annotations

from .. import ast

TRUE = ast.BoolConst(True)
FALSE = ast.BoolConst(False)
_MAX_EXPONENT = 64


class EvalError(Exception):
    """Твердження не має значення на стані (напр. від'ємний показник степеня)."""


def conj(*parts: ast.Cond) -> ast.Cond:
    """Кон'юнкція без тривіальних ``true``."""
    result: ast.Cond | None = None
    for part in parts:
        if part == TRUE:
            continue
        result = part if result is None else ast.BoolOp("and", result, part)
    return result if result is not None else TRUE


def conjuncts(cond: ast.Cond) -> list[ast.Cond]:
    if isinstance(cond, ast.BoolOp) and cond.op == "and":
        return conjuncts(cond.left) + conjuncts(cond.right)
    return [] if cond == TRUE else [cond]


def subst_expr(e: ast.Expr, name: str, repl: ast.Expr) -> ast.Expr:
    if isinstance(e, ast.Var):
        return repl if e.name == name else e
    if isinstance(e, ast.BinOp):
        return ast.BinOp(e.op, subst_expr(e.left, name, repl), subst_expr(e.right, name, repl),
                         span=e.span)
    return e


def subst_cond(c: ast.Cond, name: str, repl: ast.Expr) -> ast.Cond:
    """``c[repl / name]`` - заміна всіх входжень змінної виразом."""
    if isinstance(c, ast.Compare):
        return ast.Compare(c.op, subst_expr(c.left, name, repl), subst_expr(c.right, name, repl),
                           span=c.span)
    if isinstance(c, ast.BoolOp):
        return ast.BoolOp(c.op, subst_cond(c.left, name, repl), subst_cond(c.right, name, repl),
                          span=c.span)
    if isinstance(c, ast.Not):
        return ast.Not(subst_cond(c.operand, name, repl), span=c.span)
    return c


def free_vars(*nodes: ast.Node) -> list[str]:
    names = {n.name for node in nodes for n in node.walk() if isinstance(n, ast.Var)}
    return sorted(names)


def assigned_vars(stmt: ast.Node) -> list[str]:
    return sorted({n.target.name for n in stmt.walk() if isinstance(n, ast.Assign)})


def eval_expr(e: ast.Expr, state: dict[str, int]) -> int:
    if isinstance(e, ast.Num):
        return e.value
    if isinstance(e, ast.Var):
        try:
            return state[e.name]
        except KeyError:
            raise EvalError(f"змінна '{e.name}' не має значення") from None
    left, right = eval_expr(e.left, state), eval_expr(e.right, state)
    if e.op == "+":
        return left + right
    if e.op == "-":
        return left - right
    if e.op == "*":
        return left * right
    if e.op == "^":
        if right < 0 or right > _MAX_EXPONENT:
            raise EvalError(f"показник степеня {right} поза межами 0..{_MAX_EXPONENT}")
        return left ** right
    raise ValueError(f"невідома операція {e.op!r}")


_COMPARE = {
    "=": lambda a, b: a == b, "!=": lambda a, b: a != b, ">": lambda a, b: a > b,
    "<": lambda a, b: a < b, ">=": lambda a, b: a >= b, "<=": lambda a, b: a <= b,
}


def eval_cond(c: ast.Cond, state: dict[str, int]) -> bool:
    if isinstance(c, ast.BoolConst):
        return c.value
    if isinstance(c, ast.Compare):
        return _COMPARE[c.op](eval_expr(c.left, state), eval_expr(c.right, state))
    if isinstance(c, ast.Not):
        return not eval_cond(c.operand, state)
    if c.op == "or":
        return eval_cond(c.left, state) or eval_cond(c.right, state)
    if c.op == "and":
        return eval_cond(c.left, state) and eval_cond(c.right, state)
    if c.op == "->":
        return (not eval_cond(c.left, state)) or eval_cond(c.right, state)
    raise ValueError(f"невідома зв'язка {c.op!r}")
