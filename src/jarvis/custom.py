"""Commandes personnalisées : tes phrases, tes actions, dans config.yaml.

    commands:
      - name: projet jarvis
        say: ["ouvre mon projet", "lance le projet jarvis"]
        run: code ~/Desktop/jarvis-vocal
      - name: note
        say: ["note *", "prends note de *"]
        run: echo "{text}" >> ~/Desktop/notes.txt
        reply: "C'est noté, {text}."
      - name: réunion
        say: ["lance la réunion"]
        open: https://meet.google.com/abc-defg-hij
      - name: capture
        say: ["fais une capture"]
        keys: ctrl+shift+4
        confirm: true

Une commande a une action parmi `run` (ligne de commande), `open` (adresse web, fichier ou dossier), `keys`
(raccourci clavier dans l'application au premier plan) ou `tool` (une action de Jarvis, avec `args`).
`*` dans une phrase capture ce qui est dit à cet endroit, disponible en `{text}`. `confirm: true` fait
demander confirmation (niveau N2), `speak_output: true` lit la sortie de la commande.
Les phrases sont reconnues sans LLM ; le modèle y a aussi accès comme outils `custom_<nom>`.
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
import threading
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .commands import Command, _aligned
from .config import Config
from .system import NO_WINDOW
from .tools import N1, N2, REGISTRY, Tool

LOG = logging.getLogger("jarvis.custom")
ACTIONS = ("run", "open", "keys", "tool")
_OUTPUT_TIMEOUT_S = 20.0
_MAX_SPOKEN = 300


@dataclass
class CustomCommand:
    name: str
    phrases: list[str]
    run: str = ""
    open: str = ""
    keys: str = ""
    tool: str = ""
    args: dict[str, Any] = field(default_factory=dict)
    reply: str = ""
    confirm: bool = False
    speak_output: bool = False

    @property
    def action(self) -> str:
        return next(kind for kind in ACTIONS if getattr(self, kind))

    @property
    def tool_name(self) -> str:
        return "custom_" + re.sub(r"[^a-z0-9]+", "_", _plain(self.name)).strip("_")

    @property
    def wildcard(self) -> bool:
        return any("*" in phrase for phrase in self.phrases)


def _plain(text: str) -> str:
    text = unicodedata.normalize("NFD", text.lower().replace("œ", "oe").replace("æ", "ae"))
    return "".join(c for c in text if unicodedata.category(c) != "Mn")


def load_commands(cfg: Config) -> list[CustomCommand]:
    """Lit et valide la section `commands` ; une erreur dit quelle commande et quoi corriger."""
    commands: list[CustomCommand] = []
    seen: set[str] = set()
    for index, raw in enumerate(cfg.commands or [], 1):
        if not isinstance(raw, dict):
            raise ValueError(f"commands[{index}] : chaque commande est une liste de clés (name, say, run…).")
        name = str(raw.get("name") or "").strip()
        if not name:
            raise ValueError(f"commands[{index}] : il manque `name`.")
        phrases = raw.get("say")
        if isinstance(phrases, str):
            phrases = [phrases]
        phrases = [str(p).strip() for p in (phrases or []) if str(p).strip()]
        if not phrases:
            raise ValueError(f"commande « {name} » : il faut au moins une phrase dans `say`.")
        actions = [kind for kind in ACTIONS if raw.get(kind)]
        if len(actions) != 1:
            raise ValueError(f"commande « {name} » : exactement une action parmi run, open, keys ou tool.")
        unknown = set(raw) - {"name", "say", "args", "reply", "confirm", "speak_output", *ACTIONS}
        if unknown:
            raise ValueError(f"commande « {name} » : clé inconnue {', '.join(sorted(unknown))}.")
        if raw.get("tool") and raw["tool"] not in REGISTRY:
            raise ValueError(f"commande « {name} » : l'action {raw['tool']} n'existe pas.")
        args = raw.get("args") or {}
        if not isinstance(args, dict):
            raise ValueError(f"commande « {name} » : `args` doit être une liste de clés.")
        command = CustomCommand(name, phrases, str(raw.get("run") or ""), str(raw.get("open") or ""),
                                str(raw.get("keys") or ""), str(raw.get("tool") or ""), dict(args),
                                str(raw.get("reply") or ""), bool(raw.get("confirm")), bool(raw.get("speak_output")))
        if command.tool_name in seen:
            raise ValueError(f"commande « {name} » : un autre nom donne le même identifiant {command.tool_name}.")
        seen.add(command.tool_name)
        commands.append(command)
    return commands


def _fill(template: str, text: str) -> str:
    return template.replace("{text}", text)


def _shell_quote(text: str) -> str:
    return text.replace('"', "").replace("`", "").replace("$", "").replace("\n", " ")


def execute(command: CustomCommand, text: str = "") -> str:
    """Exécute la commande ; renvoie la phrase à dire (au moins trois mots : la voix bafouille en dessous)."""
    text = text.strip()
    reply = _fill(command.reply, text) if command.reply else f"C'est fait pour {command.name}."
    if command.run:
        line = _fill(command.run, _shell_quote(text))
        if command.speak_output:
            try:
                completed = subprocess.run(line, shell=True, capture_output=True, text=True, encoding="utf-8",
                                           errors="replace", timeout=_OUTPUT_TIMEOUT_S, cwd=Path.home(),
                                           creationflags=NO_WINDOW)
            except subprocess.TimeoutExpired:
                return f"La commande {command.name} n'a pas fini en {int(_OUTPUT_TIMEOUT_S)} secondes."
            output = (completed.stdout or completed.stderr).strip()
            return re.sub(r"\s+", " ", output)[:_MAX_SPOKEN] if output else reply
        process = subprocess.Popen(line, shell=True, cwd=Path.home(), stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
        threading.Thread(target=process.wait, name=f"custom-{command.tool_name}", daemon=True).start()
        return reply
    if command.open:
        target = _fill(command.open, text)
        if re.match(r"https?://", target):
            from .system import web
            web.open_url(target)
        else:
            from .system import folders
            path = Path(os.path.expandvars(os.path.expanduser(target)))
            if not path.exists():
                return f"Je ne trouve pas {target} sur l'ordinateur."
            folders.open_path(path)
        return reply
    if command.keys:
        from .tools import builtin
        if builtin.DESKTOP is None:
            return "Le pilotage des applications n'est pas disponible."
        result = builtin.DESKTOP.shortcut(_fill(command.keys, text))
        return reply if command.reply else result
    tool = REGISTRY[command.tool]
    args = {key: _fill(value, text) if isinstance(value, str) else value for key, value in command.args.items()}
    if command.wildcard and "text" not in args and not command.args:
        args = {}
    result = tool.handler(**args)
    return reply if command.reply else result


def register(commands: list[CustomCommand]) -> None:
    """Chaque commande devient aussi un outil du modèle : il la retrouve quand la phrase diffère."""
    for command in commands:
        phrases = " ; ".join(f"« {p} »" for p in command.phrases[:4])
        properties = {"text": {"type": "string", "description": "ce qui remplace * dans la phrase"}} \
            if command.wildcard else {}
        handler = (lambda c: lambda text="": execute(c, text))(command)
        description = f"Commande personnalisée de l'utilisateur « {command.name} », dite par exemple {phrases}."
        REGISTRY[command.tool_name] = Tool(
            command.tool_name, description,
            {"type": "object", "properties": properties, "required": ["text"] if command.wildcard else []},
            N2 if command.confirm else N1, handler,
            confirm=(lambda c: lambda a: f"Je lance {c.name} ?")(command) if command.confirm else None)


class Matcher:
    """Reconnaît une phrase personnalisée sans LLM, accents et ponctuation ignorés."""

    def __init__(self, commands: list[CustomCommand]):
        self._rules: list[tuple[re.Pattern[str], CustomCommand]] = []
        for command in commands:
            for phrase in command.phrases:
                # « * » découpé avant la normalisation, qui effacerait l'étoile. Seul le premier capture :
                # les suivants acceptent n'importe quoi (une regex n'admet qu'un groupe nommé « text »).
                parts = [re.escape(_aligned(part)[0].strip()) for part in phrase.split("*")]
                pattern = parts[0]
                for number, part in enumerate(parts[1:]):
                    pattern += (r"\s*(?P<text>.+?)\s*" if number == 0 else r"\s*.+?\s*") + part
                self._rules.append((re.compile(rf"^{pattern}$"), command))
        self._rules.sort(key=lambda rule: -len(rule[0].pattern))     # la phrase la plus précise d'abord

    def __bool__(self) -> bool:
        return bool(self._rules)

    def match(self, text: str) -> Command | None:
        plain, soft = _aligned(text)
        plain, soft = plain.strip(), soft.strip()
        for pattern, command in self._rules:
            if match := pattern.match(plain):
                text_arg = soft[match.start("text"):match.end("text")].strip() if "text" in match.groupdict() else ""
                return Command(command.tool_name, {"text": text_arg} if command.wildcard else {})
        return None


def setup(cfg: Config) -> Matcher:
    commands = load_commands(cfg)
    register(commands)
    if commands:
        LOG.info("Commandes personnalisées : %s", ", ".join(c.name for c in commands))
    return Matcher(commands)
