"""Outils intégrés, macOS et Windows."""
from __future__ import annotations

from ..system import apps, folders, media, power, status, timers, volume, web
from . import N2, N3, tool

TIMERS = timers.Timers()
POWER = power.PowerScheduler()
POWER_DELAY_S = 20


@tool("open_app", "Ouvre une application installée sur l'ordinateur (Spotify, Discord, calculatrice…). "
      "Pas pour les sites web.", {"name": {"type": "string", "description": "nom de l'application"}}, ("name",))
def open_app(name: str) -> str:
    app = apps.find_app(name)
    if app is None:
        if url := web.resolve_site(name):
            web.open_url(url)
            return f"J'ouvre {name} dans le navigateur."
        return f"Je ne trouve pas d'application qui s'appelle {name}."
    apps.launch(app)
    return f"J'ouvre {app.name}."


@tool("open_website", "Ouvre un site web connu (YouTube, Netflix…) ou une adresse dans le navigateur.",
      {"site": {"type": "string", "description": "nom du site ou adresse"}}, ("site",))
def open_website(site: str) -> str:
    url = web.resolve_site(site)
    web.open_url(url or web.search_url(site))
    return f"J'ouvre {site}." if url else f"Je cherche {site} sur internet."


@tool("web_search", "Lance une recherche Google dans le navigateur.",
      {"query": {"type": "string", "description": "ce qu'il faut chercher"}}, ("query",))
def web_search(query: str) -> str:
    web.open_url(web.search_url(query))
    return f"Je cherche {query}."


@tool("open_folder", "Ouvre un dossier : bureau, documents, téléchargements, images, musique, vidéos, "
      "dossier personnel.", {"name": {"type": "string"}}, ("name",))
def open_folder(name: str) -> str:
    path = folders.folder_path(name)
    if path is None or not path.exists():
        return f"Je ne connais pas le dossier {name}."
    folders.open_path(path)
    return f"J'ouvre {folders.LABELS.get(name, name)}."


@tool("media", "Contrôle la musique en cours : play, pause, next (suivant) ou previous (précédent).",
      {"action": {"type": "string", "enum": list(media.ACTIONS)}}, ("action",))
def media_control(action: str) -> str:
    return media.control(action)


@tool("set_volume", "Règle le volume du son : level de 0 à 100, ou change relatif (ex. 10, -10), "
      "ou mute (true coupe, false rétablit).",
      {"level": {"type": "integer"}, "change": {"type": "integer"}, "mute": {"type": "boolean"}})
def set_volume(level: int | None = None, change: int | None = None, mute: bool | None = None) -> str:
    if level is None and change is None and mute is None:
        return "Tu veux le son plus fort ou moins fort ?"
    return volume.set_volume(level, change, mute)


@tool("set_timer", "Lance un minuteur de N secondes, avec une étiquette facultative.",
      {"seconds": {"type": "integer"}, "label": {"type": "string"}}, ("seconds",))
def set_timer(seconds: int, label: str = "") -> str:
    return TIMERS.start(seconds, label)


@tool("cancel_timer", "Annule les minuteurs en cours.")
def cancel_timer() -> str:
    return TIMERS.cancel_all()


@tool("system_status", "Donne l'état de l'ordinateur : batterie, processeur, mémoire.")
def system_status() -> str:
    return status.sentence()


@tool("close_app", "Ferme une application ouverte.", {"name": {"type": "string"}}, ("name",),
      level=N2, confirm=lambda a: f"Je ferme {a.get('name', 'cette application')} ?")
def close_app(name: str) -> str:
    return apps.quit_app(name)


@tool("lock_screen", "Verrouille l'écran de l'ordinateur.", level=N2, confirm=lambda a: "Je verrouille l'écran ?")
def lock_screen() -> str:
    power.execute("lock")
    return "Écran verrouillé."


@tool("power", "Éteint, redémarre ou met en veille l'ordinateur, après un délai annulable.",
      {"action": {"type": "string", "enum": ["shutdown", "restart", "sleep"]}}, ("action",), level=N3,
      confirm=lambda a: f"Tu confirmes {power.SPOKEN.get(a.get('action', ''), 'cette action')} de l'ordinateur ?")
def power_action(action: str) -> str:
    return POWER.schedule(action, POWER_DELAY_S)


@tool("cancel_power", "Annule une extinction, un redémarrage ou une mise en veille programmés.")
def cancel_power() -> str:
    return POWER.cancel()
