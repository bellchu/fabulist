"""Printing a screen must give content on white in the light palette, whatever theme the person uses."""
import os
import re

CSS = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app", "static", "app.css")).read()


def _vars(block: str) -> dict:
    return dict(re.findall(r"(--[\w-]+):\s*([^;]+);", block))


def test_print_palette_is_the_light_palette():
    light = _vars(re.search(r":root \{\n(.*?)\n  color-scheme: light;", CSS, re.S).group(1))
    printed = _vars(re.search(r"@media print \{\s*/\*.*?\*/\s*:root, :root:not\(\[data-theme=\"light\"\]\), :root\[data-theme=\"dark\"\] \{(.*?)\}", CSS, re.S).group(1))
    assert len(light) > 15 and printed == light, "the print block must restate the light palette exactly"


def test_print_hides_navigation_and_controls_and_lets_tables_break():
    block = CSS[CSS.index("@media print {"):]
    for selector in (".global-header", ".context-bar", ".tabs", ".path-wrap", ".btn", "select", "input", ".toolbar", ".overlay", ".drawer"):
        assert selector in block.split("display: none")[0], selector + " must be hidden when printing"
    assert "thead { display: table-header-group; }" in block and ".table-wrap { overflow: visible; }" in block and "break-inside: avoid" in block
