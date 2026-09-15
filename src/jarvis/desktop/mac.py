"""macOS : l'API d'accessibilité (AX) de pyobjc.

Lire et cliquer demandent l'autorisation « Accessibilité » ; taper et les raccourcis passent par System Events,
que macOS fait autoriser au premier usage. « Contrôle » dit à voix haute devient Cmd : c'est l'intention
de quelqu'un qui dit « contrôle S » pour enregistrer.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from ..system import applescript_string, osascript
from ..system.foreground import Foreground
from . import DesktopUnavailable, Element, Snapshot

ROLES = {"AXButton": "bouton", "AXMenuButton": "menu", "AXMenuItem": "menu", "AXLink": "lien", "AXCheckBox": "case",
         "AXRadioButton": "option", "AXPopUpButton": "liste", "AXComboBox": "liste", "AXDisclosureTriangle": "bouton",
         "AXTab": "onglet", "AXTextField": "champ", "AXTextArea": "champ"}
TEXT_ROLES = {"AXStaticText", "AXTextArea", "AXTextField", "AXHeading"}
PERMISSION_HINT = ("macOS n'autorise pas encore Jarvis à lire les applications : Réglages Système › Confidentialité "
                   "et sécurité › Accessibilité, active ton terminal (ou l'application qui lance Jarvis).")
_MODIFIERS = {"ctrl": "command down", "alt": "option down", "shift": "shift down", "win": "control down"}
_KEY_CODES = {"enter": 36, "tab": 48, "space": 49, "backspace": 51, "escape": 53, "delete": 117, "left": 123,
              "right": 124, "down": 125, "up": 126, "home": 115, "end": 119, "f1": 122, "f2": 120, "f3": 99,
              "f4": 118, "f5": 96, "f6": 97, "f7": 98, "f8": 100, "f9": 101, "f10": 109, "f11": 103, "f12": 111}
MAX_NODES, MAX_DEPTH, MAX_SECONDS = 3000, 40, 2.5

Attribute = Callable[[Any, str], Any]


def collect(root: Any, attribute: Attribute, max_chars: int, max_items: int,
            clock: Callable[[], float] = time.monotonic) -> tuple[list[str], list[Element]]:
    texts: list[str] = []
    elements: list[Element] = []
    seen: set[str] = set()
    size = nodes = 0
    deadline = clock() + MAX_SECONDS
    stack = [(root, 0)]
    while stack and nodes < MAX_NODES and clock() < deadline:
        element, depth = stack.pop()
        nodes += 1
        role = attribute(element, "AXRole")
        label = next((v.strip() for v in (attribute(element, "AXTitle"), attribute(element, "AXDescription"))
                      if isinstance(v, str) and v.strip()), "")
        if role in ROLES and label and len(elements) < max_items and attribute(element, "AXEnabled") is not False:
            elements.append(Element(ROLES[role], label[:120], element))
        if role in TEXT_ROLES and attribute(element, "AXSubrole") != "AXSecureTextField" and size < max_chars:
            value = attribute(element, "AXValue")
            text = (value if isinstance(value, str) else "").strip() or (label if role != "AXTextField" else "")
            if text and text not in seen:
                seen.add(text)
                texts.append(text[:max_chars - size])
                size += len(text) + 1
        if depth < MAX_DEPTH:
            children = attribute(element, "AXChildren") or []
            stack.extend((child, depth + 1) for child in reversed(list(children)))
    return texts, elements


def shortcut_script(keys: list[str]) -> str:
    modifiers = [_MODIFIERS[key] for key in keys if key in _MODIFIERS]
    key = next(key for key in keys if key not in _MODIFIERS)
    stroke = f"key code {_KEY_CODES[key]}" if key in _KEY_CODES else f"keystroke {applescript_string(key)}"
    using = f" using {{{', '.join(modifiers)}}}" if modifiers else ""
    return f'tell application "System Events" to {stroke}{using}'


class MacBackend:
    def __init__(self) -> None:
        import ApplicationServices

        self.ax = ApplicationServices

    def _attribute(self, element: Any, name: str) -> Any:
        error, value = self.ax.AXUIElementCopyAttributeValue(element, name, None)
        return value if error == 0 else None

    def _check(self) -> None:
        if not self.ax.AXIsProcessTrusted():
            raise DesktopUnavailable(PERMISSION_HINT)

    def _app_and_window(self, window: Foreground) -> tuple[Any, Any]:
        if not window.pid:
            raise DesktopUnavailable(f"Je ne retrouve pas {window.app}.")
        app = self.ax.AXUIElementCreateApplication(window.pid)
        self.ax.AXUIElementSetMessagingTimeout(app, 1.0)
        focused = self._attribute(app, "AXFocusedWindow") or self._attribute(app, "AXMainWindow")
        if focused is None:
            windows = self._attribute(app, "AXWindows") or []
            focused = windows[0] if windows else None
        if focused is None:
            raise DesktopUnavailable(f"{window.app} n'a pas de fenêtre lisible.")
        return app, focused

    def _activate(self, window: Foreground) -> None:
        osascript('tell application "System Events" to set frontmost of '
                  f"(first process whose unix id is {int(window.pid or 0)}) to true")
        time.sleep(0.2)

    def snapshot(self, window: Foreground, max_chars: int, max_items: int) -> Snapshot:
        self._check()
        _, root = self._app_and_window(window)
        texts, elements = collect(root, self._attribute, max_chars, max_items)
        title = self._attribute(root, "AXTitle")
        return Snapshot(window.app, title if isinstance(title, str) else window.title, texts, elements)

    def press(self, window: Foreground, element: Element) -> None:
        self._check()
        error, names = self.ax.AXUIElementCopyActionNames(element.native, None)
        available = list(names or []) if error == 0 else []
        action = next((name for name in ("AXPress", "AXPick", "AXConfirm", "AXShowMenu") if name in available), None)
        if action is None:
            raise DesktopUnavailable(f"{element.name} ne se clique pas.")
        if self.ax.AXUIElementPerformAction(element.native, action) != 0:
            raise RuntimeError(f"{element.name} n'a pas répondu")

    def type_text(self, window: Foreground, text: str, submit: bool) -> None:
        self._check()
        app, _ = self._app_and_window(window)
        self._activate(window)
        focused = self._attribute(app, "AXFocusedUIElement")
        if focused is None or self.ax.AXUIElementSetAttributeValue(focused, "AXSelectedText", text) != 0:
            osascript(f'tell application "System Events" to keystroke {applescript_string(text)}')
        if submit:
            osascript('tell application "System Events" to key code 36')

    def shortcut(self, window: Foreground, keys: list[str]) -> None:
        self._activate(window)
        osascript(shortcut_script(keys))
