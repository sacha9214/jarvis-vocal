"""Sites web : noms courants → adresse, recherche, ouverture dans le navigateur par défaut."""
from __future__ import annotations

import re
import webbrowser
from urllib.parse import quote_plus

from ..fastpath import normalize

SITES = {
    "youtube": "https://www.youtube.com", "netflix": "https://www.netflix.com", "twitch": "https://www.twitch.tv",
    "gmail": "https://mail.google.com", "google": "https://www.google.com", "google maps": "https://maps.google.com",
    "wikipedia": "https://fr.wikipedia.org", "wikipedia fr": "https://fr.wikipedia.org",
    "amazon": "https://www.amazon.fr", "disney plus": "https://www.disneyplus.com",
    "disney": "https://www.disneyplus.com", "prime video": "https://www.primevideo.com",
    "github": "https://github.com", "chatgpt": "https://chatgpt.com", "claude": "https://claude.ai",
    "deepl": "https://www.deepl.com/translator", "leboncoin": "https://www.leboncoin.fr",
    "instagram": "https://www.instagram.com", "tiktok": "https://www.tiktok.com", "twitter": "https://x.com",
    "facebook": "https://www.facebook.com", "reddit": "https://www.reddit.com",
    "whatsapp web": "https://web.whatsapp.com", "linkedin": "https://www.linkedin.com",
    "pinterest": "https://www.pinterest.com", "outlook": "https://outlook.live.com",
    "deezer": "https://www.deezer.com", "canal plus": "https://www.canalplus.com",
    "crunchyroll": "https://www.crunchyroll.com", "le monde": "https://www.lemonde.fr",
}
_DOMAIN = re.compile(r"^(?:https?://)?(?:[a-z0-9-]+\.)+[a-z]{2,}(?:/\S*)?$", re.I)
_PREFIX = re.compile(r"^(?:le site (?:web )?(?:de |d )?|site (?:de )?|sur )")


def resolve_site(text: str) -> str | None:
    raw = text.strip().rstrip(".").lower().replace(" point ", ".")
    if _DOMAIN.match(raw):
        return raw if raw.startswith("http") else f"https://{raw}"
    key = _PREFIX.sub("", normalize(text))
    return SITES.get(key) or SITES.get(key.replace(" ", ""))


def search_url(query: str) -> str:
    return f"https://www.google.com/search?q={quote_plus(query)}"


def open_url(url: str) -> None:
    if not webbrowser.open(url):
        raise RuntimeError("aucun navigateur disponible")
