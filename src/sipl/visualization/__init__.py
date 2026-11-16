"""Візуалізація дерева синтаксичного виводу та AST."""

from .model import VizNode, from_ast, from_parse_tree
from .render import FORMATS, to_dot, to_html, to_json, to_svg, to_text

__all__ = ["VizNode", "from_ast", "from_parse_tree", "FORMATS", "to_dot", "to_html",
           "to_json", "to_svg", "to_text"]


def render_tree(source: str, view: str = "parse", fmt: str = "text") -> str:
    """Зручна обгортка: ``view`` - ``parse`` | ``ast`` | ``both``."""
    from ..ast_builder import build_program
    from ..parser import parse_tree

    if fmt not in FORMATS:
        raise ValueError(f"невідомий формат {fmt!r}; доступні: {', '.join(FORMATS)}")
    if view not in ("parse", "ast", "both"):
        raise ValueError(f"невідоме подання {view!r}; доступні: parse, ast, both")
    cst = parse_tree(source)
    named = []
    if view in ("parse", "both"):
        named.append(("Дерево синтаксичного виводу", from_parse_tree(cst, source)))
    if view in ("ast", "both"):
        named.append(("Абстрактне синтаксичне дерево (AST)",
                      from_ast(build_program(cst), source)))
    if fmt == "html":
        return to_html(source, named, "SIPL: дерево виводу та AST")
    if fmt == "json":
        import json

        if len(named) == 1:
            return to_json(named[0][1])
        return json.dumps({"parse_tree": named[0][1].to_dict(), "ast": named[1][1].to_dict()},
                          ensure_ascii=False, indent=2)
    one = {"text": to_text, "dot": to_dot, "svg": to_svg}[fmt]
    if len(named) == 1:
        return one(named[0][1])
    if fmt == "text":
        return "\n\n".join(f"== {name} ==\n{to_text(tree)}" for name, tree in named)
    raise ValueError(f"формат {fmt} показує одне дерево: оберіть --view parse або --view ast "
                     "(обидва дерева разом доступні у форматах text, json, html)")
