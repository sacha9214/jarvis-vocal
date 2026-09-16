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

Ce que Jarvis occupe pendant ce temps, mesuré sur ce Mac :

| Ressource | Valeur |
|---|---|
| Mémoire de Jarvis (écoute, transcription, voix) | 2,6 Go |
| Mémoire du modèle local, dans Ollama | 4,7 Go |
| Processeur en veille | 7 à 8 % d'un cœur |
| Processeur pendant que Jarvis parle | environ 1,5 cœur |
| Une transcription de 3 secondes | 380 ms |

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
- **Jamais moins de trois mots.** Mesuré (synthèse puis retranscription par Whisper, 64 essais par
  variante) : Fantine rate ~40 % des énoncés d'un ou deux mots (« Pause. » devient « Pose », « Oui. »
  devient un souffle), 2 % à partir de trois mots. Un mot d'amorce n'y change rien. Toutes les phrases
  de Jarvis font donc au moins trois mots, et le découpeur colle un morceau trop court au suivant. La
  carte son n'était pas en cause : zéro sous-alimentation mesurée, même en latence basse avec Ollama
  qui génère en même temps.

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
| « résume ce document », « clique sur Envoyer », « enregistre le fichier » | lit et pilote l'application ouverte | N1 / N2 |
| « c'est quoi cette erreur ? », « explique cette fonction », « va à la ligne 42 » | lit et pilote VS Code (extension) | N1 |
| « écris un commentaire ici », « lance les tests » | écrit dans le fichier, exécute | N2 |
| « fais une review de mon code », « relis mes changements avec Claude » | review en arrière-plan | N1 |
| « qu'est-ce que tu vois ? », « c'est quoi cette erreur ? » | regarde l'écran et répond | N1 |
| « donne-moi l'état de l'ordinateur » | batterie, processeur, mémoire | N1 |
| « cherche le fichier rapport », « trouve mon CV », « ouvre le fichier contrat » | cherche et ouvre un fichier | N1 |
| « qu'est-ce qui est ouvert ? », « passe sur Discord » | liste les fenêtres, change d'application | N1 |
| « réduis la fenêtre », « plein écran », « mets la fenêtre à gauche » | gère la fenêtre du dessus | N1 |
| « qu'est-ce que j'ai copié ? », « copie ce texte » | presse-papiers | N1 |
| « monte la luminosité », « à quel réseau je suis connecté ? » | écran, Wi-Fi, Bluetooth | N1 |
| « prends une capture » | capture enregistrée sur le bureau | N1 |
| « combien font 15 pour cent de 340 ? » | calcul exact, sans passer par le modèle | N1 |
| « combien de place libre ? », « crée un dossier Photos » | disque, dossiers | N1 |
| « ferme Discord », « verrouille l'écran » | fermeture, verrouillage | N2 |
| « éteins l'ordinateur dans 25 minutes », « redémarre le PC » | extinction différée, annulable à tout moment | N3 |

- **Extinction différée** : « éteins l'ordinateur dans 25 minutes », « redémarre le pc dans une heure »,
  « mets l'ordinateur en veille dans 20 minutes ». Jarvis confirme, annonce l'heure, **prévient une
  minute avant**, et « annule l'extinction » arrête tout. « Il reste combien de temps ? » donne le compte
  à rebours. Sans délai, il attend 20 secondes, le temps de te raviser.
- **N1** agit tout de suite. **N2** demande « Tu confirmes ? » : réponds « oui », « non » ou
  « toujours » pour ne plus être interrogé. **N3** demande à chaque fois, sans exception.
- La confirmation se donne **à la voix ou d'un clic** dans l'interface.
- Les phrases courantes sont reconnues **sans LLM** ; le reste part au modèle, qui dispose des
  **mêmes outils**, avec les mêmes confirmations.
- Avec Claude, les outils passent par un **serveur MCP local** : Claude n'a accès à aucun de
  ses outils intégrés (ni terminal, ni fichiers), seulement aux actions de Jarvis.

## Contrôle de la machine

Au-delà des applications et du son, Jarvis touche à l'ordinateur lui-même. Tout marche sur **macOS et
Windows**, et la partie Windows est vérifiée à chaque envoi par la CI, qui exécute ces tests sur une
vraie machine Windows.

| Domaine | Ce que tu peux dire |
|---|---|
| **Fichiers** | « cherche le fichier rapport », « trouve mon CV », « trouve mes photos de vacances », « ouvre le fichier contrat », « ouvre le deuxième », « montre où est le contrat », « crée un dossier Vacances », « combien de place libre ? » |
| **Fenêtres** | « qu'est-ce qui est ouvert ? », « passe sur Discord », « réduis la fenêtre », « plein écran », « ferme la fenêtre », « mets la fenêtre à gauche » |
| **Presse-papiers** | « qu'est-ce que j'ai copié ? », « copie rendez-vous à 15 h » |
| **Réglages** | « monte la luminosité », « luminosité à 50 », « à quel réseau je suis connecté ? », « coupe le Wi-Fi », « état du Bluetooth » |
| **Capture** | « prends une capture », « prends une capture d'une zone » |
| **Calcul** | « combien font 15 pour cent de 340 ? », « racine carrée de 144 », « 1250 divisé par 5 » |

- La **recherche de fichiers** utilise l'index du système, celui de la loupe : Spotlight sur macOS,
  Windows Search sur Windows. Sans index, Jarvis parcourt tes dossiers personnels pendant quatre
  secondes au plus, plutôt que de fouiller tout le disque.
- Quand plusieurs fichiers portent le même nom, il les numérote : « ouvre le deuxième » suffit.
- **Mettre à la corbeille** reste récupérable, et n'est jamais une suppression définitive.
- Le **calcul est exact** : il ne passe pas par le modèle, qui se trompe sur les nombres. L'expression
  est analysée puis évaluée opération par opération, jamais exécutée comme du code.
- Toutes ces phrases sont reconnues **sans LLM**, donc immédiates. Le modèle a les mêmes outils pour
  les formulations libres.

## Navigateur

Jarvis pilote la vidéo et la page de ton navigateur : volume de la vidéo (indépendant du volume
de l'ordinateur), pause, avance, vitesse, vidéo suivante, choix d'une vidéo, clic sur un bouton ou un
lien, défilement, onglets, recherche YouTube ou Google. Pour une demande libre (« mets la vidéo de
cuisine »), le modèle **lit d'abord la page**, puis clique.

Il **remplit aussi les formulaires**. La lecture de page numérote les champs de saisie avec leur nom et
ce qu'ils contiennent déjà, à côté des liens et des boutons :

```
2. [champ] Titre
3. [champ] Description (contient : ma première version)
6. [bouton] Publier
```

- « écris ma description dans le champ Description », « tape chat mignon dans Rechercher » : reconnu
  **sans LLM**, le champ est visé par son nom (libellé, `aria-label`, texte d'invite ou `name`).
- Sans nom de champ, Jarvis écrit dans celui qui est sélectionné ; il peut aussi le sélectionner
  lui-même (« clique sur Description »).
- Par défaut le texte **s'ajoute** à ce que contient le champ ; le modèle peut demander à le remplacer.
- Vérifié dans un vrai Firefox sur les quatre sortes de champs : `input`, `textarea`, champ à texte
  d'invite seul, et zone `contenteditable`.

| Navigateur | Comment |
|---|---|
| Chrome, Edge, Brave, Opera, Vivaldi, Arc | extension Jarvis : `uv run jarvis extension`, puis « Charger l'extension non empaquetée » |
| Firefox | la même extension, en module temporaire (à recharger après chaque redémarrage de Firefox) ; un clic sur l'icône Jarvis accorde l'accès aux sites, que Firefox ne donne pas à l'installation |
| Safari (macOS) | sans extension, par AppleScript : activer « Autoriser JavaScript depuis les Apple Events » |

- `jarvis extension` affiche la marche à suivre et ouvre le dossier à charger.
- Jarvis agit sur le **dernier navigateur utilisé** : cliquer dans sa fenêtre pour lui parler ne
  change pas la cible.
- **Sécurité** : le pont n'écoute que 127.0.0.1, n'accepte que des extensions (une page web ne peut
  pas s'y connecter) et exige un jeton propre à ta machine. Jarvis **refuse de cliquer** sur acheter,
  payer, supprimer, envoyer, publier, s'abonner… ; fermer un onglet ou taper du texte demande confirmation.

## Toute application

Hors du navigateur, Jarvis lit et pilote l'application que tu utilises — Word, Outlook, Discord,
l'Explorateur, les Réglages, ton éditeur — par l'**accessibilité du système**, celle des lecteurs d'écran :
« résume ce document », « c'est quoi ce message ? », « clique sur Envoyer », « appuie sur contrôle S ».

| Système | Comment | À autoriser |
|---|---|---|
| Windows 10/11 | UI Automation | rien |
| macOS | API d'accessibilité (AX) | Réglages Système › Confidentialité et sécurité › Accessibilité, pour ton terminal |

- Jarvis **lit d'abord** (titre, texte visible, boutons et menus numérotés), puis clique par numéro ou par nom.
- Les éléments sont activés **sans bouger la souris** quand le système le permet.
- Le contenu des **champs de mot de passe n'est jamais lu**.
- Mêmes garde-fous que dans le navigateur : Jarvis refuse acheter, payer, supprimer, envoyer, publier… et
  refuse aussi un « Oui » dans une fenêtre qui parle de supprimer. Taper du texte et envoyer un raccourci
  demandent confirmation (N2).
- « Contrôle S » devient **Cmd+S** sur Mac : c'est ce que veut dire quelqu'un qui vient de Windows.

## Éditeur de code (VS Code)

VS Code dessine son éditeur dans un canvas et **n'expose rien à l'accessibilité** (mesuré : 12 éléments,
0 caractère, même avec `editor.accessibilitySupport`). Jarvis passe donc par une **extension VS Code**,
sur le modèle de celle du navigateur : `uv run jarvis code` l'empaquette (VSIX, sans Node ni vsce) et
l'installe dans VS Code, Cursor, Windsurf ou VSCodium ; « Jarvis » apparaît dans la barre d'état.

| Tu dis | Ce que fait Jarvis |
|---|---|
| « explique ce fichier », « résume ce que j'ai sélectionné » | lit le fichier autour du curseur (lignes numérotées), la sélection, les onglets |
| « c'est quoi cette erreur ? », « il reste des problèmes ? » | lit les diagnostics (linter, compilateur) du fichier ou du projet |
| « ouvre pipeline point py », « va à la ligne 42 », « passe au deuxième onglet » | ouvre, saute, change d'onglet |
| « enregistre », « formate le fichier », « commente la ligne », « va à la définition » | ~40 commandes sûres de l'éditeur |
| « cherche foreground dans le projet » | recherche dans tous les fichiers |
| « écris `pass` ici », « lance les tests » | écrit au curseur / à la place de la sélection, exécute (N2 : confirmation) |

- Les gestes courants (enregistrer, formater, aller à la ligne, ouvrir un fichier, onglets, tests) sont
  reconnus **sans LLM**.
- La **review** utilise le vrai fichier ouvert et le vrai dossier du projet donnés par l'extension,
  plus besoin de deviner d'après le titre de la fenêtre.
- **Sécurité** : même pont local que le navigateur (127.0.0.1, port 47831, jeton propre à ta machine),
  route `/editor` réservée aux clients **sans en-tête Origin** : un navigateur en envoie toujours un,
  une page web ne peut donc pas se faire passer pour l'éditeur. L'extension n'embarque aucun secret, elle
  lit le jeton sur le disque. Pas d'identifiant de commande libre : le modèle choisit dans une liste
  fermée, sans accès au terminal (`sendSequence`), ni push, ni suppression.
- Mesuré dans un VS Code isolé : chaque action répond en **1 à 65 ms**.

## Review de code

« Fais une review de mon code », « review de mes changements avec Claude », « relis ce fichier »,
« fais la review du projet site vitrine » : Jarvis la lance **en arrière-plan**, te le dit, et t'annonce
le résumé quand elle est finie. Le rapport complet arrive dans le journal de l'interface et en markdown
dans le dossier de données (`reviews/`).

- **Quel projet** : celui de ta fenêtre VS Code, Cursor, Windsurf ou VSCodium (titre et historique de
  l'éditeur), les projets récents des IDE JetBrains, ou le dossier que tu nommes (cherché dans Bureau,
  Documents, `dev`, `projects`, `source\repos`, OneDrive…).
- **Quoi** : selon ta phrase, tout le projet, tes **changements non commités** (git) ou le **fichier affiché**.
- **Avec Claude** (par défaut si Claude est le moteur actif, modèle `sonnet`) : Claude explore le projet
  **en lecture seule** (lire, chercher, lister ; ni terminal, ni écriture), lancé hors du projet pour que
  ses réglages et serveurs MCP ne se chargent pas. Si Claude échoue, Jarvis bascule en local et le dit.
- **En local** : relecture par passes (le contexte du petit modèle est court), en pause pendant tes
  conversations, 120 ko de code au plus (réglable) ; le rapport signale une review partielle.
- « Où en est la review ? », « annule la review ».

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

## Mot d'activation

Sensibilité par défaut **0,25**, choisie par la mesure : 25 enregistrements de « Hey Jarvis » (quatre voix,
plusieurs formulations) face à cinq minutes de parole française et de bruit.

| Situation | Seuil 0,5 (avant) | Seuil 0,25 |
|---|---|---|
| Au calme | 96 % | 100 % |
| Bruit de fond modéré | 73 % | 96 % |
| Bruit de fond fort | 48 % | 83 % |
| Pendant que Jarvis parle (lui couper la parole) | 64 % | 80 % |
| Réveils intempestifs (5 min de parole et de bruit) | 0 | 0 |

- **Dire simplement « Jarvis » suffit** : le modèle le reconnaît aussi bien.
- Quand Jarvis reconnaît le mot à moitié, il l'écrit dans le journal et dans l'interface
  (« j'ai cru entendre… score 0,18 ») : de quoi régler la sensibilité au lieu de répéter dans le vide.
- **Le volume n'entre pas en jeu** : mesuré, le score est identique de 0 à −30 dB. Parler plus fort ne
  sert à rien, s'éloigner du bruit oui.
- Le pas d'analyse de 80 ms n'est pas réglable : l'affiner à 40 ms fait *chuter* la détection de 73 à
  23 % dans le bruit, parce que les 16 embeddings du classifieur ne couvrent plus la durée du mot.

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

## LLM sur une autre machine du réseau

Le modèle local peut tourner sur un autre ordinateur (un PC avec une bonne carte graphique, par
exemple) : Jarvis lui parle par le réseau, le micro, la voix et les captures restent ici.

1. Sur la machine qui héberge le modèle : `ollama pull qwen3.5:9b`, puis fais écouter Ollama sur le
   réseau avec la variable d'environnement `OLLAMA_HOST=0.0.0.0` (Windows : dans les variables
   d'environnement, puis relance Ollama ; macOS : `launchctl setenv OLLAMA_HOST 0.0.0.0` et relance
   l'application) et ouvre le port 11434 dans son pare-feu.
2. Ici, dans les réglages (« Serveur Ollama ») ou dans `config.yaml` :

```yaml
llm:
  host: http://192.168.1.20:11434
  model: auto          # prend le plus gros qwen3.5 du serveur ; ou un nom précis
```

- `jarvis doctor` dit si le serveur répond et quel modèle est choisi ; un modèle absent est signalé
  avec la liste de ceux qui existent là-bas.
- **Les captures d'écran ne partent pas sur le réseau** : l'analyse d'écran garde l'Ollama de cette
  machine. Pour l'envoyer aussi au serveur distant, mets son adresse dans `screen.host` (réglage
  « Serveur Ollama pour l'écran »), en connaissance de cause.
- La review de code locale et les questions passent par le serveur distant : c'est du texte.

## Commandes personnalisées

Tes phrases, tes actions, dans `config.yaml` (`jarvis doctor` valide la section) :

```yaml
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
  - name: thé
    say: ["lance le thé"]
    tool: set_timer
    args: { seconds: 180, label: thé }
```

- Une action par commande : `run` (ligne de commande, lancée dans ton dossier personnel), `open`
  (adresse web, fichier ou dossier), `keys` (raccourci dans l'application au premier plan) ou `tool`
  (une action de Jarvis, avec `args`).
- `*` dans une phrase capture ce que tu dis à cet endroit, disponible en `{text}` dans `run`, `open`,
  `args` et `reply` (guillemets et `$` retirés avant le shell).
- `confirm: true` fait demander confirmation (N2, mémorisable par « toujours ») ; `speak_output: true`
  lit la sortie de la commande (20 s maximum).
- Les phrases sont reconnues **sans LLM**, accents et ponctuation ignorés. Le modèle les voit aussi
  comme outils `custom_<nom>` : « tu peux ouvrir mon projet ? » marche même sans la phrase exacte.

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
| `uv run jarvis code` | empaquette l'extension VS Code et l'installe dans les éditeurs trouvés |
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
  browser/    extension navigateur, pont WebSocket local (navigateur et éditeur), Safari par AppleScript
  editor/     extension VS Code (JS pur), empaquetage VSIX et installation
  review/     projet ouvert dans l'éditeur, review par Claude (lecture seule) ou par le modèle local
  desktop/    lecture et pilotage de toute application (UI Automation, accessibilité macOS)
  system/     applications, fenêtre active, volume, lecture, dossiers, alimentation (macOS + Windows)
  vision/     analyse de l'écran par le modèle local
  ui/         interface : page, API des réglages, fenêtre native
  commands.py commandes vocales reconnues sans LLM
  custom.py   commandes personnalisées de config.yaml (phrases → shell, ouverture, raccourci, outil)
  pipeline.py la boucle de conversation
  server.py   serveur local (127.0.0.1, jeton de session)
```

## Réduire la mémoire utilisée

Mesuré poste par poste sur un MacBook Air M3 de 16 Go :

| Poste | Mémoire |
|---|---|
| Modèle local, dans Ollama | 4,1 Go |
| Voix Pocket TTS | 760 Mo |
| Whisper, une fois chauffé | 730 Mo |
| Mot d'activation et détection de voix | 140 Mo |
| Python, torch, onnxruntime | 230 Mo |

**Ce qui libère de la mémoire sans rien perdre :**

- **Passer sur Claude rend les 3,6 Go du modèle local.** Dis « passe sur Claude » et Jarvis décharge
  le modèle qui ne sert plus. Il se recharge tout seul au retour. Réglage « Libérer la mémoire en
  passant sur Claude », actif par défaut.
- **Baisser « Garder le modèle en mémoire »** de 30 à 5 minutes rend les mêmes 3,6 Go dès que tu ne
  parles plus. Prix mesuré : 1,3 seconde de rechargement sur la première question après la pause,
  en partie masquée par la préchauffe déclenchée quand tu dis « Hey Jarvis ».

**Ce qui se paie, à toi de voir :**

- **Voix Piper** au lieu de Pocket TTS : 760 Mo de moins, une voix nettement moins humaine.
- **Modèle `qwen3.5:2b`** au lieu de `4b` : 930 Mo de moins et des réponses 0,3 s plus rapides, mais
  la qualité chute. Mesuré sur 24 demandes, les deux choisissent aussi bien leurs outils (18 sur 24) ;
  en revanche, sur des questions ouvertes, le 2b a répondu « une pâte brune » pour un plat de pâtes et
  « Konnichiwa pour l'honneur » pour dire bonjour en japonais. Je ne le recommande pas.

## Optimisations essayées et écartées

Mesurées sur un MacBook Air M3, pour qu'elles ne soient pas retentées à l'aveugle :

| Idée | Résultat mesuré |
|---|---|
| Analyser l'audio tous les 40 ms au lieu de 80 pour mieux entendre le mot d'activation | Détection **effondrée** : 73 → 23 % dans le bruit. Le classifieur ne voit plus la durée du mot |
| Faire tourner le mot d'activation sur le Neural Engine (CoreML) | **1,5 fois plus lent** : les modèles sont petits et découpés en 5 partitions, les allers-retours coûtent plus que le calcul |
| Ignorer les trames silencieuses avant le modèle | 3 détections perdues sur 25, pour 5 à 13 % de calcul économisé. Le modèle a besoin d'un flux continu |
| Nourrir le modèle par blocs de 80 ms au lieu de 32 | Aucun gain mesurable (7,3 → 7,8 %, dans le bruit de mesure) |
| Réduire la fenêtre de contexte du modèle local | Ne libère que 50 Mo : 4,07 Go à 4 096 tokens contre 4,12 Go à 8 192 |
| Quantifier la voix Pocket TTS | **Pire des deux côtés** : 2,0 Go au lieu de 1,7, et deux fois plus lente à générer |
| Vider le cache mémoire de MLX après chaque transcription | Le cache passe bien de 708 Mo à zéro, mais **le système ne récupère rien** : la mémoire du processus ne bouge pas d'un mégaoctet |
| Forcer le mode hors ligne de HuggingFace au démarrage | Aucun gain (3 455 contre 3 660 ms, dans le bruit) |

Ce qui reste pour alléger vraiment relève du compromis, pas de l'optimisation : la voix Piper à la
place de Pocket TTS libère 1,7 Go avec un rendu moins humain, et `qwen3.5:2b` à la place de `4b`
libère environ 2 Go avec des réponses moins fines. Les deux se changent dans les réglages.

## Limites connues

- **16 Go de mémoire, c'est juste** : LLM (~4,1 Go), Whisper, voix naturelle (~0,8 Go) et tes
  applications se partagent la mémoire. Si le Mac swappe, tout ralentit : ferme les applications
  lourdes, ou prends `qwen3.5:2b-mlx` et la voix Piper dans les réglages. Garde-fous : l'analyse
  d'écran attend quand il reste moins de 1,5 Go libres (`screen.min_free_gb`), Whisper rend ses
  tampons Metal après chaque phrase, et le cache du modèle n'est rechauffé qu'au « Hey Jarvis »
  suivant (mesuré : 1,6 à 2,3 s de GPU à chaque rechauffe, inutile si personne ne parle).
- **Fenêtre de contexte** : mesuré, le prompt fait déjà 2 936 tokens à vide en contexte éditeur
  (27 outils) ; `llm.num_ctx` est donc à 8 192. En dessous, Ollama tronque le prompt sans prévenir.
  Le cache d'Ollama n'est valable que pour une liste d'outils donnée : Jarvis chauffe donc, au
  « Hey Jarvis », le prompt avec les outils du contexte où tu es (navigateur, éditeur, application).
  Mesuré : 1,5 s de préremplissage évitée par question dans ces contextes (0,02 s au lieu de 1,51 s).
  `jarvis bench` mesure sans outils : la vraie question avec outils coûte le même prix grâce au cache.
- **En cas de plantage** : tout est dans `logs/jarvis.log` du dossier de données (`jarvis doctor`
  affiche le chemin) ; un plantage natif (MLX, torch, PortAudio) laisse sa pile dans `logs/crash.log`.
  Une ligne « 💾 » par conversation donne mémoire, swap, trames micro perdues et nombre de fils.
- **Micro perdu** (débranché, pris par une autre application, session verrouillée) : Jarvis le dit au
  bout de trois secondes et rouvre le flux tout seul toutes les cinq secondes jusqu'à ce qu'il revienne.
  Il ne réinitialise pas PortAudio pour autant : mesuré, cela couperait sa propre voix.
- **Consommation en veille** : 1,4 s de processeur par 20 s d'écoute, soit **7 à 8 % d'un cœur** sur un
  MacBook Air M3. Presque tout est dans le modèle du mot d'activation : recevoir l'audio et publier les
  niveaux ne coûtent que 0,7 %. Il n'y a donc rien à gagner côté code. (Une première mesure annonçait
  1,3 % : elle traitait l'audio en boucle serrée, donc sur un cœur de performance à pleine fréquence.
  En vrai, le travail est étalé dans le temps et tombe sur un cœur d'efficacité, plus lent. La quantité
  de calcul est la même, la part d'un cœur non.)
- **Pas d'annulation d'écho** : sans casque, le micro entend Jarvis. Un garde-fou ignore
  ce qu'il vient de dire et la coupure passe par « Hey Jarvis ».
- **Windows** : validé par la CI (installation, lint, tests) mais pas encore sur une vraie
  machine avec micro. La voix naturelle y dépend de la puissance du processeur (Piper sinon).
- **Applications** : la lecture et la saisie sont vérifiées pour de vrai (TextEdit sur macOS, Bloc-notes
  sur la CI Windows), mais certaines applications exposent peu de choses à l'accessibilité (jeux, applications
  Electron mal étiquetées) : Jarvis dit alors qu'il ne voit rien plutôt que de cliquer au hasard.
- **Review locale** : sur un fichier piégé de 5 bugs, `qwen3.5:4b` en trouve 4 (secret en dur, injection
  SQL, division par zéro, valeur par défaut mutable) sans fausse alerte, mais signale aussi un faux problème
  sur un fichier sain du projet (~7 s par fichier). Elle repère les erreurs flagrantes ; pour une vraie
  review, passe par Claude.
- **VS Code** : la lecture du terminal n'est pas exposée par l'API des extensions (Jarvis ne lit pas la
  sortie d'une commande) ; dans un dossier « non approuvé », VS Code bloque lui-même l'exécution.
- **Navigateur** : vérifié pour de vrai dans Firefox (connexion, lecture de page, onglets, reprise après
  la mise en veille de la page d'arrière-plan) ; deux défauts trouvés à cette occasion et corrigés : la
  politique de sécurité MV3 de Firefox transformait `ws://` en `wss://` (le pont recevait un handshake
  TLS), et les actions écrites en méthode raccourcie ne s'injectaient pas. Chrome, Edge, Brave ne sont
  pas encore vérifiés en vrai (Chrome 137+ n'accepte plus `--load-extension` en test automatique). Les
  pages internes (`chrome://`, boutique d'extensions) restent inaccessibles.
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
