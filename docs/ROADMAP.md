# Feuille de route

Une étape à la fois. Chacune arrive testée, mesurée et documentée avant la suivante.

| # | Étape | État |
|---|-------|------|
| 1 | **Pipeline vocal local optimisé** : mot d'activation, VAD, Whisper, Ollama en flux, Piper, coupure de parole, `bench`/`doctor`/`setup` | ✅ |
| 2 | **Abonnement Claude** : binaire officiel `claude` en processus persistant (connexion par `claude auth login`, Jarvis ne lit jamais tes jetons), isolé de `~/.claude`, bascule à la voix, repli local | ✅ |
| 3 | **Actions sur le PC, macOS + Windows** : registre d'outils, niveaux N1/N2/N3, confirmation vocale ou d'un clic, commandes sans LLM, serveur MCP pour Claude | ✅ |
| 3b | **Compréhension de la voix** : vocabulaire de Whisper, garde-fous anti-boucle, correspondance phonétique des noms, phrases inachevées | ✅ |
| 3c | **Interface** : réacteur animé, journal, système, réglages en direct, permissions, fenêtre native | ✅ |
| 4 | **Intégrations une par une** (agenda, domotique, mail…), chacune optionnelle et testée | à faire |
| 5 | **Démarrage automatique et annulation d'écho** | à faire |

## Problèmes du projet d'origine et réponse apportée

| Problème relevé dans sosoj92/jarvis-assistant-vocal | Ici |
|---|---|
| Windows 11 uniquement (`ctypes.windll`, `.bat`, `os.startfile`) | Couche `system/` qui gère macOS et Windows ; CI sur les deux OS à chaque push |
| 14 tests, pas de CI | 129 tests + CI GitHub Actions (ruff + pytest) sur macOS et Windows |
| Fichier principal de 1 300 lignes | Modules courts à responsabilité unique (`audio/`, `stt/`, `llm/`, `tools/`, `system/`, `ui/`) |
| Mode local fragile (petit modèle perdu dans 40 outils) | Commandes courantes reconnues sans LLM ; 13 outils clairs, testés 8/8 avec `qwen3.5:4b-mlx` |
| Panneau web exposé, correctifs de sécurité en urgence | Serveur limité à 127.0.0.1, contrôle du nom d'hôte, jeton de session, en-tête anti-CSRF, dès le départ |
| Seuils audio fixes, fin de phrase approximative | VAD neuronal Silero avec hystérésis et pré-roll |
| Modèles téléchargés sans vérification | Empreintes SHA-256 épinglées pour VAD, mot d'activation et voix |
| Piper cassé par une mise à jour d'API | Dépendances verrouillées (`uv.lock`), CI qui casse avant l'utilisateur |
| Bugs silencieux (`!= "" or True`) | ruff en CI, échecs dits à voix haute et affichés dans l'interface |
| Trop d'intégrations fragiles d'un coup | Étape 4 : une intégration à la fois, chacune optionnelle et testée |
