# JSON-контракт CLI та архітектура

## Потік даних

```
текст ──lexer──▶ лексеми ──parser──▶ дерево виводу ──ast_builder──▶ AST
                                          │                          │
                                          ▼                          ├──▶ semantics.terms ──▶ терм ──▶ semantics.evaluate ──▶ стан + trace
                                   visualization ◀───────────────────┤
                                 (text/JSON/DOT/SVG/HTML)            └──▶ proof.vcgen ──▶ умови ──▶ proof.prover / пошук контрприкладів
```

| Модуль | Відповідальність | Стабільна модель даних |
|---|---|---|
| `sipl.source` | позиції та діагностика | `SourceSpan`, `Diagnostic` |
| `sipl.lexer` | лексеми, анотації `//@` | `Token`, `Annotation` |
| `sipl.parser`, `sipl.parse_tree` | дерево виводу в термінах БНФ | `ParseNode` |
| `sipl.ast_builder`, `sipl.ast` | типізоване AST | `Program`, `Assign`, `If`, ... |
| `sipl.semantics` | семантичні терми та їх обчислення | `Term`, `EvalResult`, `Step` |
| `sipl.visualization` | подання дерев | `VizNode` |
| `sipl.proof` | умови перевірки, доказ, контрприклади | `Spec`, `VC`, `Report` |
| `sipl.cli` | командний рядок | оболонка JSON нижче |

Мова "живе" в бібліотеці: CLI, візуалізатор та асистент доведення лише
викликають її API й не дублюють парсер. Так само її може використати
редактор чи web-інтерфейс.

## Оболонка

З прапорцем `--json` (до або після назви команди) кожна команда друкує рівно
один об'єкт:

```json
{
  "tool": "sipl",
  "version": "0.1.0",
  "command": "check",
  "ok": true,
  "diagnostics": [],
  "result": { }
}
```

* `ok` - чи результат позитивний; відповідає коду завершення (`0` / не `0`).
* `diagnostics[]` - `{severity, code, message, span, hint}`; `span` - `null`
  або `{start, end, line, column, end_line, end_column}` (зсуви з нуля, кінець
  не включено; рядки й колонки з одиниці).
* `result` - `null`, якщо програму не вдалося розібрати.

Коди завершення: `0` - успіх, `1` - негативний результат (синтаксична
помилка, невизначене значення, доведення не пройшло), `2` - помилка
використання.

## `result` за командами

| Команда | Поля `result` |
|---|---|
| `check` | `statements` (кількість), `variables` (список імен) |
| `parse` | `ast` - вузли `{type, ...поля, span}`; типи: `Program, Assign, Seq, If, While, Block, Skip, Num, Var, BinOp, Compare, BoolOp, Not` |
| `tree` | `parse_tree` та/або `ast` - вузли `{id, label, kind, detail, span, text, children}`; для dot/svg/html ще `rendered: {format, content}` |
| `term` | `term` - вузли `{kind, ..., origin}` з `kind` ∈ `const, deref, superposition, AS, composition, IF, WH, id`; `notation: {unicode, ascii, latex}`; `construction[]: {rule, span, source, term}` |
| `eval` | `term`, `initial_state`, `defined`, `state` або `null`, `undefined: {kind, message, span}` або `null`, `transitions`, `trace[]: {index, depth, rule, term, state, result, note, origin}` |
| `prove` | `verdict` ∈ `proved, tested, failed, refuted`; `summary`; `correctness` ∈ `partial, total`; `spec: {pre, post, loops[]}`; `conditions[]: {id, kind, loop, title, hypotheses[], goal, formula, span, status, proof[], counterexample, tested_states, note}`; `dynamic: {runs, bound, violations[], step_limit_example}` |

Терм із `sipl term --json` можна зберегти у файл і пізніше обчислити:
`sipl eval term.json --from-term --state "X=2,Y=3"`.
