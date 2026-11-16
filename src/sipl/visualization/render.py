"""Рендерери дерева: текст, JSON, DOT, SVG та інтерактивний HTML."""

from __future__ import annotations

import html
import json

from .model import VizNode

FORMATS = ("text", "json", "dot", "svg", "html")


def _one_line(text: str, limit: int = 60) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def _where(node: VizNode) -> str:
    s = node.span
    return f"{s.line}:{s.column}-{s.end_line}:{s.end_column}"


# -- текст ---------------------------------------------------------------------
def to_text(root: VizNode, spans: bool = True) -> str:
    """Дерево псевдографікою; для кожного вузла - позиція у вихідному тексті."""
    lines: list[str] = []

    def visit(node: VizNode, prefix: str, tail: str) -> None:
        label = f"'{node.label}'" if node.kind == "terminal" else node.label
        suffix = f"  [{_where(node)}]" if spans else ""
        lines.append(f"{prefix}{tail}{label}{suffix}")
        child_prefix = prefix + ("" if not tail else ("    " if tail.startswith("└") else "│   "))
        for i, child in enumerate(node.children):
            visit(child, child_prefix, "└── " if i == len(node.children) - 1 else "├── ")

    visit(root, "", "")
    return "\n".join(lines)


# -- JSON ----------------------------------------------------------------------
def to_json(root: VizNode) -> str:
    return json.dumps(root.to_dict(), ensure_ascii=False, indent=2)


# -- DOT -----------------------------------------------------------------------
_DOT_STYLE = {
    "nonterminal": 'shape=box, style="rounded,filled", fillcolor="#e8eefc"',
    "terminal": 'shape=box, style=filled, fillcolor="#fff4d6"',
    "program": 'shape=box, style="rounded,filled", fillcolor="#dcefe2"',
    "statement": 'shape=box, style="rounded,filled", fillcolor="#e8eefc"',
    "expression": 'shape=ellipse, style=filled, fillcolor="#fff4d6"',
    "condition": 'shape=hexagon, style=filled, fillcolor="#fbe3e6"',
}


def _dot_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def to_dot(root: VizNode, name: str = "sipl") -> str:
    """Graphviz DOT; підказка (tooltip) вузла - його фрагмент коду та позиція."""
    out = [f"digraph {name} {{", '  node [fontname="Consolas, monospace", fontsize=11];',
           "  edge [arrowhead=none];"]
    for node in root.walk():
        tooltip = _dot_escape(f"{_where(node)}: {_one_line(node.text)}")
        out.append(f'  n{node.id} [label="{_dot_escape(node.label)}", {_DOT_STYLE[node.kind]}, '
                   f'tooltip="{tooltip}"];')
    for node in root.walk():
        for child in node.children:
            out.append(f"  n{node.id} -> n{child.id};")
    out.append("}")
    return "\n".join(out)


# -- SVG -----------------------------------------------------------------------
_CHAR_W = 7.3
_NODE_H = 26
_LEVEL_H = 58
_GAP = 12
_MARGIN = 16

_SVG_CSS = """
.sipl-tree text{font:12px Consolas,'DejaVu Sans Mono',monospace;text-anchor:middle;
dominant-baseline:central;fill:#1c2333;pointer-events:none}
.sipl-tree line{stroke:#8a93a6;stroke-width:1.2}
.sipl-tree rect{stroke:#5b6785;stroke-width:1;rx:6}
.sipl-tree .nonterminal rect,.sipl-tree .statement rect{fill:#e8eefc}
.sipl-tree .terminal rect,.sipl-tree .expression rect{fill:#fff4d6}
.sipl-tree .condition rect{fill:#fbe3e6}
.sipl-tree .program rect{fill:#dcefe2}
.sipl-tree .node{cursor:pointer}
.sipl-tree .node.match rect{stroke:#c2410c;stroke-width:2.5}
.sipl-tree .node.sel rect{stroke:#c2410c;stroke-width:3;fill:#fed7aa}
"""


def _layout(root: VizNode) -> tuple[dict[int, tuple[float, float, float]], float, float]:
    """Координати вузлів: id -> (центр x, верх y, ширина)."""
    widths: dict[int, float] = {}
    own: dict[int, float] = {}

    def measure(node: VizNode) -> float:
        own[node.id] = max(30.0, len(node.label) * _CHAR_W + 18)
        kids = sum(measure(c) for c in node.children) + _GAP * max(0, len(node.children) - 1)
        widths[node.id] = max(own[node.id], kids)
        return widths[node.id]

    pos: dict[int, tuple[float, float, float]] = {}
    depth_max = 0

    def place(node: VizNode, left: float, depth: int) -> None:
        nonlocal depth_max
        depth_max = max(depth_max, depth)
        pos[node.id] = (left + widths[node.id] / 2, _MARGIN + depth * _LEVEL_H, own[node.id])
        kids = sum(widths[c.id] for c in node.children) + _GAP * max(0, len(node.children) - 1)
        x = left + (widths[node.id] - kids) / 2
        for child in node.children:
            place(child, x, depth + 1)
            x += widths[child.id] + _GAP

    total = measure(root)
    place(root, _MARGIN, 0)
    return pos, total + 2 * _MARGIN, 2 * _MARGIN + depth_max * _LEVEL_H + _NODE_H


def to_svg(root: VizNode, standalone: bool = True, tree_id: str = "tree") -> str:
    """SVG-дерево. Кожен вузол несе ``data-start``/``data-end`` (зсуви у
    вихідному тексті) та ``<title>`` з фрагментом коду."""
    pos, width, height = _layout(root)
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" class="sipl-tree" data-tree="{tree_id}" '
        f'width="{width:.0f}" height="{height:.0f}" viewBox="0 0 {width:.0f} {height:.0f}">'
    ]
    if standalone:
        parts.append(f"<style>{_SVG_CSS}</style>")
        parts.append(f'<rect width="{width:.0f}" height="{height:.0f}" '
                     'style="fill:#ffffff;stroke:none"/>')
    for node in root.walk():
        x, y, _ = pos[node.id]
        for child in node.children:
            cx, cy, _ = pos[child.id]
            parts.append(f'<line x1="{x:.1f}" y1="{y + _NODE_H:.1f}" x2="{cx:.1f}" '
                         f'y2="{cy:.1f}"/>')
    for node in root.walk():
        x, y, w = pos[node.id]
        title = html.escape(f"{node.detail}\n{_where(node)}: {_one_line(node.text)}")
        parts.append(
            f'<g class="node {node.kind}" data-id="{node.id}" data-start="{node.span.start}" '
            f'data-end="{node.span.end}"><title>{title}</title>'
            f'<rect x="{x - w / 2:.1f}" y="{y:.1f}" width="{w:.1f}" height="{_NODE_H}"/>'
            f'<text x="{x:.1f}" y="{y + _NODE_H / 2:.1f}">{html.escape(node.label)}</text></g>'
        )
    parts.append("</svg>")
    return "\n".join(parts)


# -- HTML ----------------------------------------------------------------------
_HTML_CSS = """
:root{color-scheme:light}
body{margin:0;font:14px/1.5 system-ui,'Segoe UI',sans-serif;background:#f5f6f9;color:#1c2333}
header{padding:12px 20px;background:#1c2333;color:#fff}
header h1{margin:0;font-size:17px;font-weight:600}
header p{margin:2px 0 0;font-size:12.5px;color:#c5cbe0}
main{display:grid;grid-template-columns:minmax(260px,340px) 1fr;gap:16px;padding:16px 20px}
@media(max-width:820px){main{grid-template-columns:1fr}}
section{background:#fff;border:1px solid #d8dce6;border-radius:8px;padding:12px 14px;min-width:0}
h2{margin:0 0 8px;font-size:14px}
#src{margin:0;font:13px/1.55 Consolas,'DejaVu Sans Mono',monospace;white-space:pre-wrap;
position:sticky;top:12px}
#src span{cursor:pointer}
#src span.hl{background:#fed7aa}
.scroll{overflow:auto}
.trees{display:grid;gap:16px;min-width:0}
#info{font:12.5px Consolas,monospace;color:#4a5370;min-height:1.5em;margin-top:8px}
"""

_HTML_JS = """
const chars = document.querySelectorAll('#src span');
const nodes = [...document.querySelectorAll('.sipl-tree .node')];
const info = document.getElementById('info');
let pinned = null;
function show(start, end, origin) {
  chars.forEach((c, i) => c.classList.toggle('hl', i >= start && i < end));
  nodes.forEach(n => {
    const same = +n.dataset.start === start && +n.dataset.end === end;
    n.classList.toggle('sel', n === origin);
    n.classList.toggle('match', same && n !== origin);
  });
  info.textContent = origin ? origin.querySelector('title').textContent : '';
}
function clear() { show(-1, -1, null); }
function restore() { pinned ? show(+pinned.dataset.start, +pinned.dataset.end, pinned) : clear(); }
nodes.forEach(n => {
  n.addEventListener('mouseenter', () => show(+n.dataset.start, +n.dataset.end, n));
  n.addEventListener('mouseleave', restore);
  n.addEventListener('click', () => { pinned = pinned === n ? null : n; restore(); });
});
// клік по символу коду -> найменший вузол кожного дерева, що його містить
chars.forEach((c, i) => c.addEventListener('click', () => {
  let best = null;
  for (const n of nodes) {
    const s = +n.dataset.start, e = +n.dataset.end;
    if (s <= i && i < e && (!best || e - s <= best.dataset.end - best.dataset.start)) best = n;
  }
  pinned = best; restore();
  if (best) best.scrollIntoView({block: 'nearest', inline: 'center', behavior: 'smooth'});
}));
"""


def to_html(source: str, trees: list[tuple[str, VizNode]], title: str = "SIPL") -> str:
    """Самодостатня HTML-сторінка: код + дерева з двобічною навігацією.

    Наведення на вузол підсвічує його фрагмент коду та вузли інших дерев із
    тим самим проміжком; клік по символу коду вибирає найменший вузол, що
    його містить.
    """
    chars = "".join(f"<span>{html.escape(ch)}</span>" for ch in source)
    sections = "".join(
        f'<section><h2>{html.escape(name)}</h2><div class="scroll">'
        f"{to_svg(tree, standalone=False, tree_id=f't{i}')}</div></section>"
        for i, (name, tree) in enumerate(trees)
    )
    return (
        '<!doctype html>\n<html lang="uk"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{html.escape(title)}</title><style>{_HTML_CSS}{_SVG_CSS}</style></head><body>"
        f"<header><h1>{html.escape(title)}</h1><p>Наведіть на вузол, щоб побачити його фрагмент "
        "коду; клацніть по коду, щоб знайти відповідні вузли.</p></header>"
        '<main><section><h2>Вихідний текст</h2>'
        f'<pre id="src">{chars}</pre><div id="info"></div></section>'
        f'<div class="trees">{sections}</div></main>'
        f"<script>{_HTML_JS}</script></body></html>\n"
    )
