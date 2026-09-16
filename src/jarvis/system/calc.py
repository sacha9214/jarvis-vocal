"""Calculs et conversions dits à voix haute, sans passer par le modèle.

Un petit modèle se trompe sur les nombres ; ici le calcul est exact. L'expression est analysée puis
évaluée nœud par nœud : seules les opérations mathématiques sont acceptées, jamais du code.
"""
from __future__ import annotations

import ast
import math
import operator
import re
import unicodedata

_BINARY = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
           ast.Div: operator.truediv, ast.Pow: operator.pow, ast.Mod: math.fmod,
           ast.FloorDiv: operator.floordiv}
_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_FUNCTIONS = {"racine": math.sqrt, "sqrt": math.sqrt, "abs": abs, "arrondi": round, "round": round,
              "cos": math.cos, "sin": math.sin, "tan": math.tan, "log": math.log10, "ln": math.log,
              "min": min, "max": max, "exp": math.exp}
_CONSTANTS = {"pi": math.pi, "e": math.e}
MAX_POWER = 1000            # 9 ** 9 ** 9 bloquerait la machine

# Mots dits à voix haute → symboles.
_WORDS = (
    (r"\bplus\b", "+"), (r"\bmoins\b", "-"), (r"\b(?:fois|multiplie par|multiplié par)\b", "*"),
    (r"\b(?:divise par|divisé par|sur)\b", "/"), (r"\b(?:puissance|exposant)\b", "**"),
    (r"\bmodulo\b", "%"), (r"\bvirgule\b", "."), (r"\b(?:egal|égal|égale|font|fait|ca fait|ça fait)\b", ""),
    (r"\bracine carr[ée]+e? de\b", "racine"), (r"\bracine de\b", "racine"),
    (r"\bpour ?cent de\b", "%de"), (r"\bpour ?cent\b", "%"),
)
_PERCENT_OF = re.compile(r"(?P<part>[\d.]+)\s*%de\s*(?P<whole>.+)")


def _plain(text: str) -> str:
    text = unicodedata.normalize("NFD", text.lower())
    return "".join(c for c in text if unicodedata.category(c) != "Mn")


def normalize(question: str) -> str:
    """« quinze pour cent de 340 » → « 15%de 340 » ; « deux fois trois » → « 2 * 3 »."""
    from ..commands import _NUMBERS

    text = question.strip().rstrip("?").strip()
    text = re.sub(r"^(?:combien (?:font|fait|vaut|est)|calcule|calculer|compute)\b", " ", _plain(text))
    text = re.sub(r"\b(?:la|le|les|de la|du)\b", " ", text)          # « la racine carrée de 2 »
    for pattern, replacement in _WORDS:
        text = re.sub(pattern, replacement, _plain(text))
    # Nombres écrits en toutes lettres, un mot à la fois (« vingt » → 20).
    text = " ".join(str(_NUMBERS[word]) if word in _NUMBERS else word for word in text.split())
    text = re.sub(r"\s+", " ", text).strip()
    # « racine 144 » n'est pas une expression : on en fait un appel, « racine(144) ».
    for name in _FUNCTIONS:
        text = re.sub(rf"\b{name}\s+(?!\()(?P<argument>[^,]+)$", rf"{name}(\g<argument>)", text)
    return text


def _evaluate(node: ast.AST) -> float:
    if isinstance(node, ast.Expression):
        return _evaluate(node.body)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise ValueError("valeur non numérique")
        return float(node.value)
    if isinstance(node, ast.BinOp) and type(node.op) in _BINARY:
        left, right = _evaluate(node.left), _evaluate(node.right)
        if isinstance(node.op, ast.Pow) and (abs(right) > MAX_POWER or abs(left) > MAX_POWER):
            raise ValueError("puissance trop grande")
        if isinstance(node.op, (ast.Div, ast.FloorDiv, ast.Mod)) and right == 0:
            raise ZeroDivisionError
        return _BINARY[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
        return _UNARY[type(node.op)](_evaluate(node.operand))
    if isinstance(node, ast.Name) and node.id in _CONSTANTS:
        return _CONSTANTS[node.id]
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FUNCTIONS:
        if node.keywords:
            raise ValueError("arguments nommés refusés")
        return float(_FUNCTIONS[node.func.id](*[_evaluate(a) for a in node.args]))
    raise ValueError("expression non autorisée")


def spoken_number(value: float) -> str:
    """Nombre lisible à voix haute : pas de 0.30000000000000004, pas de 12.0."""
    if value != value or value in (float("inf"), float("-inf")):
        raise ValueError("résultat impossible")
    rounded = round(value, 6)
    if abs(rounded - round(rounded)) < 1e-9 and abs(rounded) < 1e15:
        return f"{int(round(rounded)):,}".replace(",", " ")
    return f"{rounded:,}".replace(",", " ").replace(".", " virgule ")


def evaluate(question: str) -> str | None:
    """Résultat prêt à dire, ou None si ce n'est pas un calcul."""
    text = normalize(question)
    if not text or not any(char.isdigit() for char in text):
        return None
    if match := _PERCENT_OF.fullmatch(text.strip()):
        try:
            whole = _evaluate(ast.parse(match["whole"], mode="eval"))
            part = float(match["part"])
        except (SyntaxError, ValueError, TypeError, ZeroDivisionError, OverflowError, RecursionError):
            return None
        return f"{spoken_number(whole * part / 100)}."
    text = text.replace("%de", "*0.01*").replace("%", "*0.01")
    if not re.fullmatch(r"[0-9\s.+\-*/%()a-z]+", text):
        return None
    try:
        result = _evaluate(ast.parse(text, mode="eval"))
    except ZeroDivisionError:
        return "Une division par zéro, ça n'existe pas."
    except (SyntaxError, ValueError, TypeError, OverflowError, RecursionError, KeyError):
        return None
    try:
        return f"{spoken_number(result)}."
    except ValueError:
        return None
