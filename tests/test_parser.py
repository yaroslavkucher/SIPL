"""Лексер, парсер, AST і діагностика (CORE-02)."""

import pytest

from sipl import SiplSyntaxError, ast, check, parse, parse_assertion, parse_expression, parse_tree
from sipl.lexer import Lexer, tokenize
from sipl.source import render_diagnostic


def body(source: str) -> ast.Stmt:
    return parse(f"begin {source} end").body


def error(source: str):
    with pytest.raises(SiplSyntaxError) as info:
        parse(source)
    return info.value.diagnostic


# -- лексер ---------------------------------------------------------------------
def test_tokens_and_positions():
    tokens = tokenize("begin\n  X1 := 42\nend")
    assert [t.kind for t in tokens] == ["begin", "IDENT", ":=", "NUMBER", "end", "EOF"]
    x1 = tokens[1]
    assert (x1.span.line, x1.span.column, x1.span.end_column) == (2, 3, 5)
    assert (x1.span.start, x1.span.end) == (8, 10)


def test_unicode_aliases_and_comments():
    kinds = [t.kind for t in tokenize("¬ X ∨ Y × 2 // коментар ∨\n")]
    assert kinds == ["not", "IDENT", "or", "IDENT", "*", "NUMBER", "EOF"]


def test_annotations_are_collected_with_spans():
    lexer = Lexer("begin\n  //@ inv: X > 0\n  skip\nend")
    lexer.tokenize()
    (ann,) = lexer.annotations
    assert (ann.key, ann.text) == ("inv", "X > 0")
    assert (ann.span.line, ann.span.column) == (2, 11)


@pytest.mark.parametrize("text", ["begin X := 1 $ 2 end", "begin X := 2y end", "begin X : 1 end"])
def test_lexical_errors(text):
    assert error(text).code == "E001"


# -- структура AST ------------------------------------------------------------------
def test_assignment_and_precedence():
    stmt = body("X := 1 + 2 * 3 - 4")
    assert stmt == ast.Assign(
        ast.Var("X"),
        ast.BinOp("-", ast.BinOp("+", ast.Num(1), ast.BinOp("*", ast.Num(2), ast.Num(3))),
                  ast.Num(4)),
    )


def test_subtraction_is_left_associative_and_parens_override():
    assert parse_expression("10 - 3 - 2") == ast.BinOp(
        "-", ast.BinOp("-", ast.Num(10), ast.Num(3)), ast.Num(2))
    assert parse_expression("10 - (3 - 2)") == ast.BinOp(
        "-", ast.Num(10), ast.BinOp("-", ast.Num(3), ast.Num(2)))


def test_negative_literals():
    assert parse_expression("-5") == ast.Num(-5)
    assert parse_expression("X - -1") == ast.BinOp("-", ast.Var("X"), ast.Num(-1))
    assert parse_expression("X -1") == ast.BinOp("-", ast.Var("X"), ast.Num(1))


def test_sequence_is_flattened_and_block_is_kept():
    stmt = body("X := 1; begin Y := 2; Z := 3 end; skip")
    assert isinstance(stmt, ast.Seq) and len(stmt.statements) == 3
    assert isinstance(stmt.statements[1], ast.Block)
    assert isinstance(stmt.statements[1].body, ast.Seq)


def test_while_body_is_single_statement():
    stmt = body("while X > 0 do X := X - 1; Y := 1")
    assert isinstance(stmt, ast.Seq)
    assert isinstance(stmt.statements[0], ast.While)
    assert stmt.statements[1] == ast.Assign(ast.Var("Y"), ast.Num(1))


def test_nested_if_and_condition_precedence():
    stmt = body("if not X > 1 or Y = 2 then skip else if X = Y then skip else X := 0")
    assert isinstance(stmt, ast.If) and isinstance(stmt.orelse, ast.If)
    assert stmt.cond == ast.BoolOp(
        "or", ast.Not(ast.Compare(">", ast.Var("X"), ast.Num(1))),
        ast.Compare("=", ast.Var("Y"), ast.Num(2)))


@pytest.mark.parametrize("cond", [
    "(X + 1) > 2", "(X > 1) or Y = 2", "((X > 1))", "not (X = 1 or (Y > 2))", "(X) = (Y)",
    "((X + 1) * 2) > (Y)",
])
def test_parenthesised_conditions_and_expressions(cond):
    stmt = body(f"if {cond} then skip else skip")
    assert isinstance(stmt, ast.If)


def test_ast_is_own_typed_tree_with_spans():
    program = parse("begin\n  X := Y + 1\nend")
    assign = program.body
    assert type(assign).__module__ == "sipl.ast"
    assert (assign.span.line, assign.span.column, assign.span.end_column) == (2, 3, 13)
    assert (assign.value.span.column, assign.value.span.end_column) == (8, 13)
    assert (assign.value.right.span.start, assign.value.right.span.end) == (17, 18)
    data = program.to_dict()
    assert data["type"] == "Program" and data["body"]["type"] == "Assign"
    assert data["body"]["span"]["line"] == 2


def test_span_equality_is_structural():
    assert parse("begin X:=1 end") == parse("begin\n\n   X   :=   1\nend")


# -- дерево виводу -------------------------------------------------------------------
def test_parse_tree_follows_course_bnf(power_source):
    tree = parse_tree(power_source)
    assert tree.symbol == "програма"
    assert tree.rule == "<програма> ::= begin <оператор> end"
    rules = {n.rule for n in tree.walk() if not n.terminal}
    assert "<оператор> ::= <оператор> ; <оператор>" in rules
    assert "<оператор> ::= while <умова> do <оператор>" in rules
    assert "<оператор> ::= <змінна> := <вираз>" in rules
    assert "<вираз> ::= <вираз> * <вираз>" in rules
    assert "<умова> ::= <вираз> > <вираз>" in rules


def test_parse_tree_leaves_reproduce_the_program(power_source):
    leaves = parse_tree(power_source).leaves()
    assert "".join(leaf.symbol for leaf in leaves) == "".join(power_source.split())
    for leaf in leaves:
        assert power_source[leaf.span.start:leaf.span.end] == leaf.symbol


# -- відтворення тексту ---------------------------------------------------------------
@pytest.mark.parametrize("source", [
    "begin X := 1 - (2 - 3) * -4 end",
    "begin if not (X > 1 or Y = 2) then skip else X := (1 + 2) * 3 end",
    "begin while X > 0 do begin X := X - 1; Y := Y * 2 end; skip end",
])
def test_unparse_roundtrip(source):
    program = parse(source)
    assert parse(ast.program_to_source(program)) == program


# -- діагностика ------------------------------------------------------------------------
@pytest.mark.parametrize("source, code, line, column, fragment", [
    ("X := 1", "E002", 1, 1, "begin"),
    ("begin X := 1; end", "E004", 1, 13, "зайва ';'"),
    ("begin X := 1 Y := 2 end", "E002", 1, 13, "пропущено ';'"),
    ("begin X = 1 end", "E002", 1, 9, "':='"),
    ("begin if X > 1 then skip end", "E002", 1, 26, "'else'"),
    ("begin while X < 3 do skip end", "E003", 1, 15, "'<'"),
    ("begin X := (1 + 2 end", "E002", 1, 19, "')'"),
    ("begin if (X > 1 or Y = 2 then skip else skip end", "E002", 1, 26, "')'"),
    ("begin\n  X := 1;\n  while X do skip\nend", "E002", 3, 11, "порівняння"),
    ("begin X := 1 end end", "E002", 1, 18, "зайвий текст"),
    ("begin end", "E002", 1, 7, "порожній блок"),
    ("begin begin X := 1 end", "E002", 1, 23, "не закрито"),
])
def test_syntax_diagnostics(source, code, line, column, fragment):
    diag = error(source)
    assert (diag.code, diag.span.line, diag.span.column) == (code, line, column)
    assert fragment in diag.message


def test_check_returns_diagnostics_instead_of_raising():
    assert check("begin skip end") == []
    (diag,) = check("begin skip; end")
    assert diag.code == "E004" and diag.hint


def test_rendered_diagnostic_points_at_source():
    source = "begin\n  X := 1;\nend"
    text = render_diagnostic(check(source)[0], source, "demo.sipl")
    lines = text.splitlines()
    assert lines[0].startswith("demo.sipl:2:9: помилка [E004]")
    assert lines[1].endswith("X := 1;")
    assert lines[2].index("^") == lines[1].index(";")


# -- мова тверджень ----------------------------------------------------------------------
def test_assertion_language_is_a_superset():
    cond = parse_assertion("R * X^Y = A^B and (Y >= 0 -> X != 1)")
    assert isinstance(cond, ast.BoolOp) and cond.op == "and"
    assert parse_assertion(ast.cond_to_source(cond)) == cond
    assert error("begin if X >= 1 then skip else skip end").code == "E003"
    assert error("begin X := X ^ 2 end").code == "E003"
