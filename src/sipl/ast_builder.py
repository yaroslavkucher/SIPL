"""Перетворення дерева синтаксичного виводу на AST.

Абстрактний синтаксис відкидає те, що потрібне лише для запису: дужки,
ключові слова, ланцюжки ``<вираз> -> <змінна> -> X``; вкладені ``;``
вирівнюються в один вузол :class:`sipl.ast.Seq`.
"""

from __future__ import annotations

from . import ast
from .parse_tree import ParseNode


def build_program(tree: ParseNode) -> ast.Program:
    _begin, body, _end = tree.children
    return ast.Program(build_statement(body), span=tree.span)


def build_statement(node: ParseNode) -> ast.Stmt:
    kids = node.children
    head = kids[0]
    if not head.terminal and head.symbol == "змінна":
        target = ast.Var(head.children[0].symbol, span=head.span)
        return ast.Assign(target, build_expression(kids[2]), span=node.span)
    if not head.terminal:  # <оператор> ; <оператор>
        parts: list[ast.Stmt] = []
        for side in (kids[0], kids[2]):
            stmt = build_statement(side)
            # вирівнюємо лише "сиру" послідовність, а не begin ... end
            if isinstance(stmt, ast.Seq):
                parts.extend(stmt.statements)
            else:
                parts.append(stmt)
        return ast.Seq(tuple(parts), span=node.span)
    if head.symbol == "skip":
        return ast.Skip(span=node.span)
    if head.symbol == "begin":
        return ast.Block(build_statement(kids[1]), span=node.span)
    if head.symbol == "if":
        return ast.If(
            build_condition(kids[1]), build_statement(kids[3]), build_statement(kids[5]),
            span=node.span,
        )
    if head.symbol == "while":
        return ast.While(build_condition(kids[1]), build_statement(kids[3]), span=node.span)
    raise ValueError(f"невідоме правило оператора: {node.rule}")


def build_expression(node: ParseNode) -> ast.Expr:
    kids = node.children
    if len(kids) == 1:
        leaf = kids[0]
        text = leaf.children[0].symbol
        if leaf.symbol == "число":
            return ast.Num(int(text), span=node.span)
        return ast.Var(text, span=node.span)
    if kids[0].terminal:  # ( <вираз> )
        return build_expression(kids[1])
    return ast.BinOp(
        kids[1].token_kind,
        build_expression(kids[0]),
        build_expression(kids[2]),
        span=node.span,
    )


def build_condition(node: ParseNode) -> ast.Cond:
    kids = node.children
    if len(kids) == 1:
        return ast.BoolConst(kids[0].symbol == "true", span=node.span)
    if len(kids) == 2:
        return ast.Not(build_condition(kids[1]), span=node.span)
    if kids[0].terminal:  # ( <умова> )
        return build_condition(kids[1])
    op = kids[1].token_kind
    if kids[0].symbol == "умова":
        return ast.BoolOp(op, build_condition(kids[0]), build_condition(kids[2]), span=node.span)
    return ast.Compare(op, build_expression(kids[0]), build_expression(kids[2]), span=node.span)
