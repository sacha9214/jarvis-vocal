# Jarvis — assistant vocal local

Assistant vocal en français qui tourne **sur ta machine**, sous **macOS et Windows**.
Tu dis « Hey Jarvis », tu parles normalement, il répond d'une **voix naturelle**, **agit sur ton
ordinateur** (applications, musique, volume, minuteurs…), **pilote les vidéos et les pages de ton
navigateur**, **voit ce que tu fais à l'écran** et
s'affiche dans une **interface holographique** où tu règles tout en direct. Il réfléchit en local
ou avec **ton abonnement Claude**.

> Réécriture de [sosoj92/jarvis-assistant-vocal](https://github.com/sosoj92/jarvis-assistant-vocal)
> (MIT), repartie de zéro pour corriger ses défauts : Windows uniquement, quasiment pas
> de tests, mode local lent et fragile. Le détail est dans [docs/ROADMAP.md](docs/ROADMAP.md).

## Latence mesurée

MacBook Air M5 16 Go, modèles par défaut, `uv run jarvis bench` :

| Étage | Temps |
|---|---|
| Fin de ta phrase détectée (silence) | 550 ms, réglable |
| Transcription Whisper large-v3-turbo q4 (MLX) | ~370 ms (~550 ms si la mémoire est saturée) |
| LLM `qwen3.5:4b-mlx` : premier token, puis première phrase complète | ~200 ms, ~550-850 ms |
| Voix naturelle Pocket TTS : premier son | ~150-250 ms (Piper : ~55 ms) |
| **Fin de ta phrase → première syllabe** | **~1,7-2 s** pour une question, **~1-1,5 s** pour une commande |

Les commandes courantes sur le PC ne passent pas par le LLM : elles répondent aussi vite
que l'heure ou la date.

## Voix naturelle

Jarvis parle avec [Pocket TTS](https://github.com/kyutai-labs/pocket-tts) (Kyutai, MIT), une
voix neuronale française bien plus humaine que Piper. Elle tourne sur le processeur, en flux,
sans ralentir Whisper ni le LLM sur le GPU.

- **Voix par défaut : Fantine.** Mesuré sur 16 réponses courtes typiques, elle en rend 14 à 15
  correctement ; les voix masculines (Marius, Jean, Javert) tombent à 2 ou 3 sur ce type de phrase.
  Elles restent choisissables, signalées comme instables.
- La voix change **instantanément** depuis l'interface. `tts.voice` accepte aussi un fichier
  `.wav` à imiter.
- Garde-fous : si la machine est trop lente pour parler sans hacher, Jarvis garde **Piper** ; si une
  génération s'emballe, elle est coupée.

## Ce que Jarvis sait faire sur ton ordinateur

| Tu dis | Action | Niveau |
|---|---|---|
| « ouvre Spotify », « lance la calculatrice » | ouvre une application installée | N1 |
| « va sur YouTube », « cherche recette de crêpes » | ouvre un site, lance une recherche | N1 |
| « ouvre mes téléchargements » | ouvre un dossier | N1 |
| « monte le son », « volume à trente », « coupe le son » | volume | N1 |
| « mets pause », « chanson suivante » | lecture (Spotify, Musique ; lecteur actif sous Windows) | N1 |
| « mets un minuteur de 5 minutes pour les pâtes » | minuteur annoncé à voix haute | N1 |
| « baisse le volume de la vidéo », « avance de 30 secondes » | vidéo du navigateur | N1 |
| « lance la deuxième vidéo », « clique sur Paramètres » | clic dans la page | N1 |
| « cherche des tutos Python sur YouTube », « ferme l'onglet » | recherche, onglets | N1 / N2 |
| « qu'est-ce que tu vois ? », « c'est quoi cette erreur ? » | regarde l'écran et répond | N1 |
| « donne-moi l'état de l'ordinateur » | batterie, processeur, mémoire | N1 |
| « ferme Discord », « verrouille l'écran » | fermeture, verrouillage | N2 |
| « éteins l'ordinateur », « redémarre le PC » | extinction après 20 s, « annule l'extinction » | N3 |

- **N1** agit tout de suite. **N2** demande « Tu confirmes ? » : réponds « oui », « non » ou
  « toujours » pour ne plus être interrogé. **N3** demande à chaque fois, sans exception.
- La confirmation se donne **à la voix ou d'un clic** dans l'interface.
- Les phrases courantes sont reconnues **sans LLM** ; le reste part au modèle, qui dispose des
  **mêmes outils**, avec les mêmes confirmations.
- Avec Claude, les outils passent par un **serveur MCP local** : Claude n'a accès à aucun de
  ses outils intégrés (ni terminal, ni fichiers), seulement aux actions de Jarvis.

## Navigateur

Jarvis pilote la vidéo et la page de ton navigateur : volume de la vidéo (indépendant du volume
de l'ordinateur), pause, avance, vitesse, vidéo suivante, choix d'une vidéo, clic sur un bouton ou un
lien, défilement, onglets, recherche YouTube ou Google. Pour une demande libre (« mets la vidéo de
cuisine »), le modèle **lit d'abord la page**, puis clique.

| Navigateur | Comment |
|---|---|
| Chrome, Edge, Brave, Opera, Vivaldi, Arc | extension Jarvis : `uv run jarvis extension`, puis « Charger l'extension non empaquetée » |
| Firefox | la même extension, en module temporaire (à recharger après chaque redémarrage de Firefox) |
| Safari (macOS) | sans extension, par AppleScript : activer « Autoriser JavaScript depuis les Apple Events » |

- `jarvis extension` affiche la marche à suivre et ouvre le dossier à charger.
- Jarvis agit sur le **dernier navigateur utilisé** : cliquer dans sa fenêtre pour lui parler ne
  change pas la cible.
- **Sécurité** : le pont n'écoute que 127.0.0.1, n'accepte que des extensions (une page web ne peut
  pas s'y connecter) et exige un jeton propre à ta machine. Jarvis **refuse de cliquer** sur acheter,
  payer, supprimer, envoyer, publier, s'abonner… ; fermer un onglet ou taper du texte demande confirmation.

## Analyse de l'écran

Jarvis comprend ce que tu es en train de faire : toutes les ~20 s, et seulement si l'écran a
changé, une capture réduite est décrite en une phrase par le **modèle de vision local**
(« Sacha code en Python dans Visual Studio Code »). Cette phrase accompagne tes questions, y compris
vers Claude ; l'image, elle, ne quitte jamais la machine et n'est jamais écrite sur disque.

- L'analyse **se met en pause pendant les conversations** pour ne jamais ralentir une réponse.
- « C'est quoi cette erreur ? » déclenche un **regard immédiat** avec ta question.
- Panneau **Activité** dans l'interface ; onglet **Écran** pour la couper, régler sa fréquence
  et la précision de la capture.
- macOS demande l'autorisation « Enregistrement de l'écran » pour ton terminal au premier usage.

## L'interface

- Réacteur animé qui réagit à ta voix et à celle de Jarvis, avec une couleur par état :
  veille, écoute, réflexion, parole, confirmation.
- Journal en direct : ce que tu as dit, ce que Jarvis a répondu, chaque action avec son
  niveau et sa durée.
- Système (processeur, mémoire, batterie), activité à l'écran, minuteurs, latence du dernier échange.
- Boutons **Parler** (sans « Hey Jarvis »), **Stop**, bascule **Local / Claude**.
- **Réglages** : prénom, moteur, micro, silence de fin de phrase, voix, écran, modèles, permissions.
  Chaque réglage indique s'il s'applique **en direct** ou au **redémarrage** ; l'enregistrement
  écrit `config.yaml` et un bouton relance Jarvis si besoin.
- Raccourcis : `Espace` parler, `S` stop, `Entrée` / `Échap` pour confirmer ou refuser.

Elle s'ouvre dans une fenêtre native (WebKit sur macOS, WebView2 sous Windows). Le serveur
n'écoute que ta machine (127.0.0.1), refuse les autres noms d'hôte et exige le jeton aléatoire
de la session. Aperçu sans charger les modèles : `uv run jarvis hud`.

## Compréhension de la voix

- **Vocabulaire** : Whisper reçoit les noms de tes applications et les commandes courantes. Sur
  48 commandes enregistrées par des voix de synthèse, les bonnes commandes passent de **15 à 26**.
- **Garde-fous** : si le vocabulaire fait dérailler Whisper (boucle, phrase fantôme), la phrase est
  retranscrite aussitôt sans lui ; la longueur d'une transcription est plafonnée.
- **Noms mal entendus** : la recherche d'application compare aussi la prononciation
  (« Spotifaille » → Spotify).
- **Hésitations** : « Ouvre… euh… » ne part pas tout de suite, Jarvis attend la fin de la phrase.

## Installation

### macOS (Apple Silicon)

```bash
brew install uv ollama
brew services start ollama
git clone https://github.com/sacha9214/jarvis-vocal.git
cd jarvis-vocal
uv sync
uv run jarvis setup
uv run jarvis
```

Au premier lancement, macOS demande l'accès au micro, à l'enregistrement de l'écran et au
pilotage de Spotify ou Musique : accepte.

### Windows 10/11

```powershell
winget install astral-sh.uv Ollama.Ollama
git clone https://github.com/sacha9214/jarvis-vocal.git
cd jarvis-vocal
uv sync                 # avec un GPU NVIDIA : uv sync --extra cuda
uv run jarvis setup
uv run jarvis
```

Tout mettre à jour d'un coup :

```powershell
winget upgrade --id Ollama.Ollama; winget upgrade --id astral-sh.uv; git pull; uv sync; claude update
```

`jarvis setup` télécharge le LLM (~4 Go), Whisper (~0,5 Go), les voix (~1,3 Go) et les petits
modèles d'écoute. Les modèles d'écoute et la voix Piper sont vérifiés par empreinte SHA-256.

**Ollama doit tourner** pour le mode local et l'analyse d'écran (application Ollama, ou
`brew services start ollama` sur Mac). Le mode Claude, la voix et l'interface fonctionnent sans.

## Utiliser ton abonnement Claude

1. Installe [Claude Code](https://code.claude.com) et connecte-toi une fois, dans ton
   terminal : `claude auth login`.
2. Dis « Hey Jarvis, passe sur Claude », clique sur **CLAUDE** dans l'interface, ou mets
   `llm.backend: claude` dans les réglages.

- **Dans les règles.** Jarvis lance le programme officiel `claude`, non modifié, et lit
  sa sortie. Il ne lit, ne stocke ni ne transmet jamais tes identifiants : la connexion
  passe par le flux d'Anthropic, comme le prévoit la page *Legal and compliance* de Claude
  Code. Réutiliser directement les jetons d'un abonnement dans une autre application est interdit.
- **Rapide.** Un seul processus `claude` reste ouvert pendant toute la session. Modèle par
  défaut : `haiku`, le plus rapide à répondre.
- **Isolé.** Rien de ton `~/.claude` n'est chargé : ni CLAUDE.md, ni mémoire, ni hooks, ni
  plugins, ni connecteurs, ni outils intégrés.
- **Tolérant.** Si Claude échoue avant de répondre (hors ligne, limite atteinte, connexion
  expirée), Jarvis le dit et répond en local.

## Commandes

| Commande | Rôle |
|---|---|
| `uv run jarvis` | lance l'assistant et son interface (`--ui browser` ou `--ui none` au besoin) |
| `uv run jarvis hud` | aperçu de l'interface avec des données simulées |
| `uv run jarvis extension` | prépare l'extension navigateur et explique comment l'ajouter |
| `uv run jarvis setup` | télécharge les modèles adaptés à ta machine |
| `uv run jarvis doctor` | vérifie Ollama, Claude Code, les voix, le micro et la sortie audio |
| `uv run jarvis bench` | mesure la latence de chaque étage (`--backend claude` pour Claude) |
| `uv run jarvis devices` | liste les périphériques audio |

| À la voix | Effet |
|---|---|
| « Hey Jarvis » | réveille Jarvis, ou lui coupe la parole |
| « passe sur Claude » / « passe en local » | change de moteur |
| « quel modèle tu utilises ? » | dit le moteur actif |
| « quelle heure est-il ? », « on est quel jour ? » | réponse instantanée |
| « stop » | le remet en veille |

## Architecture

```
src/jarvis/
  audio/      micro, sortie, mot d'activation, VAD, capture d'une phrase
  stt/        Whisper (mlx, faster-whisper), vocabulaire et garde-fous
  tts/        Pocket TTS (voix naturelle), Piper (secours)
  llm/        Ollama, Claude Code, routeur local/Claude avec repli
  tools/      registre des actions, permissions N1/N2/N3, serveur MCP
  browser/    extension navigateur, pont WebSocket local, Safari par AppleScript
  system/     applications, fenêtre active, volume, lecture, dossiers, alimentation (macOS + Windows)
  vision/     analyse de l'écran par le modèle local
  ui/         interface : page, API des réglages, fenêtre native
  commands.py commandes vocales reconnues sans LLM
  pipeline.py la boucle de conversation
  server.py   serveur local (127.0.0.1, jeton de session)
```

## Limites connues

- **16 Go de mémoire, c'est juste** : LLM (~4,8 Go), Whisper, voix naturelle (~1,6 Go) et tes
  applications se partagent la mémoire. Si le Mac swappe, tout ralentit : ferme les applications
  lourdes, ou prends `qwen3.5:2b-mlx` et la voix Piper dans les réglages.
- **Pas d'annulation d'écho** : sans casque, le micro entend Jarvis. Un garde-fou ignore
  ce qu'il vient de dire et la coupure passe par « Hey Jarvis ».
- **Windows** : validé par la CI (installation, lint, tests) mais pas encore sur une vraie
  machine avec micro. La voix naturelle y dépend de la puissance du processeur (Piper sinon).
- **Navigateur** : les actions sont testées sur une vraie page (lecture, volume, clics, refus des
  actions sensibles) et le pont par des tests automatiques ; l'extension n'a pas encore été chargée
  dans chaque navigateur. Les pages internes (`chrome://`, boutique d'extensions) restent inaccessibles.
- **Voix de test** : les mesures de compréhension utilisent des voix de synthèse, plus dures à
  transcrire qu'une vraie voix.

## Développement

```bash
uv run pytest
uv run ruff check
```

La CI GitHub Actions lance les deux sur macOS et Windows à chaque push. Le moteur Claude
est testé contre un faux `claude`, la voix contre un faux modèle : aucun test ne consomme
d'abonnement ni n'agit sur l'ordinateur.

Licence MIT.
