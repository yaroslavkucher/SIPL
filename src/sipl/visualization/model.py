"""Спільна модель дерева для всіх форматів виводу.

І дерево синтаксичного виводу, і AST спершу зводяться до :class:`VizNode`;
текстовий, JSON-, DOT-, SVG- та HTML-рендерери працюють лише з цією моделлю,
тож усі подання гарантовано породжені з тих самих реальних структур.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterator

from .. import ast
from ..parse_tree import ParseNode
from ..source import SourceSpan


@dataclass
class VizNode:
    id: int
    label: str
    kind: str  # nonterminal | terminal | program | statement | expression | condition
    span: SourceSpan
    text: str  # фрагмент вихідного коду, якому відповідає вузол
    detail: str = ""  # правило БНФ або тип вузла AST
    children: list["VizNode"] = field(default_factory=list)

    def walk(self) -> Iterator["VizNode"]:
        yield self
        for child in self.children:
            yield from child.walk()

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "kind": self.kind,
            "detail": self.detail,
            "span": self.span.to_dict(),
            "text": self.text,
            "children": [c.to_dict() for c in self.children],
        }


class _Counter:
    def __init__(self) -> None:
        self.value = 0

    def next(self) -> int:
        self.value += 1
        return self.value


def from_parse_tree(root: ParseNode, source: str) -> VizNode:
    """Модель дерева синтаксичного виводу."""
    counter = _Counter()

    def convert(node: ParseNode) -> VizNode:
        viz = VizNode(
            counter.next(),
            node.symbol if node.terminal else f"<{node.symbol}>",
            "terminal" if node.terminal else "nonterminal",
            node.span,
            source[node.span.start:node.span.end],
            f"лексема {node.token_kind}" if node.terminal else (node.rule or ""),
        )
        viz.children = [convert(c) for c in node.children]
        return viz

    return convert(root)


_AST_KIND = {
    ast.Program: "program", ast.Assign: "statement", ast.Seq: "statement",
    ast.If: "statement", ast.While: "statement", ast.Block: "statement",
    ast.Skip: "statement", ast.Num: "expression", ast.Var: "expression",
    ast.BinOp: "expression", ast.Compare: "condition", ast.BoolOp: "condition",
    ast.Not: "condition", ast.BoolConst: "condition",
}


def _ast_label(node: ast.Node) -> str:
    if isinstance(node, ast.Num):
        return str(node.value)
    if isinstance(node, ast.Var):
        return node.name
    if isinstance(node, (ast.BinOp, ast.Compare, ast.BoolOp)):
        return node.op
    if isinstance(node, ast.BoolConst):
        return "true" if node.value else "false"
    return {
        ast.Program: "Program", ast.Assign: ":=", ast.Seq: ";", ast.If: "if",
        ast.While: "while", ast.Block: "begin-end", ast.Skip: "skip", ast.Not: "not",
    }[type(node)]


def from_ast(root: ast.Node, source: str) -> VizNode:
    """Модель абстрактного синтаксичного дерева."""
    counter = _Counter()

    def convert(node: ast.Node) -> VizNode:
        viz = VizNode(counter.next(), _ast_label(node), _AST_KIND[type(node)], node.span,
                      source[node.span.start:node.span.end], type(node).__name__)
        viz.children = [convert(c) for c in node.children()]
        return viz

    return convert(root)
