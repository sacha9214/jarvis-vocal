"""Outils intégrés, macOS et Windows."""
from __future__ import annotations

from ..memory import Memory
from ..system import (
    apps,
    calc,
    capture,
    clipboard,
    files,
    folders,
    gui,
    media,
    power,
    settings,
    status,
    timers,
    volume,
    web,
)
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
    # Dans le navigateur, « mets pause » vise la vidéo qu'on regarde, pas Spotify.
    if BROWSER is not None and BROWSER.in_browser():
        try:
            result = BROWSER.call("media", {"action": action})
            if result.get("found"):
                return result.get("error") or _media_sentence(action, None, result)
        except Exception:  # noqa: BLE001 - extension absente : on retombe sur le lecteur système
            pass
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


@tool("system_status", "Donne l'état de l'ordinateur : batterie, processeur, mémoire, et l'extinction "
      "ou le redémarrage programmés s'il y en a un.")
def system_status() -> str:
    sentence = status.sentence()
    if POWER.pending() is not None:
        sentence += " " + POWER.status()
    return sentence


@tool("close_app", "Ferme une application ouverte.", {"name": {"type": "string"}}, ("name",),
      level=N2, confirm=lambda a: f"Je ferme {a.get('name', 'cette application')} ?")
def close_app(name: str) -> str:
    return apps.quit_app(name)


@tool("lock_screen", "Verrouille l'écran de l'ordinateur.", level=N2, confirm=lambda a: "Je verrouille l'écran ?")
def lock_screen() -> str:
    power.execute("lock")
    return "J'ai verrouillé l'écran."


def _power_question(arguments: dict) -> str:
    what = power.SPOKEN.get(arguments.get("action", ""), "cette action")
    seconds = arguments.get("seconds")
    when = f" dans {timers.spoken_duration(seconds)}" if seconds else ""
    return f"Tu confirmes {what} de l'ordinateur{when} ?"


@tool("power", "Éteint, redémarre ou met en veille l'ordinateur. seconds : dans combien de temps "
      "(ex. 1500 pour vingt-cinq minutes) ; sans rien, tout de suite après un court délai pour annuler.",
      {"action": {"type": "string", "enum": ["shutdown", "restart", "sleep"]},
       "seconds": {"type": "integer", "description": "délai avant l'action, en secondes"}},
      ("action",), level=N3, confirm=_power_question)
def power_action(action: str, seconds: int | None = None) -> str:
    return POWER.schedule(action, seconds if seconds and seconds > 0 else POWER_DELAY_S)


@tool("cancel_power", "Annule une extinction, un redémarrage ou une mise en veille programmés.")
def cancel_power() -> str:
    return POWER.cancel()


SCREEN = None   # ScreenWatcher branché au démarrage (jarvis.app)


@tool("describe_screen", "Regarde l'écran de l'utilisateur maintenant : répond à une question sur ce qui est "
      "affiché (erreur, page, document, code) ou décrit ce qu'il est en train de faire.",
      {"question": {"type": "string", "description": "la question de l'utilisateur sur son écran"}})
def describe_screen(question: str = "") -> str:
    if SCREEN is None or not SCREEN.cfg.enabled:
        return "L'analyse d'écran est désactivée dans les réglages."
    return SCREEN.look(question) or "Je n'arrive pas à voir l'écran pour le moment."


# -- review de code (projet ouvert dans l'éditeur)

REVIEW = None   # ReviewManager branché au démarrage (jarvis.app)


@tool("review_code", "Lance en arrière-plan la review du code ouvert dans l'éditeur (VS Code, Cursor…) : scope "
      "project (tout le projet), changes (changements non commités) ou file (fichier affiché) ; target : nom du "
      "projet s'il est cité ; engine : claude ou local seulement si c'est demandé.",
      {"scope": {"type": "string", "enum": ["project", "changes", "file"]}, "target": {"type": "string"},
       "engine": {"type": "string", "enum": ["auto", "claude", "local"]}})
def review_code(scope: str = "project", target: str = "", engine: str = "auto") -> str:
    if REVIEW is None:
        return "La review de code n'est pas disponible."
    return REVIEW.start(scope, target, engine)


@tool("review_control", "Review de code en cours : status (où elle en est) ou cancel (l'arrêter).",
      {"action": {"type": "string", "enum": ["status", "cancel"]}}, ("action",))
def review_control(action: str) -> str:
    if REVIEW is None:
        return "Aucune review en cours."
    return REVIEW.cancel() if action == "cancel" else REVIEW.status()


# -- toute application (UI Automation sous Windows, accessibilité sous macOS)

DESKTOP = None   # DesktopController branché au démarrage (jarvis.app)


def _desktop_call(action) -> str:
    if DESKTOP is None:
        return "Le pilotage des applications n'est pas disponible."
    try:
        return action(DESKTOP)
    except Exception as exc:  # noqa: BLE001 - autorisation manquante, fenêtre fermée : dit tel quel
        return str(exc)


@tool("app_read", "Lit l'application au premier plan (Word, Outlook, Discord, Explorateur, Réglages, VS Code…) : "
      "titre, texte visible et liste numérotée des boutons, menus, onglets et champs. À utiliser pour résumer, "
      "relire ou expliquer ce qui est affiché, et avant app_press.", context="app,code", speaks=False)
def app_read() -> str:
    if EDITOR is not None and EDITOR.linked():      # VS Code : l'accessibilité ne voit rien, l'extension si
        return code_read("file")
    return _desktop_call(lambda desktop: desktop.read())


@tool("app_press", "Clique un bouton, un menu, un onglet ou un lien de l'application au premier plan : index "
      "(numéro donné par app_read) ou text (son nom).", {"index": {"type": "integer"}, "text": {"type": "string"}},
      context="app,code")
def app_press(index: int | None = None, text: str = "") -> str:
    return _desktop_call(lambda desktop: desktop.press(index, text, forbid=RISKY))


@tool("app_type", "Tape du texte dans le champ actif de l'application au premier plan, et valide si submit est vrai.",
      {"text": {"type": "string"}, "submit": {"type": "boolean"}}, ("text",), level=N2,
      confirm=lambda a: f"Je tape « {str(a.get('text', ''))[:80]} » ?", context="app,code")
def app_type(text: str, submit: bool = False) -> str:
    return _desktop_call(lambda desktop: desktop.type(text, submit))


@tool("app_shortcut", "Envoie un raccourci clavier à l'application au premier plan, ex. ctrl+s, ctrl+shift+t, alt+f4.",
      {"keys": {"type": "string"}}, ("keys",), level=N2,
      confirm=lambda a: f"J'appuie sur {a.get('keys', 'ce raccourci')} ?", context="app,code")
def app_shortcut(keys: str) -> str:
    return _desktop_call(lambda desktop: desktop.shortcut(keys))


# -- navigateur (extension Jarvis, ou Safari sur macOS)

BROWSER = None   # BrowserController branché au démarrage (jarvis.app)
RISKY = (r"\b(?:acheter|achat|payer|paiement|commander|valider la commande|supprimer|effacer|envoyer|publier"
         r"|confirmer|s abonner|abonnement|checkout|buy|pay|delete|send|subscribe|order)\b")
MEDIA_ACTIONS = ["play", "pause", "toggle", "volume", "volume_up", "volume_down", "mute", "unmute",
                 "forward", "back", "speed", "next"]


def _browser():
    if BROWSER is None:
        raise RuntimeError("le pilotage du navigateur est désactivé")
    return BROWSER


def _media_sentence(action: str, value: float | None, result: dict) -> str:
    seconds = int(value or 10)
    return {
        "play": "Je relance la vidéo.", "pause": "Je mets la vidéo en pause.", "toggle": "Voilà, c'est fait.",
        "volume": f"Volume de la vidéo à {result.get('volume')} pour cent.",
        "volume_up": f"Volume de la vidéo à {result.get('volume')} pour cent.",
        "volume_down": f"Volume de la vidéo à {result.get('volume')} pour cent.",
        "mute": "Je coupe le son de la vidéo.", "unmute": "Son de la vidéo rétabli.",
        "forward": f"J'avance de {seconds} secondes.", "back": f"Je recule de {seconds} secondes.",
        "speed": f"Vitesse {result.get('speed')}.", "next": "Je passe à la vidéo suivante.",
    }.get(action, "Voilà, c'est fait.")


@tool("browser_media", "Contrôle la vidéo ou la musique de l'onglet actif du navigateur (YouTube, Netflix…) : "
      "play, pause, volume (value de 0 à 100), volume_up, volume_down, mute, unmute, forward ou back (value en "
      "secondes), speed (value, ex. 1.5), next.",
      {"action": {"type": "string", "enum": MEDIA_ACTIONS}, "value": {"type": "number"}}, ("action",),
      context="browser")
def browser_media(action: str, value: float | None = None) -> str:
    result = _browser().call("media", {"action": action, "value": value})
    if not result.get("found"):
        return "Je ne trouve pas de vidéo dans l'onglet actif."
    return result.get("error") or _media_sentence(action, value, result)


@tool("browser_read", "Lit l'onglet actif du navigateur : titre, adresse, texte et liste numérotée des vidéos, "
      "liens, boutons et champs de saisie. À utiliser avant browser_open ou browser_type, ou pour résumer "
      "la page.",
      context="browser", speaks=False)
def browser_read() -> str:
    page = _browser().call("page", {"max_chars": 3500, "max_items": 30})
    lines = [f"Page : {page.get('title')} ({page.get('url')})"]
    if items := page.get("items"):
        lines.append("Éléments cliquables :")
        lines += [f"{item['index']}. [{item['kind']}] {item['text']}"
                  + (f" (contient : {item['value']})" if item.get("value") else "") for item in items]
    if page.get("selection"):
        lines.append(f"Texte sélectionné : {page['selection']}")
    lines.append("Texte de la page :\n" + (page.get("text") or ""))
    return "\n".join(lines)


@tool("browser_open", "Ouvre ou clique un élément de la page active : index (numéro donné par browser_read), "
      "text (texte du lien ou du bouton), ou video (n-ième vidéo de la page, ex. 2 pour la deuxième).",
      {"index": {"type": "integer"}, "text": {"type": "string"}, "video": {"type": "integer"}}, context="browser")
def browser_open(index: int | None = None, text: str = "", video: int | None = None) -> str:
    browser = _browser()
    if video:
        items = browser.call("page", {"max_chars": 0, "max_items": 80}).get("items") or []
        videos = [item for item in items if item["kind"] == "vidéo"]
        if len(videos) < video:
            return "Je ne trouve pas autant de vidéos sur cette page."
        index = videos[video - 1]["index"]
    elif index:
        browser.call("page", {"max_chars": 0, "max_items": 80})     # numérotation à jour
    if not index and not text:
        return "Dis-moi quoi ouvrir sur la page."
    result = browser.call("click", {"index": index, "text": text, "forbid": RISKY})
    if not result.get("clicked"):
        return result.get("error") or "Je n'ai pas pu cliquer."
    label = (result.get("text") or "l'élément")[:90]
    return f"J'ouvre {label}." if result.get("kind") == "lien" else f"Je clique sur {label}."


@tool("browser_scroll", "Fait défiler la page active : down, up, top ou bottom.",
      {"direction": {"type": "string", "enum": ["down", "up", "top", "bottom"]}}, ("direction",), context="browser")
def browser_scroll(direction: str) -> str:
    _browser().call("scroll", {"direction": direction})
    return {"down": "Je descends la page.", "up": "Je remonte la page.", "top": "Je vais en haut de la page.",
            "bottom": "Je vais en bas de la page."}.get(direction, "Voilà, c'est fait.")


@tool("browser_navigate", "Dans l'onglet actif : direction back (page précédente), forward ou reload, ou url à ouvrir.",
      {"direction": {"type": "string", "enum": ["back", "forward", "reload"]}, "url": {"type": "string"}},
      context="browser")
def browser_navigate(direction: str = "", url: str = "") -> str:
    if url:
        target = web.resolve_site(url) or (url if url.startswith("http") else web.search_url(url))
        _browser().call("navigate", {"url": target})
        return f"J'ouvre {url}."
    _browser().call("navigate", {"direction": direction or "reload"})
    return {"back": "Je reviens à la page précédente.", "forward": "Je passe à la page suivante."}.get(
        direction, "Je recharge la page.")


@tool("browser_search", "Lance une recherche dans l'onglet actif : sur YouTube (site youtube) ou sur Google.",
      {"query": {"type": "string"}, "site": {"type": "string", "enum": ["youtube", "google"]}}, ("query",),
      context="browser")
def browser_search(query: str, site: str = "google") -> str:
    from urllib.parse import quote_plus
    url = (f"https://www.youtube.com/results?search_query={quote_plus(query)}" if site == "youtube"
           else web.search_url(query))
    _browser().call("navigate", {"url": url})
    return f"Je cherche {query} sur YouTube." if site == "youtube" else f"Je cherche {query}."


@tool("browser_tabs", "Onglets du navigateur : list (les lister), switch (index, ou onglet suivant sans index), "
      "new (url facultative).",
      {"action": {"type": "string", "enum": ["list", "switch", "new"]}, "index": {"type": "integer"},
       "url": {"type": "string"}}, ("action",), context="browser")
def browser_tabs(action: str, index: int | None = None, url: str = "") -> str:
    result = _browser().call("tabs", {"action": action, "index": index, "url": url})
    if action == "list":
        tabs = result.get("tabs") or []
        return "Onglets ouverts : " + " ; ".join(f"{t['index']}, {t['title']}" for t in tabs[:12]) + "."
    return f"Onglet : {result.get('title')}." if action == "switch" else "Nouvel onglet ouvert."


@tool("browser_close_tab", "Ferme l'onglet actif du navigateur.", level=N2,
      confirm=lambda a: "Je ferme cet onglet ?", context="browser")
def browser_close_tab() -> str:
    result = _browser().call("tabs", {"action": "close"})
    return f"J'ai fermé {result.get('title') or 'l onglet'}."


@tool("browser_type", "Écrit du texte dans un champ de la page : field (nom du champ, ex. Description, "
      "Rechercher) ou index (numéro d'un champ donné par browser_read) ; sans les deux, le champ déjà "
      "sélectionné. replace vrai remplace ce que le champ contient, sinon le texte s'ajoute à la suite. "
      "submit vrai valide (touche Entrée).",
      {"text": {"type": "string"}, "field": {"type": "string"}, "index": {"type": "integer"},
       "replace": {"type": "boolean"}, "submit": {"type": "boolean"}}, ("text",), level=N2,
      confirm=lambda a: f"J'écris « {str(a.get('text', ''))[:60]} »"
      + (f" dans le champ {a['field']}" if a.get("field") else " dans la page") + " ?", context="browser")
def browser_type(text: str, field: str = "", index: int | None = None, replace: bool = False,
                 submit: bool = False) -> str:
    result = _browser().call("type", {"text": text, "field": field, "index": index, "replace": replace,
                                      "submit": submit})
    if not result.get("typed"):
        return result.get("error") or "Je n'ai pas pu écrire dans la page."
    where = result.get("field") or field
    return f"C'est écrit dans le champ {where}." if where else "Voilà, c'est écrit dans la page."


# -- éditeur de code (extension VS Code de Jarvis)

EDITOR = None   # EditorController branché au démarrage (jarvis.app)
CODE_COMMANDS = ["save", "save_all", "format", "organize_imports", "comment", "undo", "redo", "select_all", "find",
                 "replace", "rename", "quick_fix", "definition", "references", "symbol", "back", "forward",
                 "next_error", "previous_error", "fold", "unfold", "word_wrap", "terminal", "clear_terminal",
                 "problems", "explorer", "source_control", "search_view", "sidebar", "zen", "palette", "quick_open",
                 "split", "new_file", "close_tab", "next_tab", "previous_tab", "reopen_tab"]
_CODE_SENTENCES = {
    "save": "J'ai enregistré le fichier.", "save_all": "J'ai tout enregistré.", "format": "J'ai formaté le fichier.",
    "organize_imports": "J'ai rangé les imports.", "comment": "J'ai commenté la ligne.",
    "undo": "J'ai annulé la dernière modification.", "redo": "J'ai rétabli la modification.",
    "terminal": "Voilà le terminal.", "problems": "Voici les problèmes.", "close_tab": "J'ai fermé l'onglet.",
    "next_tab": "Je passe à l'onglet suivant.", "previous_tab": "Je reviens à l'onglet précédent.",
    "reopen_tab": "J'ai rouvert l'onglet.", "definition": "Je vais à la définition.",
    "back": "Je reviens en arrière.", "next_error": "Je passe à l'erreur suivante.",
    "previous_error": "Je reviens à l'erreur précédente.", "zen": "Je passe en mode zen.",
    "split": "J'ai divisé l'éditeur.",
}


def _editor():
    if EDITOR is None or not EDITOR.linked():
        raise RuntimeError("aucun éditeur n'est relié à Jarvis : installe l'extension VS Code avec `jarvis code`")
    return EDITOR


@tool("code_read", "Lit l'éditeur de code (VS Code) : what = file (le fichier ouvert autour du curseur, lignes "
      "numérotées), selection (le texte sélectionné) ou tabs (les fichiers ouverts). À utiliser pour expliquer, "
      "résumer ou corriger le code affiché.", {"what": {"type": "string", "enum": ["file", "selection", "tabs"]}},
      context="code", speaks=False)
def code_read(what: str = "file") -> str:
    result = _editor().call("read", {"what": what})
    if what == "tabs":
        tabs = result.get("tabs") or []
        return "Fichiers ouverts :\n" + "\n".join(
            f"{t['index']}. {t['label']}{' (actif)' if t.get('active') else ''}{' (modifié)' if t.get('dirty') else ''}"
            for t in tabs) if tabs else "Aucun fichier ouvert dans l'éditeur."
    if what == "selection":
        if not result.get("text"):
            return f"Rien n'est sélectionné dans {result.get('name')}."
        return f"Sélection dans {result.get('name')}, lignes {result['from']} à {result['to']} :\n{result['text']}"
    window = ("" if result.get("from") == 1 and result.get("to") == result.get("total_lines")
              else f", lignes {result.get('from')} à {result.get('to')} sur {result.get('total_lines')}")
    lines = [f"Fichier : {result.get('name')} ({result.get('language')}), curseur ligne {result.get('line')}{window}"]
    if result.get("selection"):
        lines.append(f"Texte sélectionné : {result['selection']}")
    lines.append(result.get("text") or "(fichier vide)")
    return "\n".join(lines)


@tool("code_errors", "Erreurs et avertissements affichés par l'éditeur (linter, compilateur) : scope file (le "
      "fichier ouvert) ou project (tous les fichiers).", {"scope": {"type": "string", "enum": ["file", "project"]}},
      context="code", speaks=False)
def code_errors(scope: str = "file") -> str:
    result = _editor().call("errors", {"scope": scope})
    items = result.get("items") or []
    if not items:
        where = "dans le fichier ouvert" if scope == "file" else "dans le projet"
        return f"Aucune erreur ni avertissement {where}."
    head = f"{result.get('errors', 0)} erreur(s), {result.get('warnings', 0)} avertissement(s) :"
    return head + "\n" + "\n".join(
        f"- {item['file']}:{item['line']} [{item['severity']}{' ' + item['source'] if item.get('source') else ''}] "
        f"{item['message']}" for item in items)


@tool("code_open", "Dans l'éditeur : ouvre un fichier du projet par son nom (file, ex. pipeline.py), va à une "
      "ligne (line) du fichier ouvert, ou passe à un onglet (tab, numéro donné par code_read tabs).",
      {"file": {"type": "string"}, "line": {"type": "integer"}, "tab": {"type": "integer"}}, context="code")
def code_open(file: str = "", line: int | None = None, tab: int | None = None) -> str:
    if not file and not line and not tab:
        return "Dis-moi quel fichier ouvrir, ou quelle ligne."
    result = _editor().call("open", {"file": file, "line": line, "tab": tab})
    if file or tab:
        return f"J'ouvre {result.get('name')}" + (f", ligne {result.get('line')}." if line else ".")
    return f"Ligne {result.get('line')}."


@tool("code_command", "Action dans l'éditeur de code (enregistrer, formater, commenter, terminal, problèmes, "
      "onglets, navigation…) : action = son nom.",
      {"action": {"type": "string", "enum": CODE_COMMANDS}}, ("action",), context="code")
def code_command(action: str) -> str:
    _editor().call("command", {"action": action})
    return _CODE_SENTENCES.get(action, "Voilà, c'est fait.")


@tool("code_search", "Cherche un texte dans tous les fichiers du projet (panneau de recherche de l'éditeur).",
      {"query": {"type": "string"}}, ("query",), context="code")
def code_search(query: str) -> str:
    _editor().call("search", {"query": query})
    return f"Je cherche {query} dans le projet."


@tool("code_insert", "Écrit du texte dans le fichier ouvert : mode cursor (au curseur), replace (à la place de la "
      "sélection) ou line (nouvelle ligne sous le curseur).",
      {"text": {"type": "string"}, "mode": {"type": "string", "enum": ["cursor", "replace", "line"]}}, ("text",),
      level=N2, confirm=lambda a: f"J'écris « {str(a.get('text', ''))[:80]} » dans le fichier ?", context="code")
def code_insert(text: str, mode: str = "cursor") -> str:
    result = _editor().call("insert", {"text": text, "mode": mode})
    return f"C'est écrit dans {result.get('name')}."


@tool("code_run", "Dans l'éditeur : lance le programme (run), le débogueur (debug) ou les tests (test).",
      {"mode": {"type": "string", "enum": ["run", "debug", "test"]}}, ("mode",), level=N2,
      confirm=lambda a: {"debug": "Je lance le débogueur ?", "test": "Je lance les tests ?"}.get(
          a.get("mode", ""), "Je lance le programme ?"), context="code")
def code_run(mode: str) -> str:
    _editor().call("run", {"mode": mode})
    return {"debug": "Je lance le débogueur.", "test": "Je lance les tests."}.get(mode, "Je lance le programme.")


# -- fichiers, fenêtres, presse-papiers, réglages : des outils groupés, pour ne pas gonfler le prompt

FILE_ACTIONS = ["find", "open", "reveal", "new_folder", "disk", "trash"]
_FOUND: list = []       # derniers fichiers trouvés, pour « ouvre le deuxième »


def _say_files(found: list) -> str:
    lines = [f"{index}. {item.name} (dans {item.path.parent.name})" for index, item in enumerate(found, 1)]
    return f"J'ai trouvé {len(found)} fichier" + ("s" if len(found) > 1 else "") + " :\n" + "\n".join(lines)


@tool("files", "Fichiers de l'ordinateur : find (chercher par nom, query ; kind facultatif document, image "
      "ou tableur), open (ouvrir : query, ou index d'un résultat précédent), reveal (montrer dans le "
      "Finder ou l'Explorateur), new_folder (créer un dossier, query = son nom), disk (place libre), "
      "trash (mettre à la corbeille, récupérable).",
      {"action": {"type": "string", "enum": FILE_ACTIONS}, "query": {"type": "string"},
       "index": {"type": "integer", "description": "numéro d'un fichier déjà trouvé"},
       "kind": {"type": "string", "enum": ["document", "image", "tableur"]}}, ("action",))
def files_tool(action: str, query: str = "", index: int | None = None, kind: str = "") -> str:
    global _FOUND
    if action == "disk":
        free, total = files.disk_usage()
        return f"Il reste {free:.0f} giga-octets libres sur {total:.0f}."
    if action == "new_folder":
        if not query:
            return "Dis-moi comment appeler le dossier."
        folder = files.new_folder(query)
        folders.open_path(folder)
        return f"J'ai créé le dossier {folder.name} sur le bureau."
    if action == "find":
        _FOUND = files.search(query, kind)
        if not _FOUND:
            return f"Je ne trouve aucun fichier qui s'appelle {query}." if query else "Dis-moi quoi chercher."
        return _say_files(_FOUND)
    target = None
    if index and 1 <= index <= len(_FOUND):
        target = _FOUND[index - 1].path
    elif query:
        found = files.search(query, kind)
        if not found:
            return f"Je ne trouve aucun fichier qui s'appelle {query}."
        if len(found) > 1 and action != "trash":
            _FOUND = found
            target = found[0].path          # le plus récent, mais on dit qu'il y en a d'autres
        elif len(found) > 1:
            _FOUND = found
            return _say_files(found) + "\nDis-moi lequel mettre à la corbeille."
        else:
            target = found[0].path
    if target is None:
        return "Dis-moi quel fichier."
    if action == "open":
        files.open_file(target)
        others = " Il y en a d'autres du même nom, dis « le deuxième » si ce n'est pas le bon." \
            if len(_FOUND) > 1 else ""
        return f"J'ouvre {target.name}.{others}"
    if action == "reveal":
        files.reveal(target)
        return f"Je te montre {target.name} dans son dossier."
    files.trash(target)
    return f"J'ai mis {target.name} à la corbeille, tu peux encore la récupérer."


@tool("windows", "Fenêtres ouvertes : list (dire ce qui est ouvert), switch (passer à une application, "
      "name), minimize, maximize, fullscreen, close (la fenêtre au premier plan), left ou right (ranger "
      "la fenêtre sur une moitié d'écran).",
      {"action": {"type": "string", "enum": ["list", "switch", *gui.ACTIONS]}, "name": {"type": "string"}},
      ("action",))
def windows_tool(action: str, name: str = "") -> str:
    if action == "list":
        open_windows = gui.list_windows()
        if not open_windows:
            return "Je ne vois aucune fenêtre ouverte."
        return "Applications ouvertes : " + ", ".join(w.app for w in open_windows) + "."
    if action == "switch":
        if not name:
            return "Dis-moi quelle application."
        window = gui.find(name)
        if window is None:
            app = apps.find_app(name)
            if app is None:
                return f"{name} n'est pas ouvert et je ne le trouve pas sur l'ordinateur."
            apps.launch(app)
            return f"{app.name} n'était pas ouvert, je le lance."
        gui.activate(window)
        return f"Je passe sur {window.app}."
    try:
        gui.act(action)
    except RuntimeError as exc:
        if str(exc) != "mac-keys":
            raise
        keys = gui.MAC_KEYS.get(action)
        if keys is None or DESKTOP is None:
            return "Ranger les fenêtres côte à côte n'est pas possible sur ce Mac ; utilise Rectangle."
        return _desktop_call(lambda desktop: desktop.shortcut(keys)) and gui.SPOKEN[action]
    return gui.SPOKEN[action]


@tool("clipboard", "Presse-papiers : read pour dire ce qui est copié, write pour y mettre le texte donné.",
      {"action": {"type": "string", "enum": ["read", "write"]}, "text": {"type": "string"}}, ("action",))
def clipboard_tool(action: str, text: str = "") -> str:
    if action == "write":
        if not text:
            return "Dis-moi quoi copier."
        clipboard.write(text)
        return "C'est copié, tu peux le coller où tu veux."
    content = clipboard.read().strip()
    if not content:
        return "Le presse-papiers est vide."
    short = content if len(content) <= 400 else content[:400] + "…"
    return f"Tu as copié : {short}"


@tool("settings", "Réglages de la machine : brightness (luminosité, level de 0 à 100 ou change relatif), "
      "wifi (sans on : dire à quel réseau tu es connecté ; avec on : allumer ou couper), bluetooth (état).",
      {"action": {"type": "string", "enum": ["brightness", "wifi", "bluetooth"]},
       "level": {"type": "integer"}, "change": {"type": "integer"}, "on": {"type": "boolean"}}, ("action",))
def settings_tool(action: str, level: int | None = None, change: int | None = None,
                  on: bool | None = None) -> str:
    if action == "brightness":
        if level is None and change is None:
            current = settings.get_brightness()
            return f"Luminosité à {current} pour cent." if current is not None else \
                "Je ne peux pas lire la luminosité de cet écran, mais je peux la monter ou la baisser."
        return settings.set_brightness(level, change)
    if action == "wifi":
        return settings.wifi_status() if on is None else settings.set_wifi(bool(on))
    return settings.bluetooth_status()


@tool("screenshot", "Prend une capture d'écran et l'enregistre sur le bureau : screen (tout l'écran), "
      "area (tu choisis la zone à la souris) ou window (une fenêtre).",
      {"mode": {"type": "string", "enum": list(capture.MODES)}})
def screenshot_tool(mode: str = "screen") -> str:
    try:
        path = capture.take(mode)
    except RuntimeError as exc:
        if str(exc) == "outil-capture":
            return "J'ai ouvert l'outil de capture, choisis ta zone."
        if str(exc) == "capture annulée":
            return "Tu as annulé la capture."
        raise
    return f"C'est enregistré sur le bureau, {path.name}."


@tool("calculate", "Calcule une expression donnée à voix haute (pourcentages, racine, puissances). "
      "Le résultat est exact : à utiliser pour tout calcul plutôt que de compter toi-même.",
      {"expression": {"type": "string"}}, ("expression",))
def calculate_tool(expression: str) -> str:
    result = calc.evaluate(expression)
    return result or f"Je n'arrive pas à calculer {expression}."


# -- mémoire : ce que Jarvis retient d'une session à l'autre

MEMORY = Memory()


@tool("memory", "Mémoire de long terme sur l'utilisateur : remember (retenir un fait court, text), forget (oublier "
      "ce qui ressemble à text), list (réciter ce que tu sais), forget_all (tout effacer). À utiliser quand "
      "on te dit « retiens que », « souviens-toi », « oublie que », ou pour noter une préférence durable.",
      {"action": {"type": "string", "enum": ["remember", "forget", "list", "forget_all"]},
       "text": {"type": "string"}}, ("action",))
def memory_tool(action: str, text: str = "") -> str:
    if action == "remember":
        return MEMORY.remember(text)
    if action == "forget":
        return MEMORY.forget(text)
    if action == "forget_all":
        return MEMORY.forget_all()
    return MEMORY.recite()
