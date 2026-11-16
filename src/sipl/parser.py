"""Синтаксичний аналізатор SIPL (рекурсивний спуск).

Парсер будує дерево синтаксичного виводу (:mod:`sipl.parse_tree`) у термінах
БНФ курсу; AST отримується з нього окремим проходом
(:mod:`sipl.ast_builder`). Неоднозначність БНФ знято пріоритетами, які
зафіксовано у ``docs/grammar.md``:

* ``*`` сильніше за ``+`` та ``-``; усі арифметичні операції лівоасоціативні;
* ``not`` сильніше за ``or``;
* ``;`` має найнижчий пріоритет: тіло ``while`` та гілки ``if`` - це один
  оператор, для кількох потрібен ``begin ... end``.

Той самий парсер у режимі ``extended=True`` розбирає мову тверджень
(додатково ``and``, ``->``, ``<``, ``>=``, ``<=``, ``!=``, ``^``, ``true``,
``false``).
"""

from __future__ import annotations

from . import ast
from .lexer import Annotation, Lexer, Token
from .parse_tree import CONDITION, EXPRESSION, NUMBER, PROGRAM, STATEMENT, VARIABLE, ParseNode
from .source import Diagnostic, SiplSyntaxError, SourceSpan

_STATEMENT_START = ("IDENT", "if", "while", "begin", "skip")
_BASE_RELOPS = ("=", ">")
_EXT_RELOPS = ("=", ">", "<", ">=", "<=", "!=")
_EXTENDED_ONLY = {
    "<": "запишіть порівняння навпаки: 'B > A'",
    ">=": "запишіть як 'A > B or A = B'",
    "<=": "запишіть як 'B > A or A = B'",
    "!=": "запишіть як 'not (A = B)'",
    "and": "кон'юнкцію можна виразити як 'not (not P or not Q)'",
    "->": "імплікацію можна виразити як 'not P or Q'",
    "^": "піднесення до степеня реалізуйте циклом множень",
    "true": "використайте тотожно істинну умову, напр. '0 = 0'",
    "false": "використайте тотожно хибну умову, напр. '1 = 0'",
}


def _describe(tok: Token) -> str:
    if tok.kind == "EOF":
        return "кінець тексту"
    return f"'{tok.text}'"


class Parser:
    def __init__(self, tokens: list[Token], extended: bool = False):
        self.tokens = tokens
        self.pos = 0
        self.extended = extended

    # -- базові операції -------------------------------------------------
    @property
    def tok(self) -> Token:
        return self.tokens[self.pos]

    def at(self, *kinds: str) -> bool:
        return self.tok.kind in kinds

    def fail(self, message: str, *, code: str = "E002", hint: str | None = None,
             span: SourceSpan | None = None) -> None:
        tok = self.tok
        if not self.extended and tok.kind in _EXTENDED_ONLY and span is None:
            raise SiplSyntaxError(
                Diagnostic(
                    "E003",
                    f"'{tok.text}' не входить до базової мови SIPL",
                    tok.span,
                    hint=_EXTENDED_ONLY[tok.kind],
                )
            )
        raise SiplSyntaxError(Diagnostic(code, message, span or tok.span, hint=hint))

    def expected(self, what: str, hint: str | None = None) -> None:
        self.fail(f"очікується {what}, а знайдено {_describe(self.tok)}", hint=hint)

    def terminal(self, kind: str, what: str | None = None, hint: str | None = None) -> ParseNode:
        tok = self.tok
        if tok.kind != kind:
            self.expected(what or f"'{kind}'", hint)
        self.pos += 1
        return ParseNode(tok.text, tok.span, terminal=True, token_kind=tok.kind)

    @staticmethod
    def node(symbol: str, children: list[ParseNode]) -> ParseNode:
        return ParseNode(symbol, children[0].span.merge(children[-1].span), children)

    def _after_previous(self) -> SourceSpan:
        """Позиція одразу за попередньою лексемою (для "пропущено ...")."""
        prev = self.tokens[self.pos - 1].span
        return SourceSpan(prev.end, prev.end, prev.end_line, prev.end_column,
                          prev.end_line, prev.end_column)

    # -- програма та оператори ------------------------------------------
    def program(self) -> ParseNode:
        if not self.at("begin"):
            self.expected("'begin' на початку програми",
                          hint="програма SIPL має вигляд: begin <оператор> end")
        begin = self.terminal("begin")
        body = self.statement_sequence(begin)
        end = self.terminal("end")
        if not self.at("EOF"):
            hint = "';' розділяє оператори всередині begin ... end" if self.at(";") else None
            self.fail(f"зайвий текст після завершального 'end': {_describe(self.tok)}", hint=hint)
        return self.node(PROGRAM, [begin, body, end])

    def statement_sequence(self, opener: ParseNode) -> ParseNode:
        """``<оператор> ::= <оператор> ; <оператор>`` (лівоасоціативно)."""
        if self.at("end"):
            self.fail("порожній блок: між 'begin' та 'end' потрібен хоча б один оператор",
                      hint="для порожньої дії використайте 'skip'")
        left = self.statement()
        while True:
            if self.at(";"):
                semi = self.terminal(";")
                if self.at("end"):
                    self.fail("зайва ';' перед 'end'", code="E004", span=semi.span,
                              hint="у SIPL ';' розділяє оператори, а не завершує їх")
                right = self.statement()
                left = self.node(STATEMENT, [left, semi, right])
            elif self.at(*_STATEMENT_START):
                self.fail("пропущено ';' між операторами", span=self._after_previous())
            elif self.at("EOF"):
                self.fail(
                    f"очікується 'end': блок, відкритий у рядку {opener.span.line}, не закрито"
                )
            elif self.at("end"):
                return left
            elif self.at("else"):
                self.fail("'else' без відповідного 'if ... then'",
                          hint="гілки if - це один оператор; кілька операторів беріть у begin ... end")
            else:
                self.expected("';' або 'end'")

    def statement(self) -> ParseNode:
        if self.at("IDENT"):
            var = self.variable()
            if self.at("="):
                self.fail("очікується ':=', а знайдено '='", hint="присвоювання записується як ':='")
            assign = self.terminal(":=", "':=' після імені змінної")
            return self.node(STATEMENT, [var, assign, self.expression()])
        if self.at("skip"):
            return self.node(STATEMENT, [self.terminal("skip")])
        if self.at("begin"):
            begin = self.terminal("begin")
            body = self.statement_sequence(begin)
            return self.node(STATEMENT, [begin, body, self.terminal("end")])
        if self.at("if"):
            kw_if = self.terminal("if")
            cond = self.condition()
            kw_then = self.terminal("then", "'then' після умови")
            then = self.statement()
            if not self.at("else"):
                hint = "у SIPL оператор if завжди має гілку else; додайте 'else skip'"
                if self.at(";"):
                    hint = ("гілка then - це один оператор; кілька операторів беріть у "
                            "begin ... end, а гілка else обов'язкова")
                self.expected("'else'", hint)
            kw_else = self.terminal("else")
            return self.node(STATEMENT, [kw_if, cond, kw_then, then, kw_else, self.statement()])
        if self.at("while"):
            kw_while = self.terminal("while")
            cond = self.condition()
            kw_do = self.terminal("do", "'do' після умови циклу")
            return self.node(STATEMENT, [kw_while, cond, kw_do, self.statement()])
        self.expected("оператор (присвоювання, if, while, begin або skip)")
        raise AssertionError("unreachable")

    def variable(self) -> ParseNode:
        return self.node(VARIABLE, [self.terminal("IDENT", "ім'я змінної")])

    # -- арифметичні вирази ---------------------------------------------
    def expression(self) -> ParseNode:
        left = self.term()
        while self.at("+", "-"):
            op = self.terminal(self.tok.kind)
            left = self.node(EXPRESSION, [left, op, self.term()])
        return left

    def term(self) -> ParseNode:
        left = self.factor()
        while self.at("*"):
            op = self.terminal("*")
            left = self.node(EXPRESSION, [left, op, self.factor()])
        return left

    def factor(self) -> ParseNode:
        base = self.atom()
        if self.extended and self.at("^"):
            op = self.terminal("^")
            return self.node(EXPRESSION, [base, op, self.factor()])
        return base

    def atom(self) -> ParseNode:
        if self.at("NUMBER"):
            return self.node(EXPRESSION, [self.node(NUMBER, [self.terminal("NUMBER")])])
        if self.at("-") and self.tokens[self.pos + 1].kind == "NUMBER":
            minus, digits = self.tok, self.tokens[self.pos + 1]
            self.pos += 2
            leaf = ParseNode("-" + digits.text, minus.span.merge(digits.span),
                             terminal=True, token_kind="NUMBER")
            return self.node(EXPRESSION, [self.node(NUMBER, [leaf])])
        if self.at("IDENT"):
            return self.node(EXPRESSION, [self.variable()])
        if self.at("("):
            lpar = self.terminal("(")
            inner = self.expression()
            return self.node(EXPRESSION, [lpar, inner, self.terminal(")", "')'")])
        self.expected("арифметичний вираз (число, змінна або '(')")
        raise AssertionError("unreachable")

    # -- умови -----------------------------------------------------------
    def condition(self) -> ParseNode:
        left = self.disjunction()
        if self.extended and self.at("->"):
            op = self.terminal("->")
            return self.node(CONDITION, [left, op, self.condition()])
        return left

    def disjunction(self) -> ParseNode:
        left = self.conjunction()
        while self.at("or"):
            op = self.terminal("or")
            left = self.node(CONDITION, [left, op, self.conjunction()])
        return left

    def conjunction(self) -> ParseNode:
        left = self.negation()
        while self.extended and self.at("and"):
            op = self.terminal("and")
            left = self.node(CONDITION, [left, op, self.negation()])
        return left

    def negation(self) -> ParseNode:
        if self.at("not"):
            op = self.terminal("not")
            return self.node(CONDITION, [op, self.negation()])
        return self.condition_atom()

    def condition_atom(self) -> ParseNode:
        if self.extended and self.at("true", "false"):
            return self.node(CONDITION, [self.terminal(self.tok.kind)])
        if not self.at("("):
            return self.comparison()
        # '(' може відкривати і вираз "(X + 1) > 2", і умову "(X > 1) or ...".
        # Пробуємо обидва варіанти; якщо жоден не підходить, повідомляємо
        # про ту помилку, яка сталася далі в тексті.
        start = self.pos
        try:
            return self.comparison()
        except SiplSyntaxError as as_comparison:
            self.pos = start
            try:
                lpar = self.terminal("(")
                inner = self.condition()
                return self.node(CONDITION, [lpar, inner, self.terminal(")", "')'")])
            except SiplSyntaxError as as_group:
                a = as_comparison.diagnostic.span.start if as_comparison.diagnostic.span else -1
                b = as_group.diagnostic.span.start if as_group.diagnostic.span else -1
                raise (as_comparison if a >= b else as_group) from None

    def comparison(self) -> ParseNode:
        left = self.expression()
        relops = _EXT_RELOPS if self.extended else _BASE_RELOPS
        if not self.at(*relops):
            names = ", ".join(f"'{r}'" for r in relops)
            self.expected(f"оператор порівняння ({names})")
        op = self.terminal(self.tok.kind)
        return self.node(CONDITION, [left, op, self.expression()])

    def finish(self, what: str) -> None:
        if not self.at("EOF"):
            self.fail(f"зайвий текст після {what}: {_describe(self.tok)}")


# -- публічний API ---------------------------------------------------------
def parse_tree(source: str) -> ParseNode:
    """Дерево синтаксичного виводу програми. Кидає :class:`SiplSyntaxError`."""
    return Parser(Lexer(source).tokenize()).program()


def parse(source: str) -> ast.Program:
    """AST програми. Кидає :class:`SiplSyntaxError`."""
    from .ast_builder import build_program

    return build_program(parse_tree(source))


def parse_with_annotations(source: str) -> tuple[ast.Program, list[Annotation]]:
    """AST разом з анотаціями ``//@ ключ: текст``."""
    from .ast_builder import build_program

    lexer = Lexer(source)
    tree = Parser(lexer.tokenize()).program()
    return build_program(tree), lexer.annotations


def parse_assertion(source: str) -> ast.Cond:
    """Розбирає твердження (розширена мова умов)."""
    from .ast_builder import build_condition

    parser = Parser(Lexer(source).tokenize(), extended=True)
    tree = parser.condition()
    parser.finish("твердження")
    return build_condition(tree)


def parse_expression(source: str, extended: bool = False) -> ast.Expr:
    """Розбирає окремий арифметичний вираз."""
    from .ast_builder import build_expression

    parser = Parser(Lexer(source).tokenize(), extended=extended)
    tree = parser.expression()
    parser.finish("виразу")
    return build_expression(tree)


def check(source: str) -> list[Diagnostic]:
    """Перевіряє синтаксичну коректність; порожній список - помилок немає."""
    try:
        parse(source)
    except SiplSyntaxError as err:
        return [err.diagnostic]
    return []
