"""SIPL Language Toolkit - ядро: парсер, AST, семантичні терми, візуалізація,
асистент доведення коректності циклів."""

from .parser import check, parse, parse_assertion, parse_expression, parse_tree
from .source import Diagnostic, SiplError, SiplSyntaxError, SourceSpan

__version__ = "0.1.0"

__all__ = [
    "check", "parse", "parse_assertion", "parse_expression", "parse_tree",
    "Diagnostic", "SiplError", "SiplSyntaxError", "SourceSpan", "__version__",
]
