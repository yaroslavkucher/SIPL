"""Дерево синтаксичного виводу (конкретний синтаксис).

Вузли-нетермінали названі так само, як у БНФ курсу: ``програма``,
``оператор``, ``вираз``, ``умова``, ``змінна``, ``число``. Кожен нетермінал
пам'ятає правило БНФ, яке до нього застосовано, тож дерево є записом
виведення програми з аксіоми ``<програма>``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterator

from .source import SourceSpan

PROGRAM = "програма"
STATEMENT = "оператор"
EXPRESSION = "вираз"
CONDITION = "умова"
VARIABLE = "змінна"
NUMBER = "число"


@dataclass
class ParseNode:
    symbol: str  # назва нетермінала або текст термінала
    span: SourceSpan
    children: list["ParseNode"] = field(default_factory=list)
    terminal: bool = False
    token_kind: str | None = None  # для терміналів: тип лексеми

    @property
    def rule(self) -> str | None:
        """Застосоване правило БНФ, напр. ``<оператор> ::= <змінна> := <вираз>``."""
        if self.terminal:
            return None
        rhs = " ".join(c.symbol if c.terminal else f"<{c.symbol}>" for c in self.children)
        return f"<{self.symbol}> ::= {rhs}"

    def walk(self) -> Iterator["ParseNode"]:
        yield self
        for child in self.children:
            yield from child.walk()

    def leaves(self) -> list["ParseNode"]:
        return [n for n in self.walk() if n.terminal]

    def to_dict(self) -> dict:
        out: dict = {"symbol": self.symbol, "terminal": self.terminal, "span": self.span.to_dict()}
        if self.terminal:
            out["token"] = self.token_kind
        else:
            out["rule"] = self.rule
            out["children"] = [c.to_dict() for c in self.children]
        return out
