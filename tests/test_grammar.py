"""Формальна специфікація (CORE-01): корпус коректних і некоректних програм
та звірка машинозчитуваної граматики ``grammar/sipl.lark`` з парсером."""

import pytest

from conftest import INVALID, ROOT, VALID, read
from sipl import SiplSyntaxError, check, parse


def test_corpus_is_large_enough():
    assert len(VALID) >= 10
    assert len(INVALID) >= 10


@pytest.mark.parametrize("path", VALID, ids=lambda p: p.stem)
def test_valid_program_is_accepted(path):
    assert check(read(path)) == []


@pytest.mark.parametrize("path", INVALID, ids=lambda p: p.stem)
def test_invalid_program_is_rejected_at_expected_place(path):
    source = read(path)
    # перший рядок файла: // expect: <код> <рядок>:<колонка> <пояснення>
    _, _, code, position, *_ = source.splitlines()[0].split()
    with pytest.raises(SiplSyntaxError) as info:
        parse(source)
    diag = info.value.diagnostic
    assert (diag.code, str(diag.span)) == (code, position)


@pytest.fixture(scope="module")
def lark_grammar():
    lark = pytest.importorskip("lark")
    return lark.Lark(read(ROOT / "grammar" / "sipl.lark"), parser="lalr")


@pytest.mark.parametrize("path", VALID, ids=lambda p: p.stem)
def test_lark_grammar_accepts_valid(lark_grammar, path):
    lark_grammar.parse(read(path))


@pytest.mark.parametrize("path", INVALID, ids=lambda p: p.stem)
def test_lark_grammar_rejects_invalid(lark_grammar, path):
    from lark.exceptions import LarkError

    with pytest.raises(LarkError):
        lark_grammar.parse(read(path))


EXTRA = [
    "begin skip end",
    "begin X := -1 end",
    "begin X := ((1)) end",
    "begin begin begin skip end end end",
    "begin if X = 1 then begin X := 2; Y := 3 end else skip end",
    "begin while not not X > 1 do skip end",
    "begin beginning := 1; iffy := endless + 1 end",
    "begin X := 1;; Y := 2 end",
    "begin X := 1 + * 2 end",
    "begin if X then skip else skip end",
    "begin while X > 1 do end",
    "begin X := Y Z end",
    "begin skip skip end",
    "begin not X := 1 end",
    "begin X := 1 > 2 end",
    "begin if X > 1 or then skip else skip end",
    "begin X := () end",
    "begin and := 1 end",
]


@pytest.mark.parametrize("source", EXTRA)
def test_lark_grammar_agrees_with_parser(lark_grammar, source):
    from lark.exceptions import LarkError

    try:
        lark_grammar.parse(source)
        accepted_by_grammar = True
    except LarkError:
        accepted_by_grammar = False
    assert accepted_by_grammar == (check(source) == [])
