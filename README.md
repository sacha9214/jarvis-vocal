# Jarvis — assistant vocal local

Assistant vocal en français qui tourne **sur ta machine**, sous **macOS et Windows**.
Tu dis « Hey Jarvis », tu parles normalement, il répond à voix haute. Et tu peux lui
couper la parole.

> Réécriture de [sosoj92/jarvis-assistant-vocal](https://github.com/sosoj92/jarvis-assistant-vocal)
> (MIT), repartie de zéro pour corriger ses défauts : Windows uniquement, quasiment pas
> de tests, mode local lent et fragile. Le détail est dans [docs/ROADMAP.md](docs/ROADMAP.md).

**État : étape 1 sur 6, le pipeline vocal 100 % local.** La connexion avec ton abonnement
Claude arrive à l'étape 2, les actions sur le PC à l'étape 3.

## Latence mesurée

MacBook Air M5 16 Go, modèles par défaut, `uv run jarvis bench` :

| Étage | Temps |
|---|---|
| Fin de ta phrase détectée (silence) | 550 ms, réglable |
| Transcription Whisper large-v3-turbo q4 (MLX) | ~350 ms |
| LLM `qwen3.5:4b-mlx` : premier token, puis première phrase complète | ~200 ms, ~690 ms |
| Voix Piper : premier son | ~55 ms |
| **Fin de ta phrase → première syllabe** | **~1,65 s** pour une question, **~0,96 s** pour l'heure ou la date |

Mesure sur ta propre machine avec `uv run jarvis bench` : chaque étage est chronométré
séparément, sans micro.

## Ce qui rend la conversation fluide

- **Fin de phrase détectée par un réseau de neurones** (Silero VAD, avec hystérésis et
  pré-roll), pas par un seuil de volume ni un délai fixe.
- **Tout est chargé et chauffé au démarrage, en parallèle**, et reste en mémoire
  (`keep_alive`). Aucune première question lente.
- **Réponse en flux** : le LLM écrit, la phrase part en synthèse dès qu'elle est complète,
  la voix commence pendant que le reste s'écrit. Le premier morceau part dès la première
  virgule.
- **Réflexes sans LLM** : heure, date, « stop » répondus instantanément.
- **Réglages mesurés, pas devinés** : Whisper quantifié q4 (même texte que la version
  pleine, ~1 Go de mémoire en moins), variante `-mlx` d'Ollama sur Mac, pas de
  « réflexion » du modèle (`think=False`), contexte court, prompt système stable pour
  réutiliser le cache.
- **Coupure de parole** : l'écoute continue pendant qu'il parle ; « Hey Jarvis » le fait
  taire dans les 20 ms.
- **Choix automatique selon la machine** : Metal sur Apple Silicon, CUDA sur GPU NVIDIA,
  modèles réduits sur CPU seul (`uv run jarvis doctor` montre le plan choisi).

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

Au premier lancement, macOS demande l'accès au micro pour ton terminal : accepte.

### Windows 10/11

```powershell
winget install astral-sh.uv Ollama.Ollama
git clone https://github.com/sacha9214/jarvis-vocal.git
cd jarvis-vocal
uv sync                 # avec un GPU NVIDIA : uv sync --extra cuda
uv run jarvis setup
uv run jarvis
```

`jarvis setup` télécharge le LLM (~4 Go), Whisper (~0,5 Go), la voix (~60 Mo) et les
petits modèles d'écoute. Tous les fichiers audio sont vérifiés par empreinte SHA-256.

## Commandes

| Commande | Rôle |
|---|---|
| `uv run jarvis` | lance l'assistant |
| `uv run jarvis setup` | télécharge les modèles adaptés à ta machine |
| `uv run jarvis doctor` | vérifie Ollama, les modèles, le micro et la sortie audio |
| `uv run jarvis bench` | mesure la latence de chaque étage |
| `uv run jarvis devices` | liste les périphériques audio |

Pour régler quoi que ce soit, copie `config.example.yaml` en `config.yaml` : chaque clé y
est commentée.

## Architecture

```mermaid
flowchart LR
    Mic([Micro]) --> WW[Mot d'activation<br/>openWakeWord ONNX]
    WW --> VAD[Silero VAD<br/>fin de phrase]
    VAD --> STT[Whisper<br/>MLX ou CTranslate2]
    STT --> FP{Réflexe ?}
    FP -- oui --> TTS
    FP -- non --> LLM[Ollama<br/>en flux]
    LLM --> CH[Découpe en phrases]
    CH --> TTS[Piper]
    TTS --> Out([Haut-parleurs])
    Mic -. « Hey Jarvis » pendant qu'il parle .-> Out
```

```
src/jarvis/
  audio/      micro, sortie, mot d'activation, VAD, capture d'une phrase
  stt/        Whisper : mlx (Apple Silicon), faster-whisper (CUDA/CPU)
  llm/        interface en flux + backend Ollama
  tts/        Piper
  text/       découpe du flux du LLM en morceaux prononçables
  pipeline.py la boucle de conversation
  hardware.py détection de la machine → choix des modèles
  bench.py    doctor.py  assets.py  config.py
```

## Limites connues

- **Pas d'annulation d'écho** : sans casque, le micro entend Jarvis. Un garde-fou ignore
  ce qu'il vient de dire et la coupure passe par « Hey Jarvis », mais un casque reste le
  plus confortable.
- **Windows** : validé par la CI (installation, lint, tests) mais pas encore sur une
  vraie machine avec micro.
- **Étape 1 seulement** : Jarvis converse, mais n'agit pas encore sur le PC.

## Développement

```bash
uv run pytest
uv run ruff check
```

La CI GitHub Actions lance les deux sur macOS et Windows à chaque push.

Licence MIT.
