"""Windows : UI Automation via le paquet uiautomation.

Tous les appels passent par un seul fil initialisé pour COM : les éléments lus restent valides pour le clic
qui suit, quel que soit le fil de l'outil (voix, ou serveur MCP pour Claude).
"""
from __future__ import annotations

import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from ..system.foreground import Foreground
from . import DesktopUnavailable, Element, Snapshot

ROLES = {
    "ButtonControl": "bouton", "SplitButtonControl": "bouton", "MenuItemControl": "menu", "HyperlinkControl": "lien",
    "TabItemControl": "onglet", "ListItemControl": "élément", "TreeItemControl": "élément",
    "DataItemControl": "élément", "CheckBoxControl": "case", "RadioButtonControl": "option",
    "ComboBoxControl": "liste", "EditControl": "champ",
}
_KEY_NAMES = {"ctrl": "Ctrl", "alt": "Alt", "shift": "Shift", "win": "Win", "enter": "Enter", "escape": "Esc",
              "tab": "Tab", "space": "Space", "delete": "Delete", "backspace": "Back", "up": "Up", "down": "Down",
              "left": "Left", "right": "Right", "home": "Home", "end": "End"}
_ACTIONS = (("invoke", "Invoke"), ("toggle", "Toggle"), ("selection_item", "Select"),
            ("expand_collapse", "Expand"), ("legacy", "DoDefaultAction"))
MAX_NODES, MAX_DEPTH, MAX_SECONDS = 3000, 40, 2.5


def typed_text(text: str) -> str:
    """Texte littéral pour SendKeys : « { » et « } » s'échappent, un retour à la ligne devient Entrée."""
    return "".join({"{": "{{}", "}": "{}}", "\n": "{Enter}", "\r": ""}.get(char, char) for char in text)


def keys_text(keys: list[str]) -> str:
    parts = []
    for key in keys:
        if key in _KEY_NAMES:
            parts.append("{" + _KEY_NAMES[key] + "}")
        elif key.startswith("f") and key[1:].isdigit():
            parts.append("{" + key.upper() + "}")
        else:
            parts.append(key)
    return "".join(parts)


def _pattern(control: Any, patterns: dict[str, Any], key: str) -> Any:
    pattern_id = patterns.get(key)
    if pattern_id is None:
        return None
    try:
        return control.GetPattern(pattern_id)
    except Exception:  # noqa: BLE001 - motif non pris en charge par ce contrôle
        return None


def _text(control: Any, kind: str, name: str, patterns: dict[str, Any], limit: int) -> str:
    if kind in ("DocumentControl", "EditControl"):
        if getattr(control, "IsPassword", False):
            return ""                                    # jamais le contenu d'un champ de mot de passe
        for key in ("text", "value"):
            pattern = _pattern(control, patterns, key)
            if not pattern:
                continue
            try:
                value = pattern.DocumentRange.GetText(limit) if key == "text" else pattern.Value
            except Exception:  # noqa: BLE001
                continue
            if isinstance(value, str) and value.strip():
                return value.strip()[:limit]
        return ""
    return name[:limit] if kind == "TextControl" else ""


def collect(root: Any, patterns: dict[str, Any], max_chars: int, max_items: int,
            clock: Callable[[], float] = time.monotonic) -> tuple[list[str], list[Element]]:
    """Parcours en profondeur, borné en nombre d'éléments et en temps : une application lourde ne bloque pas."""
    texts: list[str] = []
    elements: list[Element] = []
    seen: set[str] = set()
    size = nodes = 0
    deadline = clock() + MAX_SECONDS
    stack = [(root, 0)]
    while stack and nodes < MAX_NODES and clock() < deadline:
        control, depth = stack.pop()
        nodes += 1
        try:
            if control.IsOffscreen:
                continue
            kind = control.ControlTypeName
            name = (control.Name or "").strip()
        except Exception:  # noqa: BLE001 - élément disparu pendant la lecture
            continue
        if kind in ROLES and name and len(elements) < max_items:
            try:
                enabled = bool(control.IsEnabled)
            except Exception:  # noqa: BLE001
                enabled = False
            if enabled:
                elements.append(Element(ROLES[kind], name[:120], control))
        if size < max_chars:
            text = _text(control, kind, name, patterns, max_chars - size)
            if text and text not in seen:
                seen.add(text)
                texts.append(text)
                size += len(text) + 1
        if depth < MAX_DEPTH:
            try:
                children = control.GetChildren() or []
            except Exception:  # noqa: BLE001
                children = []
            stack.extend((child, depth + 1) for child in reversed(children))
    return texts, elements


def press(control: Any, patterns: dict[str, Any]) -> None:
    """Active l'élément par son motif UI Automation ; le clic souris n'est que le dernier recours."""
    for key, method in _ACTIONS:
        if pattern := _pattern(control, patterns, key):
            getattr(pattern, method)()
            return
    control.Click(simulateMove=False)


class WindowsBackend:
    def __init__(self) -> None:
        try:
            import uiautomation as auto
        except ImportError as exc:
            raise DesktopUnavailable("Le paquet uiautomation est absent : relance `uv sync`.") from exc
        self.auto = auto
        ids = auto.PatternId
        self.patterns = {
            "text": getattr(ids, "TextPattern", None), "value": getattr(ids, "ValuePattern", None),
            "invoke": getattr(ids, "InvokePattern", None), "toggle": getattr(ids, "TogglePattern", None),
            "selection_item": getattr(ids, "SelectionItemPattern", None),
            "expand_collapse": getattr(ids, "ExpandCollapsePattern", None),
            "legacy": getattr(ids, "LegacyIAccessiblePattern", None),
        }
        self._worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="uia", initializer=self._init_thread)

    def _init_thread(self) -> None:
        self._com = self.auto.UIAutomationInitializerInThread()     # gardé vivant : COM reste initialisé

    def _call(self, function: Callable[..., Any], *args: Any) -> Any:
        return self._worker.submit(function, *args).result(timeout=20)

    def _root(self, window: Foreground) -> Any:
        if not window.handle:
            raise DesktopUnavailable(f"Je ne retrouve pas la fenêtre de {window.app}.")
        root = self.auto.ControlFromHandle(window.handle)
        if root is None:
            raise DesktopUnavailable(f"La fenêtre de {window.app} est fermée.")
        return root

    def _activate(self, window: Foreground) -> None:
        root = self._root(window)
        activate = getattr(root, "SetActive", None)
        if activate is None or not activate():
            self.auto.SwitchToThisWindow(window.handle)
        time.sleep(0.15)

    def _snapshot(self, window: Foreground, max_chars: int, max_items: int) -> Snapshot:
        root = self._root(window)
        texts, elements = collect(root, self.patterns, max_chars, max_items)
        return Snapshot(window.app, (root.Name or window.title or "").strip(), texts, elements)

    def _type(self, window: Foreground, text: str, submit: bool) -> None:
        self._activate(window)
        self.auto.SendKeys(typed_text(text) + ("{Enter}" if submit else ""), interval=0.005, charMode=True)

    def _shortcut(self, window: Foreground, keys: list[str]) -> None:
        self._activate(window)
        self.auto.SendKeys(keys_text(keys), interval=0.02, charMode=False)

    def snapshot(self, window: Foreground, max_chars: int, max_items: int) -> Snapshot:
        return self._call(self._snapshot, window, max_chars, max_items)

    def press(self, window: Foreground, element: Element) -> None:
        self._call(press, element.native, self.patterns)

    def type_text(self, window: Foreground, text: str, submit: bool) -> None:
        self._call(self._type, window, text, submit)

    def shortcut(self, window: Foreground, keys: list[str]) -> None:
        self._call(self._shortcut, window, keys)
