# SIPL Language Toolkit - трек «Формальна семантика»

Інструменти для навчальної мови SIPL (SImple Programming Language) з курсу
«Теорія програмування»: парсер із типізованим AST, дерево синтаксичного
виводу, семантичні терми в нотації курсу, їх обчислення на стані та асистент
доведення коректності циклічних програм.

Ядро - бібліотека Python без зовнішніх залежностей; усе доступне і з коду,
і з командного рядка `sipl`, і у вигляді JSON.

## Обрані завдання

| ID | Завдання | Бали | Де реалізовано |
|---|---|---|---|
| CORE-01 | Формальна специфікація SIPL | 0.5 | [`grammar/sipl.lark`](grammar/sipl.lark), [`docs/grammar.md`](docs/grammar.md), [`examples/`](examples), `tests/test_grammar.py` |
| CORE-02 | Парсер і типізоване AST із `SourceSpan` | 1.5 | `src/sipl/{source,lexer,parser,parse_tree,ast_builder,ast}.py` |
| VIS-01 | Візуалізація дерева синтаксичного виводу | 1.5 | `src/sipl/visualization/` - text, JSON, DOT, SVG, HTML |
| SEM-01 | Побудова семантичного терму | 2.0 | `src/sipl/semantics/terms.py`, еталони `tests/golden/` |
| SEM-02 | Обчислення терму на стані з поясненням | 2.0 | `src/sipl/semantics/evaluate.py` |
| SEM-04 | Асистент доведення коректності циклів | 2.5 | `src/sipl/proof/` |
| | **Заявлено** | **10.0** | |
| VIS-02 | AST та зіставлення Parse Tree ↔ AST (запас) | 1.0 | `sipl tree --view both --format html` |

REL-завдання (CI, релізи, PyPI) не заявляються.

## Встановлення

Потрібен Python 3.10 або новіший.

```bash
python -m venv .venv
```

Активуйте середовище: `.venv\Scripts\activate` (Windows) або
`source .venv/bin/activate` (Linux/macOS). Далі:

```bash
python -m pip install -e ".[dev]"
```

```bash
python -m pytest
```

Для самого інструмента без тестів досить `python -m pip install .`
(залежностей немає). Набір `dev` додає `pytest`, `pytest-cov` та `lark` -
останній потрібен лише тестам, що звіряють граматику з парсером.

Якщо консоль Windows показує кирилицю або символи `⇒`, `•` некоректно,
виконайте `chcp 65001` або використайте `--style ascii`.

## Використання

```bash
sipl check examples/power.sipl
```

```bash
sipl tree examples/max.sipl
```

```bash
sipl tree examples/power.sipl --view both --format html -o tree.html
```

```bash
sipl term examples/power.sipl --explain
```

```bash
sipl eval examples/power.sipl --state "X=2,Y=5" --trace
```

```bash
sipl prove examples/power.sipl
```

| Команда | Призначення | Основні параметри |
|---|---|---|
| `check` | синтаксична перевірка | |
| `parse` | AST | `--format text\|json\|source` |
| `tree` | дерево виводу / AST | `--view parse\|ast\|both`, `--format text\|json\|dot\|svg\|html`, `-o файл` |
| `term` | семантичний терм | `--style unicode\|ascii\|latex`, `--pretty`, `--explain` |
| `eval` | обчислення терму на стані | `--state "X=2,Y=3"`, `--state-file`, `--trace`, `--detail`, `--max-steps`, `--from-term` |
| `prove` | перевірка коректності за інваріантом | `--pre`, `--post`, `--inv`, `--variant`, `--max-steps` |

Спільне: `--json` - структурований вивід ([`docs/json-contract.md`](docs/json-contract.md));
`-` замість файла - читати зі стандартного вводу. Коди завершення: `0` -
успіх, `1` - негативний результат, `2` - помилка використання.

### Приклад: від тексту до доведення

Програма [`examples/power.sipl`](examples/power.sipl):

```
//@ pre: Y >= 0 and X = A and Y = B
//@ post: R = A^B
begin
  R := 1;
  //@ inv: R * X^Y = A^B and Y >= 0
  //@ variant: Y
  while Y > 0 do
  begin
    R := R * X;
    Y := Y - 1
  end
end
```

Семантичний терм (`sipl term`):

```
AS^R(1̄) • WH(S^2(gr, Y⇒, 0̄), AS^R(S^2(mult, R⇒, X⇒)) • AS^Y(S^2(sub, Y⇒, 1̄)))
```

Обчислення (`sipl eval --state "X=2,Y=2" --trace`, скорочено):

```
   1. [AS] 1̄(st) = 1; st ∇ [R ↦ 1]
   2. [WH-true] умова S^2(gr, Y⇒, 0̄)(st) = true → WH(b, f)(st) = WH(b, f)(f(st)), ітерація 1
   3.   [AS] S^2(mult, R⇒, X⇒)(st) = 2; st ∇ [R ↦ 2]
   ...
   8. [WH-false] умова S^2(gr, Y⇒, 0̄)(st) = false → WH(b, f)(st) = st, цикл завершено після 2 ітерацій
Результат: [R ↦ 4, X ↦ 2, Y ↦ 0]  (переходів: 8)
```

Доведення (`sipl prove`): п'ять умов перевірки - база, крок, вихід і дві
умови завершуваності - доводяться символьно, висновок - повна коректність.
Із заслабким інваріантом (`sipl prove examples/multiply.sipl --inv "Y >= 0"`)
асистент показує контрприклад до умови виходу й пояснює, що інваріант треба
підсилити.

### Помилки

```
examples/invalid/02_trailing_semicolon.sipl:3:9: помилка [E004]: зайва ';' перед 'end'
   3 |   X := 1;
               ^
  підказка: у SIPL ';' розділяє оператори, а не завершує їх
```

## Використання як бібліотеки

```python
from sipl import parse
from sipl.semantics import build_term, evaluate, render

program = parse("begin R := 1; while Y > 0 do begin R := R * X; Y := Y - 1 end end")
term = build_term(program)
print(render(term))                      # AS^R(1̄) • WH(...)
result = evaluate(term, {"X": 2, "Y": 10})
print(result.state)                      # {'X': 2, 'Y': 0, 'R': 1024}
```

## Структура

```
grammar/sipl.lark        формальна граматика (Lark)
src/sipl/
  source.py              SourceSpan, Diagnostic
  lexer.py  parser.py    лексеми -> дерево синтаксичного виводу
  parse_tree.py          вузли дерева виводу в термінах БНФ курсу
  ast_builder.py ast.py  дерево виводу -> типізоване AST
  semantics/             семантичні терми та їх обчислення
  visualization/         text / JSON / DOT / SVG / HTML
  proof/                 умови перевірки, доводжувач, контрприклади
  cli.py                 командний рядок
examples/                15 коректних програм, invalid/ - 16 некоректних
tests/                   pytest; golden/ - еталонні терми
docs/                    специфікація мови, семантика, асистент, JSON-контракт
```

Документація: [граматика та лексика](docs/grammar.md) ·
[семантичні терми](docs/semantics.md) ·
[асистент доведення](docs/proof-assistant.md) ·
[JSON-контракт і архітектура](docs/json-contract.md).

## Тести

`python -m pytest` - близько 240 тестів: позитивні й негативні програми,
структура AST і позиції, еталонні (golden) терми для всіх прикладів,
обчислення та невизначеність, формати візуалізації, асистент доведення
(включно з перевіркою коректності доводжувача на випадкових формулах) та
інтеграційні тести CLI. Покриття: `python -m pytest --cov=sipl`.

## Обмеження

* Розбір зупиняється на першій синтаксичній помилці (без відновлення).
* Обчислювач не доводить зациклення - лише обмежує кількість кроків.
* Асистент перевіряє заданий інваріант, а не вигадує його; символьний
  доводжувач коректний, але неповний, а відсутність контрприкладу на малих
  значеннях не є доказом. Докладно - у
  [`docs/proof-assistant.md`](docs/proof-assistant.md#межі-автоматизації).
* Мова - базова SIPL версії 1.0 без рекурсивних функцій; розширені операції
  (`and`, `<`, `^` ...) доступні лише в твердженнях специфікації.
