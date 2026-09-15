# Feuille de route

Une étape à la fois. Chacune arrive testée, mesurée et documentée avant la suivante.

| # | Étape | État |
|---|-------|------|
| 1 | **Pipeline vocal local optimisé** : mot d'activation, VAD, Whisper, Ollama en flux, Piper, coupure de parole, `bench`/`doctor`/`setup` | ✅ |
| 2 | **Abonnement Claude** : moteur qui pilote le binaire officiel `claude` en processus persistant (connexion par `claude auth login`, Jarvis ne lit jamais tes jetons), isolé de `~/.claude`, bascule à la voix, repli local automatique ; clé API en option | ✅ |
| 3 | **Outils & permissions** : registre typé, niveaux N1/N2/N3, confirmation vocale, serveur MCP partagé avec le moteur Claude Code | à faire |
| 4 | **Contrôle du PC, Windows + macOS** : apps, média, volume, minuteurs, via une couche `platform/` testée sur les deux OS | à faire |
| 5 | **Intégrations une par une** (agenda, musique, domotique…), chacune optionnelle et testée | à faire |
| 6 | **Interface** (HUD, overlay) et démarrage automatique | à faire |

## Problèmes du projet d'origine et réponse apportée

| Problème relevé dans sosoj92/jarvis-assistant-vocal | Ici |
|---|---|
| Windows 11 uniquement (`ctypes.windll`, `.bat`, `os.startfile`) | Code portable ; tout ce qui dépend de l'OS passera par `platform/` ; CI macOS + Windows à chaque push |
| 14 tests, pas de CI | Tests unitaires + CI GitHub Actions (ruff + pytest) sur les deux OS |
| Fichier principal de 1 300 lignes | Modules courts à responsabilité unique (`audio/`, `stt/`, `llm/`, `tts/`, `pipeline.py`) |
| Mode local fragile (petit modèle perdu dans 40 outils) | Réflexes sans LLM, prompt court et stable ; les outils arriveront filtrés par intention (étape 3) |
| Seuils audio fixes, fin de phrase approximative | VAD neuronal Silero avec hystérésis et pré-roll |
| Modèles téléchargés sans vérification | Empreintes SHA-256 épinglées pour VAD, mot d'activation et voix |
| Piper cassé par une mise à jour d'API | Dépendances verrouillées (`uv.lock`), CI qui casse avant l'utilisateur |
| Bugs silencieux (`!= "" or True`) | ruff en CI, exceptions remontées avec un message actionnable |
| Trop d'intégrations fragiles d'un coup | Étapes 4-5 : une intégration à la fois, chacune optionnelle et testée |
