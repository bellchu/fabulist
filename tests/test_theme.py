"""Light, dark and 'follow my system' are a choice on the Account page: the dark colours must exist twice (for the system setting and for the explicit choice) and agree."""
import os
import re

STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app", "static")
CSS = open(os.path.join(STATIC, "app.css")).read()


def _variables(block):
    return dict(re.findall(r"(--[\w-]+):\s*([^;]+);", block))


def test_the_explicit_dark_theme_matches_the_system_dark_theme():
    system = re.search(r'@media \(prefers-color-scheme: dark\) \{\s*:root:not\(\[data-theme="light"\]\) \{(.*?)\n  \}\n\}', CSS, re.S).group(1)
    explicit = re.search(r':root\[data-theme="dark"\] \{(.*?)\n\}', CSS, re.S).group(1)
    assert len(_variables(system)) >= 25
    assert _variables(system) == _variables(explicit)


def test_the_saved_choice_is_applied_before_the_page_paints_and_the_csp_allows_it():
    html = open(os.path.join(STATIC, "index.html")).read()
    head = html.split("</head>")[0]
    assert '<script src="/static/js/theme.js"></script>' in head, "a blocking script in the head, so there is no flash of the wrong theme"
    assert 'type="module"' not in head.split("theme.js")[0].split("<script")[-1]
    assert "<script>" not in html, "inline scripts are blocked by the Content Security Policy"
    js = open(os.path.join(STATIC, "js", "theme.js")).read()
    assert "try" in js and "localStorage" in js, "storage can be blocked: the page must still render"


def test_the_api_reference_follows_the_explicit_choice_too():
    css = open(os.path.join(STATIC, "docs.css")).read()
    assert '@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) .docs-req' in css, "system dark, unless light was chosen"
    assert ':root[data-theme="dark"] .docs-req' in css, "dark chosen on a light system"
    assert "\n@media (prefers-color-scheme: dark) { .docs-req" not in css, "no rule that ignores the choice"
