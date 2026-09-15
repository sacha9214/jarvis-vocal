"""Lecture et pilotage de l'application au premier plan, par l'accessibilité du système.

Windows : UI Automation, l'accès qu'utilisent les lecteurs d'écran (paquet uiautomation, en ctypes).
macOS : l'API d'accessibilité (AX) ; macOS demande l'autorisation « Accessibilité » pour le terminal ou
l'application qui lance Jarvis. Jarvis vise la dernière application utilisée avant lui, jamais sa fenêtre,
et active les éléments sans bouger la souris quand ils le permettent.
"""
from __future__ import annotations

import re
import threading
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Protocol

from ..system import IS_MAC, IS_WINDOWS
from ..system.foreground import Foreground, ForegroundTracker

MODIFIERS = ("ctrl", "alt", "shift", "win")
_KEY_WORDS = {
    "ctrl": "ctrl", "control": "ctrl", "controle": "ctrl", "cmd": "ctrl", "commande": "ctrl", "command": "ctrl",
    "alt": "alt", "option": "alt", "shift": "shift", "maj": "shift", "majuscule": "shift", "win": "win",
    "windows": "win", "entree": "enter", "enter": "enter", "echap": "escape", "echappe": "escape", "escape": "escape",
    "tab": "tab", "tabulation": "tab", "espace": "space", "space": "space", "suppr": "delete", "delete": "delete",
    "backspace": "backspace", "haut": "up", "bas": "down", "gauche": "left", "droite": "right", "debut": "home",
    "fin": "end",
}
# Bouton générique d'une boîte de dialogue : sensible si la fenêtre parle de supprimer, payer, envoyer…
_CONFIRMATION = re.compile(r"(?:oui|ok|d accord|continuer|confirmer|valider|accepter|terminer|yes|continue)")


class DesktopUnavailable(RuntimeError):
    """Application illisible : autorisation manquante, fenêtre fermée, système non pris en charge."""


@dataclass
class Element:
    role: str                 # bouton, menu, lien, onglet, case, option, liste, champ, élément
    name: str
    native: Any = None        # contrôle UI Automation ou élément AX


@dataclass
class Snapshot:
    app: str
    title: str
    texts: list[str] = field(default_factory=list)
    elements: list[Element] = field(default_factory=list)

    def render(self, max_chars: int) -> str:
        head = f"Application : {self.app}" + (f" — {self.title}" if self.title else "")
        text = "\n".join(self.texts)[:max_chars] or "(aucun texte lisible)"
        items = "\n".join(f"{index} : {element.role} « {element.name} »"
                          for index, element in enumerate(self.elements, 1)) or "(aucun)"
        return f"{head}\nTexte visible :\n{text}\nÉléments cliquables (numéro : type « nom ») :\n{items}"


class Backend(Protocol):
    def snapshot(self, window: Foreground, max_chars: int, max_items: int) -> Snapshot: ...
    def press(self, window: Foreground, element: Element) -> None: ...
    def type_text(self, window: Foreground, text: str, submit: bool) -> None: ...
    def shortcut(self, window: Foreground, keys: list[str]) -> None: ...


def soft(text: str) -> str:
    text = unicodedata.normalize("NFD", text.lower())
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def parse_keys(spoken: str) -> list[str] | None:
    """« contrôle shift T », « ctrl+s », « alt f4 » → [ctrl, shift, t] ; None si ce n'est pas un raccourci."""
    modifiers: set[str] = set()
    keys: list[str] = []
    for word in soft(spoken).split():
        if word in ("plus", "et", "la", "touche"):
            continue
        name = _KEY_WORDS.get(word)
        if name in MODIFIERS:
            modifiers.add(name)
        elif name or re.fullmatch(r"f(?:[1-9]|1[0-2])", word) or (len(word) == 1 and word.isalnum()):
            keys.append(name or word)
        else:
            return None
    if len(keys) != 1:
        return None
    return [m for m in MODIFIERS if m in modifiers] + keys


def best_match(elements: list[Element], query: str) -> Element | None:
    wanted = soft(query)
    words = [word for word in wanted.split() if len(word) > 2]
    best, best_score = None, 0.0
    for element in elements:
        name = soft(element.name)
        if not name:
            continue
        if name == wanted:
            return element
        if wanted and wanted in name:
            score = 100 - min(99, len(name) - len(wanted)) / 4
        else:
            score = 60 * sum(word in name for word in words) / max(1, len(words))
        if score > best_score:
            best, best_score = element, score
    return best if best_score >= 30 else None


def load_backend() -> Backend:
    if IS_WINDOWS:
        from .windows import WindowsBackend
        return WindowsBackend()
    if IS_MAC:
        from .mac import MacBackend
        return MacBackend()
    raise DesktopUnavailable("Le pilotage des applications n'est pris en charge que sous Windows et macOS.")


class DesktopController:
    def __init__(self, tracker: ForegroundTracker, backend: Backend | None = None, max_chars: int = 6000,
                 max_items: int = 60):
        self.tracker = tracker
        self.max_chars = max_chars
        self.max_items = max_items
        self._backend = backend
        self._lock = threading.Lock()
        self._last: tuple[Foreground, Snapshot] | None = None

    @property
    def backend(self) -> Backend:
        if self._backend is None:        # chargé au premier usage : uiautomation et AX sont longs à importer
            self._backend = load_backend()
        return self._backend

    def _window(self) -> Foreground:
        window = self.tracker.last()
        if window is None:
            raise DesktopUnavailable("Je ne sais pas encore quelle application tu utilises : clique dans sa fenêtre, "
                                     "puis redemande-moi.")
        return window

    def _snapshot(self, window: Foreground) -> Snapshot:
        snapshot = self.backend.snapshot(window, self.max_chars, self.max_items)
        with self._lock:
            self._last = (window, snapshot)
        return snapshot

    def read(self) -> str:
        return self._snapshot(self._window()).render(self.max_chars)

    def press(self, index: int | None = None, text: str = "", forbid: str = "") -> str:
        window = self._window()
        with self._lock:
            last = self._last
        if index and last is not None and last[0].app == window.app and 1 <= index <= len(last[1].elements):
            snapshot, element = last[1], last[1].elements[index - 1]      # numéro donné par la lecture précédente
        else:
            snapshot = self._snapshot(window)
            element = best_match(snapshot.elements, text) if text else None
        if element is None:
            return f"Je ne trouve pas {text} dans {window.app}." if text else "Dis-moi sur quoi cliquer."
        name = soft(element.name)
        if forbid and re.search(forbid, name):
            return f"{element.name} est une action sensible : fais-la toi-même."
        context = soft(" ".join([snapshot.title, *snapshot.texts]))
        if forbid and _CONFIRMATION.fullmatch(name) and re.search(forbid, context):
            return f"Cette fenêtre demande de confirmer une action sensible : clique toi-même sur {element.name}."
        try:
            self.backend.press(window, element)
        except DesktopUnavailable:
            raise
        except Exception:  # noqa: BLE001 - élément périmé (l'interface a bougé) : on le retrouve par son nom
            retry = best_match(self._snapshot(window).elements, element.name)
            if retry is None:
                return f"{element.name} n'est plus affiché dans {window.app}."
            self.backend.press(window, retry)
        with self._lock:
            self._last = None            # l'interface a changé : les numéros ne valent plus
        return f"Je clique sur {element.name}."

    def type(self, text: str, submit: bool = False) -> str:
        window = self._window()
        self.backend.type_text(window, text, submit)
        return f"C'est tapé dans {window.app}."

    def shortcut(self, keys: str) -> str:
        parsed = parse_keys(keys)
        if parsed is None:
            return f"Je ne comprends pas le raccourci {keys}."
        window = self._window()
        self.backend.shortcut(window, parsed)
        return f"J'ai appuyé sur {' '.join(parsed)} dans {window.app}."
