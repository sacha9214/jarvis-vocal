"""Safari (macOS) : les mêmes actions que l'extension, injectées par AppleScript.

Une extension Safari exige Xcode ; AppleScript non. Réglage à activer une fois dans Safari :
Réglages › Avancés › « Afficher les fonctionnalités pour les développeurs web », puis menu
Développement › « Autoriser JavaScript depuis les Apple Events ».
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from ..system import applescript_string, osascript

ACTIONS_JS = Path(__file__).parent / "extension" / "actions.js"
SETUP_HINT = ("Dans Safari, active Réglages › Avancés › « Afficher les fonctionnalités pour les développeurs web », "
              "puis Développement › « Autoriser JavaScript depuis les Apple Events ».")


@lru_cache(maxsize=1)
def _actions_source() -> str:
    return ACTIONS_JS.read_text(encoding="utf-8").replace("globalThis.JarvisActions =", "const JarvisActions =", 1)


def script(action: str, params: dict[str, Any]) -> str:
    return (f"(function () {{\n{_actions_source()}\n"
            f"return JSON.stringify(JarvisActions[{json.dumps(action)}]({json.dumps(params)}));\n}})()")


def _tell(body: str) -> str:
    try:
        return osascript(f'tell application "Safari"\n{body}\nend tell', timeout=10)
    except RuntimeError as exc:
        if "JavaScript" in str(exc) or "Apple Events" in str(exc):
            raise RuntimeError(SETUP_HINT) from exc
        raise


def run(action: str, params: dict[str, Any]) -> dict[str, Any]:
    if action == "navigate":
        if params.get("url"):
            _tell(f"set URL of current tab of front window to {applescript_string(params['url'])}")
        else:
            js = {"back": "history.back()", "forward": "history.forward()"}.get(params.get("direction", ""),
                                                                            "location.reload()")
            _tell(f"do JavaScript {applescript_string(js)} in current tab of front window")
        return {"done": True}
    if action == "tabs":
        return _tabs(params)
    if action == "status":
        title = _tell("return name of current tab of front window")
        url = _tell("return URL of current tab of front window")
        return {"title": title, "url": url, "browser": "Safari"}
    output = _tell(f"do JavaScript {applescript_string(script(action, params))} in current tab of front window")
    result = json.loads(output) if output else {}
    if action in ("page", "media"):
        result.setdefault("title", _tell("return name of current tab of front window"))
    if action == "page":
        result.setdefault("url", _tell("return URL of current tab of front window"))
    return result


def _tabs(params: dict[str, Any]) -> dict[str, Any]:
    action = params.get("action")
    if action == "switch":
        index = params.get("index")
        if index:
            _tell(f"set current tab of front window to tab {int(index)} of front window")
        else:
            _tell("set current tab of front window to tab ((index of current tab of front window) mod "
                  "(count of tabs of front window) + 1) of front window")
        return {"title": _tell("return name of current tab of front window")}
    if action == "close":
        title = _tell("return name of current tab of front window")
        _tell("close current tab of front window")
        return {"title": title}
    if action == "new":
        url = params.get("url") or "about:blank"
        _tell("tell front window to set current tab to "
              f"(make new tab with properties {{URL:{applescript_string(url)}}})")
        return {"title": "Nouvel onglet"}
    titles = _tell("set AppleScript's text item delimiters to linefeed\n"
                   "return (name of every tab of front window) as text")
    return {"tabs": [{"index": i, "title": t} for i, t in enumerate(titles.splitlines(), 1)]}
