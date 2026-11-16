"""Лексичний аналізатор SIPL.

Лексер розпізнає і лексеми базової мови, і лексеми розширеної мови
тверджень (``and``, ``<``, ``>=``, ``->``, ``^`` ...), якою записують
перед-/післяумови та інваріанти. Чи дозволена розширена лексема в даному
місці, вирішує парсер - так він може дати змістовну підказку замість
"невідомий символ".
"""

from __future__ import annotations

from dataclasses import dataclass

from .source import Diagnostic, SiplSyntaxError, SourceSpan

KEYWORDS = frozenset(
    {"begin", "end", "if", "then", "else", "while", "do", "skip", "or", "not", "and", "true", "false"}
)

# Юнікод-варіанти з нотації курсу зводяться до ASCII-лексем.
_UNICODE_ALIASES = {
    "∨": "or",
    "¬": "not",
    "∧": "and",
    "≥": ">=",
    "≤": "<=",
    "≠": "!=",
    "→": "->",
    "·": "*",
    "×": "*",
}

_OPERATORS = (":=", ">=", "<=", "!=", "->", ";", "+", "-", "*", "(", ")", "=", ">", "<", "^")

ANNOTATION_PREFIX = "//@"


@dataclass(frozen=True)
class Token:
    """Лексема: ``kind`` - тип (``IDENT``, ``NUMBER``, ``EOF`` або сам текст
    ключового слова/оператора), ``text`` - як записано у вихідному тексті."""

    kind: str
    text: str
    span: SourceSpan


@dataclass(frozen=True)
class Annotation:
    """Коментар-анотація ``//@ ключ: текст`` (специфікація для асистента)."""

    key: str
    text: str
    span: SourceSpan  # проміжок саме тексту після двокрапки
    comment_span: SourceSpan


def _is_ident_start(ch: str) -> bool:
    return ch == "_" or ("a" <= ch <= "z") or ("A" <= ch <= "Z")


def _is_ident_part(ch: str) -> bool:
    return _is_ident_start(ch) or ("0" <= ch <= "9")


class Lexer:
    def __init__(self, source: str):
        self.source = source
        self.pos = 0
        self.line = 1
        self.column = 1
        self.tokens: list[Token] = []
        self.annotations: list[Annotation] = []

    # -- позиції ---------------------------------------------------------
    def _mark(self) -> tuple[int, int, int]:
        return self.pos, self.line, self.column

    def _span_from(self, mark: tuple[int, int, int]) -> SourceSpan:
        return SourceSpan(mark[0], self.pos, mark[1], mark[2], self.line, self.column)

    def _advance(self, count: int = 1) -> None:
        for _ in range(count):
            if self.source[self.pos] == "\n":
                self.line += 1
                self.column = 1
            else:
                self.column += 1
            self.pos += 1

    # -- основний цикл ---------------------------------------------------
    def tokenize(self) -> list[Token]:
        src = self.source
        while self.pos < len(src):
            ch = src[self.pos]
            if ch in " \t\r\n﻿":
                self._advance()
            elif src.startswith("//", self.pos):
                self._comment()
            elif "0" <= ch <= "9":
                mark = self._mark()
                while self.pos < len(src) and "0" <= src[self.pos] <= "9":
                    self._advance()
                if self.pos < len(src) and _is_ident_start(src[self.pos]):
                    bad = self._mark()
                    while self.pos < len(src) and _is_ident_part(src[self.pos]):
                        self._advance()
                    raise SiplSyntaxError(
                        Diagnostic(
                            "E001",
                            f"некоректна лексема '{src[mark[0]:self.pos]}': "
                            "ім'я змінної не може починатися з цифри",
                            SourceSpan(mark[0], self.pos, mark[1], mark[2], self.line, self.column),
                            hint="імена змінних мають вигляд [A-Za-z_][A-Za-z0-9_]*",
                        )
                    ) from None
                self.tokens.append(Token("NUMBER", src[mark[0]:self.pos], self._span_from(mark)))
            elif _is_ident_start(ch):
                mark = self._mark()
                while self.pos < len(src) and _is_ident_part(src[self.pos]):
                    self._advance()
                text = src[mark[0]:self.pos]
                kind = text if text in KEYWORDS else "IDENT"
                self.tokens.append(Token(kind, text, self._span_from(mark)))
            elif ch in _UNICODE_ALIASES:
                mark = self._mark()
                self._advance()
                self.tokens.append(Token(_UNICODE_ALIASES[ch], ch, self._span_from(mark)))
            else:
                for op in _OPERATORS:
                    if src.startswith(op, self.pos):
                        mark = self._mark()
                        self._advance(len(op))
                        self.tokens.append(Token(op, op, self._span_from(mark)))
                        break
                else:
                    self._bad_character(ch)
        mark = self._mark()
        self.tokens.append(Token("EOF", "", self._span_from(mark)))
        return self.tokens

    def _comment(self) -> None:
        src = self.source
        mark = self._mark()
        end = src.find("\n", self.pos)
        if end == -1:
            end = len(src)
        body = src[self.pos:end]
        if body.startswith(ANNOTATION_PREFIX):
            payload = body[len(ANNOTATION_PREFIX):]
            key, sep, text = payload.partition(":")
            if sep:
                text_offset = self.pos + len(ANNOTATION_PREFIX) + len(key) + 1
                text_col = mark[2] + (text_offset - mark[0])
                stripped_end = text_offset + len(text.rstrip())
                self._advance(end - self.pos)
                self.annotations.append(
                    Annotation(
                        key.strip().lower(),
                        text.strip(),
                        SourceSpan(
                            text_offset,
                            stripped_end,
                            mark[1],
                            text_col,
                            mark[1],
                            text_col + (stripped_end - text_offset),
                        ),
                        self._span_from(mark),
                    )
                )
                return
        self._advance(end - self.pos)

    def _bad_character(self, ch: str) -> None:
        mark = self._mark()
        self._advance()
        hint = None
        if ch == ":":
            hint = "присвоювання записується як ':='"
        elif ch == "/":
            hint = "ділення не входить до SIPL; коментар починається з '//'"
        elif ch in "{}":
            hint = "блок операторів записується як begin ... end"
        elif ch in "&|!":
            hint = "логічні зв'язки SIPL: 'or' (∨) та 'not' (¬)"
        raise SiplSyntaxError(
            Diagnostic("E001", f"неочікуваний символ {ch!r}", self._span_from(mark), hint=hint)
        )


def tokenize(source: str) -> list[Token]:
    """Розбиває текст на лексеми (остання завжди ``EOF``)."""
    return Lexer(source).tokenize()
