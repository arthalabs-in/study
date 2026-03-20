"""
LaTeX → Unicode renderer for terminal display.
Pure-Python, zero external dependencies, instant startup.
Detects $...$ (inline) and $$...$$ (display) blocks in text.
"""

from __future__ import annotations

import re

# ── Greek letters ──────────────────────────────────────────────────

_GREEK = {
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ",
    "epsilon": "ε", "varepsilon": "ε", "zeta": "ζ", "eta": "η",
    "theta": "θ", "vartheta": "ϑ", "iota": "ι", "kappa": "κ",
    "lambda": "λ", "mu": "μ", "nu": "ν", "xi": "ξ",
    "pi": "π", "rho": "ρ", "varrho": "ϱ", "sigma": "σ",
    "varsigma": "ς", "tau": "τ", "upsilon": "υ", "phi": "φ",
    "varphi": "φ", "chi": "χ", "psi": "ψ", "omega": "ω",
    # Uppercase
    "Gamma": "Γ", "Delta": "Δ", "Theta": "Θ", "Lambda": "Λ",
    "Xi": "Ξ", "Pi": "Π", "Sigma": "Σ", "Upsilon": "Υ",
    "Phi": "Φ", "Psi": "Ψ", "Omega": "Ω",
}

# ── Superscripts / Subscripts ─────────────────────────────────────

_SUPERSCRIPTS = {
    "0": "⁰", "1": "¹", "2": "²", "3": "³", "4": "⁴",
    "5": "⁵", "6": "⁶", "7": "⁷", "8": "⁸", "9": "⁹",
    "+": "⁺", "-": "⁻", "=": "⁼", "(": "⁽", ")": "⁾",
    "a": "ᵃ", "b": "ᵇ", "c": "ᶜ", "d": "ᵈ", "e": "ᵉ",
    "f": "ᶠ", "g": "ᵍ", "h": "ʰ", "i": "ⁱ", "j": "ʲ",
    "k": "ᵏ", "l": "ˡ", "m": "ᵐ", "n": "ⁿ", "o": "ᵒ",
    "p": "ᵖ", "r": "ʳ", "s": "ˢ", "t": "ᵗ", "u": "ᵘ",
    "v": "ᵛ", "w": "ʷ", "x": "ˣ", "y": "ʸ", "z": "ᶻ",
    "T": "ᵀ",
}

_SUBSCRIPTS = {
    "0": "₀", "1": "₁", "2": "₂", "3": "₃", "4": "₄",
    "5": "₅", "6": "₆", "7": "₇", "8": "₈", "9": "₉",
    "+": "₊", "-": "₋", "=": "₌", "(": "₍", ")": "₎",
    "a": "ₐ", "e": "ₑ", "h": "ₕ", "i": "ᵢ", "j": "ⱼ",
    "k": "ₖ", "l": "ₗ", "m": "ₘ", "n": "ₙ", "o": "ₒ",
    "p": "ₚ", "r": "ᵣ", "s": "ₛ", "t": "ₜ", "u": "ᵤ",
    "v": "ᵥ", "x": "ₓ",
}

# ── Operators / Symbols ───────────────────────────────────────────

_SYMBOLS = {
    # Big operators
    r"\sum": "∑", r"\prod": "∏", r"\coprod": "∐",
    r"\int": "∫", r"\iint": "∬", r"\iiint": "∭",
    r"\oint": "∮",

    # Calculus / analysis
    r"\partial": "∂", r"\nabla": "∇", r"\infty": "∞",
    r"\lim": "lim", r"\to": "→", r"\gets": "←",

    # Relations
    r"\leq": "≤", r"\geq": "≥", r"\neq": "≠",
    r"\approx": "≈", r"\equiv": "≡", r"\sim": "∼",
    r"\simeq": "≃", r"\cong": "≅", r"\propto": "∝",
    r"\ll": "≪", r"\gg": "≫", r"\prec": "≺", r"\succ": "≻",
    r"\perp": "⊥", r"\parallel": "∥",
    r"\le": "≤", r"\ge": "≥", r"\ne": "≠",

    # Set theory
    r"\in": "∈", r"\notin": "∉", r"\ni": "∋",
    r"\subset": "⊂", r"\supset": "⊃",
    r"\subseteq": "⊆", r"\supseteq": "⊇",
    r"\cup": "∪", r"\cap": "∩", r"\setminus": "∖",
    r"\emptyset": "∅", r"\varnothing": "∅",

    # Logic
    r"\forall": "∀", r"\exists": "∃", r"\nexists": "∄",
    r"\neg": "¬", r"\lnot": "¬",
    r"\land": "∧", r"\lor": "∨",
    r"\implies": "⟹", r"\iff": "⟺",
    r"\therefore": "∴", r"\because": "∵",
    r"\oplus": "⊕", r"\otimes": "⊗",

    # Arrows
    r"\rightarrow": "→", r"\leftarrow": "←",
    r"\Rightarrow": "⇒", r"\Leftarrow": "⇐",
    r"\leftrightarrow": "↔", r"\Leftrightarrow": "⇔",
    r"\uparrow": "↑", r"\downarrow": "↓",
    r"\mapsto": "↦", r"\hookrightarrow": "↪",
    r"\nearrow": "↗", r"\searrow": "↘",
    r"\longrightarrow": "⟶", r"\longleftarrow": "⟵",

    # Binary ops
    r"\times": "×", r"\div": "÷", r"\cdot": "·",
    r"\pm": "±", r"\mp": "∓", r"\circ": "∘",
    r"\star": "⋆", r"\bullet": "•", r"\dagger": "†",

    # Dots
    r"\cdots": "⋯", r"\ldots": "…", r"\vdots": "⋮", r"\ddots": "⋱",
    r"\dots": "…",

    # Geometry / misc
    r"\angle": "∠", r"\triangle": "△", r"\diamond": "◇",
    r"\square": "□", r"\hbar": "ℏ", r"\ell": "ℓ",
    r"\Re": "ℜ", r"\Im": "ℑ", r"\aleph": "ℵ", r"\wp": "℘",

    # Spacing (strip)
    r"\,": " ", r"\;": " ", r"\!": "", r"\:": " ",
    r"\quad": "  ", r"\qquad": "    ",

    # Delimiters
    r"\langle": "⟨", r"\rangle": "⟩",
    r"\lceil": "⌈", r"\rceil": "⌉",
    r"\lfloor": "⌊", r"\rfloor": "⌋",
    r"\{": "{", r"\}": "}",
    r"\|": "‖",

    # Accents / hats (simplified)
    r"\hat": "̂", r"\tilde": "̃", r"\bar": "̄",
    r"\vec": "⃗", r"\dot": "̇", r"\ddot": "̈",
    r"\overline": "‾",
}

# ── Blackboard bold ────────────────────────────────────────────────

_MATHBB = {
    "A": "𝔸", "B": "𝔹", "C": "ℂ", "D": "𝔻", "E": "𝔼",
    "F": "𝔽", "G": "𝔾", "H": "ℍ", "I": "𝕀", "J": "𝕁",
    "K": "𝕂", "L": "𝕃", "M": "𝕄", "N": "ℕ", "O": "𝕆",
    "P": "ℙ", "Q": "ℚ", "R": "ℝ", "S": "𝕊", "T": "𝕋",
    "U": "𝕌", "V": "𝕍", "W": "𝕎", "X": "𝕏", "Y": "𝕐",
    "Z": "ℤ",
}

# Named trig/log functions that should render as upright text
_FUNCTIONS = {
    r"\sin", r"\cos", r"\tan", r"\cot", r"\sec", r"\csc",
    r"\arcsin", r"\arccos", r"\arctan",
    r"\sinh", r"\cosh", r"\tanh",
    r"\log", r"\ln", r"\exp", r"\det", r"\dim", r"\ker",
    r"\hom", r"\arg", r"\deg", r"\min", r"\max", r"\inf", r"\sup",
    r"\gcd", r"\mod", r"\Pr",
}


# ── Core converter ─────────────────────────────────────────────────


def _strip_braces(s: str) -> str:
    """Remove outermost {} if present."""
    s = s.strip()
    if s.startswith("{") and s.endswith("}"):
        return s[1:-1]
    return s


def _to_superscript(text: str) -> str:
    """Convert text to Unicode superscript characters.
    Falls back to ^[text] notation if any character can't be mapped."""
    text = _strip_braces(text)
    inner = render_latex(text)  # recursively render
    # Check if ALL characters can be mapped
    mapped = [_SUPERSCRIPTS.get(c) for c in inner]
    if all(m is not None for m in mapped):
        return "".join(mapped)
    # Fallback: clean bracket notation
    return f"^({inner})"


def _to_subscript(text: str) -> str:
    """Convert text to Unicode subscript characters.
    Falls back to _[text] notation if any character can't be mapped."""
    text = _strip_braces(text)
    inner = render_latex(text)  # recursively render
    # Check if ALL characters can be mapped
    mapped = [_SUBSCRIPTS.get(c) for c in inner]
    if all(m is not None for m in mapped):
        return "".join(mapped)
    # Fallback: clean bracket notation 
    return f"_({inner})"


def _find_brace_group(s: str, start: int) -> tuple[str, int] | None:
    """Extract content of a {...} group starting at position start.
    Returns (content, end_pos) or None if no brace group found."""
    if start >= len(s) or s[start] != '{':
        return None
    depth = 0
    for i in range(start, len(s)):
        if s[i] == '{':
            depth += 1
        elif s[i] == '}':
            depth -= 1
            if depth == 0:
                return s[start + 1:i], i + 1
    return None


def render_latex(latex: str) -> str:
    """Convert a LaTeX math expression to Unicode text."""
    s = latex.strip()

    # ── \mathbb{X} → blackboard bold
    s = re.sub(
        r"\\mathbb\{(\w)\}",
        lambda m: _MATHBB.get(m.group(1), m.group(1)),
        s,
    )

    # ── \text{...}, \mathrm{...}, \mathbf{...} etc → just the text
    s = re.sub(r"\\(?:text|mathrm|mathbf|mathit|mathcal|operatorname)\{([^}]*)\}", r"\1", s)

    # ── Symbols FIRST (before super/subscript to avoid mangling ∞ etc.)
    for cmd in sorted(_SYMBOLS, key=len, reverse=True):
        s = s.replace(cmd, _SYMBOLS[cmd])

    # ── Greek letters
    for name, sym in sorted(_GREEK.items(), key=lambda x: -len(x[0])):
        s = s.replace(f"\\{name}", sym)

    # ── Named functions → plain text
    for fn in _FUNCTIONS:
        name = fn[1:]  # strip backslash
        s = s.replace(fn, name)

    # ── \sqrt[n]{...} → ⁿ√(...)
    def _sqrt_n(m):
        n = m.group(1)
        body = render_latex(m.group(2))
        sup_n = _to_superscript(n)
        return f"{sup_n}√({body})"
    s = re.sub(r"\\sqrt\[([^\]]+)\]\{([^}]*)\}", _sqrt_n, s)

    # ── \sqrt{...} → √(...)
    def _sqrt(m):
        body = render_latex(m.group(1))
        return f"√({body})"
    s = re.sub(r"\\sqrt\{([^}]*)\}", _sqrt, s)

    # ── \frac{a}{b} → a⁄b  (handles nested braces)
    while r'\frac' in s:
        idx = s.index(r'\frac')
        after = idx + 5  # len(r'\frac')
        g1 = _find_brace_group(s, after)
        if not g1:
            break
        num_str, pos2 = g1
        g2 = _find_brace_group(s, pos2)
        if not g2:
            break
        den_str, end = g2
        num = render_latex(num_str)
        den = render_latex(den_str)
        if len(num) <= 3 and len(den) <= 3:
            replacement = f"{num}⁄{den}"
        else:
            replacement = f"({num})⁄({den})"
        s = s[:idx] + replacement + s[end:]

    # ── \binom{n}{k} → C(n,k)
    def _binom(m):
        n = render_latex(m.group(1))
        k = render_latex(m.group(2))
        return f"C({n},{k})"
    s = re.sub(r"\\binom\{([^}]*)\}\{([^}]*)\}", _binom, s)

    # ── Superscripts: ^{...} or ^x
    def _sup_brace(m):
        return _to_superscript(m.group(1))
    s = re.sub(r"\^\{([^}]*)\}", _sup_brace, s)

    def _sup_char(m):
        return _to_superscript(m.group(1))
    s = re.sub(r"\^([a-zA-Z0-9+\-])", _sup_char, s)

    # ── Subscripts: _{...} or _x
    def _sub_brace(m):
        return _to_subscript(m.group(1))
    s = re.sub(r"_\{([^}]*)\}", _sub_brace, s)

    def _sub_char(m):
        return _to_subscript(m.group(1))
    s = re.sub(r"_([a-zA-Z0-9])", _sub_char, s)

    # ── Cleanup: strip remaining \left, \right, \big, \Big etc.
    s = re.sub(r"\\(?:left|right|big|Big|bigg|Bigg)\b", "", s)

    # ── Strip any remaining unknown backslash commands
    s = re.sub(r"\\[a-zA-Z]+", "", s)

    # ── Clean up braces and whitespace
    s = s.replace("{", "").replace("}", "")
    s = re.sub(r"  +", " ", s)

    return s.strip()


# ── Inline detection & replacement ─────────────────────────────────

# Match $$...$$ (display) and $...$ (inline), avoiding escaped \$
_DISPLAY_RE = re.compile(r"\$\$(.+?)\$\$", re.DOTALL)
_INLINE_RE = re.compile(r"(?<!\$)\$(?!\$)(.+?)(?<!\$)\$(?!\$)")


def render_math_in_text(text: str) -> str:
    """Find and render all LaTeX math expressions in a text string.

    - $$...$$ → display math (indented on its own line)
    - $...$  → inline math (rendered in place)
    """
    # Display math first
    def _display_replace(m):
        rendered = render_latex(m.group(1))
        return f"\n    {rendered}\n"

    result = _DISPLAY_RE.sub(_display_replace, text)

    # Inline math
    def _inline_replace(m):
        return render_latex(m.group(1))

    result = _INLINE_RE.sub(_inline_replace, result)

    return result
