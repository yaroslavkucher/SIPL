"""Командний рядок ``sipl``.

Коди завершення: ``0`` - успіх; ``1`` - негативний результат (синтаксична
помилка, невизначене значення, доведення не пройшло); ``2`` - помилка
використання (немає файла, некоректні параметри).

З прапорцем ``--json`` кожна команда друкує один JSON-об'єкт зі сталою
оболонкою ``{tool, version, command, ok, diagnostics, result}``
(див. ``docs/json-contract.md``).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__, ast
from .ast_builder import build_program
from .lexer import Lexer
from .parser import Parser
from .source import Diagnostic, SiplError, render_diagnostic

EXIT_OK, EXIT_FAIL, EXIT_USAGE = 0, 1, 2


class UsageError(Exception):
    pass


# -- допоміжне ------------------------------------------------------------------------
def _read(path: str) -> tuple[str, str]:
    if path == "-":
        return sys.stdin.read(), "<stdin>"
    try:
        return Path(path).read_text(encoding="utf-8-sig"), path
    except OSError as err:
        raise UsageError(f"не вдалося прочитати файл '{path}': {err.strerror or err}") from None
    except UnicodeDecodeError:
        raise UsageError(f"файл '{path}' не є текстом у кодуванні UTF-8") from None


def parse_state(text: str | None, file: str | None) -> dict[str, int]:
    """Стан із рядка ``X=2,Y=3`` та/або JSON-файла ``{"X": 2}``."""
    state: dict[str, int] = {}
    if file:
        try:
            data = json.loads(Path(file).read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as err:
            raise UsageError(f"не вдалося прочитати стан із '{file}': {err}") from None
        if not isinstance(data, dict) or not all(
                isinstance(v, int) and not isinstance(v, bool) for v in data.values()):
            raise UsageError("файл стану має містити JSON-об'єкт виду {\"X\": 2, \"Y\": 3}")
        state.update(data)
    for item in (text or "").replace(";", ",").split(","):
        item = item.strip()
        if not item:
            continue
        name, sep, value = item.partition("=")
        name, value = name.strip(), value.strip()
        try:
            if not sep or not name.isidentifier():
                raise ValueError
            state[name] = int(value)
        except ValueError:
            raise UsageError(
                f"некоректний елемент стану '{item}'; очікується вигляд X=2,Y=-3") from None
    return state


class _Output:
    """Збирає результат команди і друкує його текстом або JSON-оболонкою."""

    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.diagnostics: list[Diagnostic] = []
        self.result: dict | None = None
        self.text: str = ""
        self.ok = True

    def emit(self, source: str | None, filename: str) -> int:
        target = getattr(self.args, "output", None)
        if self.args.json:
            payload = json.dumps({
                "tool": "sipl", "version": __version__, "command": self.args.command,
                "ok": self.ok, "diagnostics": [d.to_dict() for d in self.diagnostics],
                "result": self.result,
            }, ensure_ascii=False, indent=2)
        else:
            payload = self.text
            for diag in self.diagnostics:
                print(render_diagnostic(diag, source, filename), file=sys.stderr)
        if payload:
            if target and self.ok:
                Path(target).write_text(payload + "\n", encoding="utf-8")
                if not self.args.json:
                    print(f"записано: {target}")
            else:
                print(payload)
        return EXIT_OK if self.ok else EXIT_FAIL


# -- команди ----------------------------------------------------------------------------
def _cmd_check(args, out: _Output, source: str) -> None:
    program = build_program(Parser(Lexer(source).tokenize()).program())
    statements = sum(1 for n in program.walk()
                     if isinstance(n, (ast.Assign, ast.If, ast.While, ast.Skip)))
    variables = sorted({n.name for n in program.walk() if isinstance(n, ast.Var)})
    out.result = {"statements": statements, "variables": variables}
    out.text = (f"синтаксично коректна програма: операторів - {statements}, "
                f"змінні - {', '.join(variables) or 'немає'}")


def _cmd_parse(args, out: _Output, source: str) -> None:
    program = build_program(Parser(Lexer(source).tokenize()).program())
    out.result = {"ast": program.to_dict()}
    if args.format == "source":
        out.text = ast.program_to_source(program).rstrip("\n")
    elif args.format == "json":
        out.text = json.dumps(program.to_dict(), ensure_ascii=False, indent=2)
    else:
        from .visualization import from_ast, to_text

        out.text = to_text(from_ast(program, source))


def _cmd_tree(args, out: _Output, source: str) -> None:
    from .visualization import from_ast, from_parse_tree, render_tree

    try:
        out.text = render_tree(source, args.view, args.format)
    except ValueError as err:
        raise UsageError(str(err)) from None
    cst = Parser(Lexer(source).tokenize()).program()
    out.result = {}
    if args.view in ("parse", "both"):
        out.result["parse_tree"] = from_parse_tree(cst, source).to_dict()
    if args.view in ("ast", "both"):
        out.result["ast"] = from_ast(build_program(cst), source).to_dict()
    if args.format in ("dot", "svg", "html"):
        out.result["rendered"] = {"format": args.format, "content": out.text}


def _cmd_term(args, out: _Output, source: str) -> None:
    from .semantics.terms import explain_construction, render, to_dict

    program = build_program(Parser(Lexer(source).tokenize()).program())
    term, steps = explain_construction(program)
    out.result = {
        "term": to_dict(term),
        "notation": {style: render(term, style) for style in ("unicode", "ascii", "latex")},
        "construction": [{
            "rule": s.rule, "span": s.span.to_dict(),
            "source": " ".join(source[s.span.start:s.span.end].split()),
            "term": render(s.term, args.style),
        } for s in steps],
    }
    lines = [render(term, args.style, args.pretty)]
    if args.explain:
        lines += ["", "Побудова (правила застосовано рекурсивно за структурою AST):"]
        for i, s in enumerate(steps, 1):
            fragment = " ".join(source[s.span.start:s.span.end].split())
            if len(fragment) > 48:
                fragment = fragment[:47] + "…"
            lines.append(f"{i:>3}. [{s.span}] {s.rule}")
            lines.append(f"       {fragment}  ⟼  {render(s.term, args.style)}")
    out.text = "\n".join(lines)


def _cmd_eval(args, out: _Output, source: str) -> None:
    from .semantics.evaluate import evaluate, format_state, format_trace
    from .semantics.terms import build_term, from_dict, render

    if args.from_term:
        try:
            data = json.loads(source)
            data = data.get("result", data)
            term = from_dict(data.get("term", data))
        except (ValueError, KeyError, TypeError, AttributeError) as err:
            raise UsageError(f"не вдалося прочитати терм із JSON: {err}") from None
    else:
        term = build_term(build_program(Parser(Lexer(source).tokenize()).program()))
    state = parse_state(args.state, args.state_file)
    try:
        result = evaluate(term, state, max_steps=args.max_steps, detail=args.detail,
                          style=args.style)
    except ValueError as err:
        raise UsageError(f"некоректний терм: {err}") from None
    out.result = {"term": render(term, args.style), "initial_state": dict(sorted(state.items())),
                  **result.to_dict()}
    lines = [f"Терм: {render(term, args.style)}", f"Початковий стан: {format_state(state)}"]
    if args.trace or args.detail:
        lines += ["", "Застосовані правила:", format_trace(result), ""]
    if result.defined:
        lines.append(f"Результат: {format_state(result.state or {})}  "
                     f"(переходів: {result.transitions})")
    else:
        assert result.reason is not None
        out.ok = False
        code = "R002" if result.reason.kind == "step_limit" else "R001"
        out.diagnostics.append(Diagnostic(code, result.reason.message, result.reason.span))
        lines.append("Результат: НЕ ВИЗНАЧЕНО (значення терму на цьому стані не отримано)")
    out.text = "\n".join(lines)


def _cmd_prove(args, out: _Output, source: str) -> None:
    from .proof import Options, format_report, verify

    lexer = Lexer(source)
    program = build_program(Parser(lexer.tokenize()).program())
    report = verify(program, lexer.annotations, source, pre=args.pre, post=args.post,
                    invariants=args.inv, variants=args.variant,
                    options=Options(max_steps=args.max_steps))
    out.result = report.to_dict()
    out.ok = report.ok
    out.text = format_report(report)


# -- розбір аргументів ---------------------------------------------------------------------
def build_arg_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true",
                        help="структурований JSON-вивід для інтеграцій")
    parser = argparse.ArgumentParser(
        prog="sipl", description="SIPL Language Toolkit: парсер, дерева, семантичні терми, "
                                 "асистент доведення.")
    parser.add_argument("--version", action="version", version=f"sipl {__version__}")
    parser.add_argument("--json", dest="json_top", action="store_true",
                        help="структурований JSON-вивід (можна вказати й після команди)")
    sub = parser.add_subparsers(dest="command", required=True, metavar="КОМАНДА")

    def command(name: str, help_text: str) -> argparse.ArgumentParser:
        p = sub.add_parser(name, help=help_text, description=help_text, parents=[common])
        p.add_argument("file", help="файл із програмою SIPL ('-' - стандартний ввід)")
        return p

    command("check", "перевірити синтаксичну коректність програми")

    p = command("parse", "побудувати AST")
    p.add_argument("--format", choices=("text", "json", "source"), default="text",
                   help="text - дерево; json - AST; source - програма, відтворена з AST")

    p = command("tree", "дерево синтаксичного виводу та/або AST")
    p.add_argument("--view", choices=("parse", "ast", "both"), default="parse")
    p.add_argument("--format", choices=("text", "json", "dot", "svg", "html"), default="text")
    p.add_argument("-o", "--output", help="записати результат у файл")

    p = command("term", "побудувати семантичний терм")
    p.add_argument("--style", choices=("unicode", "ascii", "latex"), default="unicode")
    p.add_argument("--pretty", action="store_true", help="багаторядковий запис")
    p.add_argument("--explain", action="store_true", help="показати правила побудови")
    p.add_argument("-o", "--output", help="записати результат у файл")

    p = command("eval", "обчислити семантичний терм на стані")
    p.add_argument("--state", help="початковий стан, напр. \"X=2,Y=3\"")
    p.add_argument("--state-file", help="JSON-файл зі станом")
    p.add_argument("--from-term", action="store_true",
                   help="FILE - це JSON із термом (вивід 'sipl term --json')")
    p.add_argument("--trace", action="store_true", help="показати застосовані правила")
    p.add_argument("--detail", action="store_true",
                   help="додати кроки обчислення виразів")
    p.add_argument("--max-steps", type=int, default=10_000, help="ліміт переходів")
    p.add_argument("--style", choices=("unicode", "ascii", "latex"), default="unicode")

    p = command("prove", "перевірити коректність програми з циклами за інваріантом")
    p.add_argument("--pre", help="передумова (замість //@ pre:)")
    p.add_argument("--post", help="післяумова (замість //@ post:)")
    p.add_argument("--inv", action="append", help="інваріант чергового циклу (можна повторювати)")
    p.add_argument("--variant", action="append", help="варіант чергового циклу")
    p.add_argument("--max-steps", type=int, default=2_000,
                   help="ліміт кроків одного запуску в динамічній перевірці")
    return parser


_COMMANDS = {"check": _cmd_check, "parse": _cmd_parse, "tree": _cmd_tree, "term": _cmd_term,
             "eval": _cmd_eval, "prove": _cmd_prove}


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    try:
        args = build_arg_parser().parse_args(argv)
    except SystemExit as exit_:
        return int(exit_.code or 0) if exit_.code in (0, None) else EXIT_USAGE
    args.json = args.json or args.json_top
    out = _Output(args)
    source: str | None = None
    filename = args.file
    try:
        if getattr(args, "max_steps", 1) < 1:
            raise UsageError("--max-steps має бути додатним числом")
        source, filename = _read(args.file)
        _COMMANDS[args.command](args, out, source)
    except UsageError as err:
        if args.json:
            out.ok = False
            out.diagnostics.append(Diagnostic("U001", str(err)))
            out.emit(source, filename)
        else:
            print(f"sipl: {err}", file=sys.stderr)
        return EXIT_USAGE
    except SiplError as err:
        out.ok = False
        out.result = None
        out.text = ""
        out.diagnostics.append(err.diagnostic)
    return out.emit(source, filename)


if __name__ == "__main__":
    sys.exit(main())
