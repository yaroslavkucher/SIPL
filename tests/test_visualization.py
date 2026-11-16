"""Візуалізація дерева виводу (VIS-01) та AST і їх зіставлення (VIS-02)."""

import json
import re
import xml.etree.ElementTree as ET

import pytest

from sipl import parse, parse_tree
from sipl.visualization import (from_ast, from_parse_tree, render_tree, to_dot, to_html,
                                to_svg, to_text)

SOURCE = "begin\n  X := (Y + 1) * 2;\n  if X > 3 then skip else X := 0\nend"


@pytest.fixture
def cst():
    return from_parse_tree(parse_tree(SOURCE), SOURCE)


@pytest.fixture
def tree_ast():
    return from_ast(parse(SOURCE), SOURCE)


def test_every_node_is_linked_to_its_source_fragment(cst, tree_ast):
    for root in (cst, tree_ast):
        for node in root.walk():
            assert node.text == SOURCE[node.span.start:node.span.end]
            for child in node.children:
                assert node.span.start <= child.span.start and child.span.end <= node.span.end
    assert cst.text == SOURCE


def test_text_rendering(cst):
    text = to_text(cst)
    lines = text.splitlines()
    assert lines[0] == "<програма>  [1:1-4:4]"
    assert "├── 'begin'  [1:1-1:6]" in lines
    assert any(line.endswith("'Y'  [2:9-2:10]") for line in lines)
    assert len(lines) == sum(1 for _ in cst.walk())
    assert "[" not in to_text(cst, spans=False)


def test_json_rendering_is_structured(cst):
    data = json.loads(render_tree(SOURCE, "parse", "json"))
    assert data["label"] == "<програма>" and data["kind"] == "nonterminal"
    assert data["detail"] == "<програма> ::= begin <оператор> end"
    assert [c["label"] for c in data["children"]] == ["begin", "<оператор>", "end"]
    assert data["children"][0]["span"] == {
        "start": 0, "end": 5, "line": 1, "column": 1, "end_line": 1, "end_column": 6}
    both = json.loads(render_tree(SOURCE, "both", "json"))
    assert set(both) == {"parse_tree", "ast"}


def test_dot_rendering(cst):
    dot = to_dot(cst)
    nodes = sum(1 for _ in cst.walk())
    assert dot.startswith("digraph sipl {") and dot.rstrip().endswith("}")
    assert len(re.findall(r"^  n\d+ \[label=", dot, re.M)) == nodes
    assert len(re.findall(r"^  n\d+ -> n\d+;", dot, re.M)) == nodes - 1
    assert 'tooltip="2:3-2:19: X := (Y + 1) * 2"' in dot


def test_svg_is_well_formed_and_carries_spans(cst):
    svg = to_svg(cst)
    root = ET.fromstring(svg)
    ns = "{http://www.w3.org/2000/svg}"
    groups = root.findall(f"{ns}g")
    nodes = list(cst.walk())
    assert len(groups) == len(nodes)
    assert len(root.findall(f"{ns}line")) == len(nodes) - 1
    for group, node in zip(groups, nodes):
        assert int(group.get("data-start")) == node.span.start
        assert int(group.get("data-end")) == node.span.end
        assert group.find(f"{ns}text").text == node.label
    # вузли одного рівня не перекриваються
    rects = [(float(g.find(f"{ns}rect").get("y")), float(g.find(f"{ns}rect").get("x")),
              float(g.find(f"{ns}rect").get("width"))) for g in groups]
    by_level: dict[float, list[tuple[float, float]]] = {}
    for y, x, w in rects:
        by_level.setdefault(y, []).append((x, x + w))
    for spans in by_level.values():
        spans.sort()
        assert all(a[1] <= b[0] for a, b in zip(spans, spans[1:]))


def test_ast_is_smaller_and_drops_concrete_syntax(cst, tree_ast):
    assert sum(1 for _ in tree_ast.walk()) < sum(1 for _ in cst.walk())
    labels = [n.label for n in tree_ast.walk()]
    assert "(" not in labels and "begin" not in labels
    assert labels[:3] == ["Program", ";", ":="]


def test_parse_tree_and_ast_nodes_correspond_by_span(cst, tree_ast):
    cst_spans = {(n.span.start, n.span.end) for n in cst.walk()}
    for node in tree_ast.walk():
        assert (node.span.start, node.span.end) in cst_spans


def test_html_view_embeds_both_trees(cst, tree_ast):
    page = to_html(SOURCE, [("Дерево виводу", cst), ("AST", tree_ast)], "демо")
    assert page.startswith("<!doctype html>")
    assert page.count("<svg") == 2 and page.count('data-tree="t') == 2
    assert page.count("<span>") == len(SOURCE)
    assert "&lt;програма&gt;" in page and "<script>" in page
    assert render_tree(SOURCE, "both", "html").count("<svg") == 2


def test_render_tree_argument_errors():
    with pytest.raises(ValueError):
        render_tree(SOURCE, "parse", "png")
    with pytest.raises(ValueError):
        render_tree(SOURCE, "both", "svg")
    assert "== Абстрактне" in render_tree(SOURCE, "both", "text")
