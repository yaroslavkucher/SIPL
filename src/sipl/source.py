"""Позиції у вихідному тексті та діагностика.

Усі шари (лексер, парсер, AST, семантичні терми, асистент доведення)
посилаються на фрагменти тексту через :class:`SourceSpan`.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SourceSpan:
    """Напіввідкритий проміжок ``[start, end)`` у вихідному тексті.

    ``start``/``end`` - зсуви в символах від початку тексту (з нуля);
    рядки та колонки рахуються з одиниці, ``end_column`` - колонка
    одразу за останнім символом.
    """

    start: int
    end: int
    line: int
    column: int
    end_line: int
    end_column: int

    def merge(self, other: "SourceSpan") -> "SourceSpan":
        """Найменший проміжок, що охоплює обидва."""
        first = self if self.start <= other.start else other
        last = self if self.end >= other.end else other
        return SourceSpan(
            first.start, last.end, first.line, first.column, last.end_line, last.end_column
        )

    def to_dict(self) -> dict:
        return {
            "start": self.start,
            "end": self.end,
            "line": self.line,
            "column": self.column,
            "end_line": self.end_line,
            "end_column": self.end_column,
        }

    @staticmethod
    def from_dict(data: dict) -> "SourceSpan":
        return SourceSpan(
            data["start"],
            data["end"],
            data["line"],
            data["column"],
            data["end_line"],
            data["end_column"],
        )

    def __str__(self) -> str:
        return f"{self.line}:{self.column}"


@dataclass(frozen=True)
class Diagnostic:
    """Повідомлення для користувача, прив'язане до фрагмента тексту."""

    code: str
    message: str
    span: SourceSpan | None = None
    severity: str = "error"
    hint: str | None = None

    def to_dict(self) -> dict:
        return {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
            "span": self.span.to_dict() if self.span else None,
            "hint": self.hint,
        }


class SiplError(Exception):
    """Базова помилка інструментарію; завжди несе :class:`Diagnostic`."""

    def __init__(self, diagnostic: Diagnostic):
        super().__init__(diagnostic.message)
        self.diagnostic = diagnostic


class SiplSyntaxError(SiplError):
    """Лексична або синтаксична помилка."""


def render_diagnostic(diag: Diagnostic, source: str | None = None, filename: str = "<input>") -> str:
    """Текстове подання діагностики з рядком коду та підкресленням."""
    where = f"{filename}:{diag.span}" if diag.span else filename
    severity = {"error": "помилка", "warning": "попередження"}.get(diag.severity, diag.severity)
    lines = [f"{where}: {severity} [{diag.code}]: {diag.message}"]
    if diag.span and source is not None:
        src_lines = source.split("\n")
        if 1 <= diag.span.line <= len(src_lines):
            text = src_lines[diag.span.line - 1].replace("\t", " ")
            if diag.span.end_line == diag.span.line:
                width = max(1, diag.span.end_column - diag.span.column)
            else:
                width = max(1, len(text) - diag.span.column + 1)
            gutter = f"{diag.span.line:>4} | "
            lines.append(gutter + text)
            lines.append(" " * (len(gutter) + diag.span.column - 1) + "^" * width)
    if diag.hint:
        lines.append(f"  підказка: {diag.hint}")
    return "\n".join(lines)
