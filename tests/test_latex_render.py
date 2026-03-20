from __future__ import annotations

from src.latex_render import (
    _find_brace_group,
    _strip_braces,
    _to_subscript,
    _to_superscript,
    render_latex,
    render_math_in_text,
)


def test_strip_braces_and_find_group() -> None:
    assert _strip_braces(' {alpha} ') == 'alpha'
    assert _strip_braces('alpha') == 'alpha'
    assert _find_brace_group('{a{b}c}', 0) == ('a{b}c', 7)
    assert _find_brace_group('nope', 0) is None


def test_super_and_subscript_fallbacks() -> None:
    assert _to_superscript('{10}') == '¹⁰'
    assert _to_subscript('{x2}') == 'ₓ₂'
    assert _to_superscript('{A/B}') == '^(A/B)'
    assert _to_subscript('{H2O}') == '_(H2O)'


def test_render_latex_handles_symbols_structures_and_cleanup() -> None:
    assert render_latex(r'\alpha + \beta \to \infty') == 'α + β → ∞'
    assert render_latex(r'\frac{a+b}{c}') == 'a+b⁄c'
    assert render_latex(r'\sqrt[3]{x^2}') == '³√(x²)'
    assert render_latex(r'\sqrt{y_2}') == '√(y₂)'
    assert render_latex(r'\binom{n}{k}') == 'C(n,k)'
    assert render_latex(r'\mathbb{R} \subseteq \mathbb{C}') == 'ℝ ⊆ ℂ'
    assert render_latex(r'\text{speed} = x_1^2') == 'speed = x₁²'
    assert render_latex(r'\unknowncmd{z}') == 'z'


def test_render_math_in_text_rewrites_inline_and_display_math() -> None:
    text = 'Inline $\\frac{1}{2}$ and display $$\\sum_{i=1}^{3} x_i$$ done.'
    rendered = render_math_in_text(text)
    assert 'Inline 1⁄2' in rendered
    assert "\n    ∑ᵢ₌₁³ xᵢ\n" in rendered
    assert rendered.endswith(' done.')


def test_render_math_in_text_leaves_non_math_content_alone() -> None:
    text = r'Price is \$5 and text without math stays put.'
    assert render_math_in_text(text) == text
