"""Claude via le binaire officiel `claude` (Claude Code), connecté à ton abonnement.

Jarvis ne lit, ne stocke ni ne transmet aucun identifiant : il lance le programme `claude`
installé sur ta machine, non modifié, et lit sa sortie. La connexion passe par le flux
d'Anthropic (`claude auth login`), comme le prévoit la page « Legal and compliance » de
la documentation de Claude Code.

Latence : un seul processus reste ouvert (`--input-format stream-json`), le démarrage
n'est payé qu'une fois et chaque question suivante part immédiatement.
Sobriété : rien de ton ~/.claude ne s'y charge (CLAUDE.md, mémoire automatique, hooks,
plugins, connecteurs, outils), ce qui garde le prompt minuscule et le quota intact.
"""
from __future__ import annotations

import atexit
import itertools
import json
import logging
import os
import queue
import shutil
import subprocess
import threading
import time
from collections import deque
from collections.abc import Iterator, Sequence
from typing import Any

from ..config import ClaudeConfig
from ..paths import data_dir
from .base import Delta, Done, Event, Message

LOG = logging.getLogger("jarvis.llm")
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# Variables d'une session Claude Code parente (Jarvis lancé depuis Claude Code ou son app) :
# transmises au processus enfant, elles le feraient se croire imbriqué dans cette session.
_PARENT_PREFIXES = ("CLAUDE_CODE_", "CLAUDE_AGENT_SDK")
_PARENT_VARS = {"CLAUDECODE", "CLAUDE_PID", "CLAUDE_EFFORT", "ANTHROPIC_BASE_URL"}
_USER_VARS = {"CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CODE_GIT_BASH_PATH"}   # réglages de l'utilisateur, conservés
# En mode -p, une clé API présente passe avant l'abonnement : on la retire en mode abonnement.
_API_CREDENTIAL_VARS = {"ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"}
_ISOLATION_ENV = {
    "CLAUDE_CODE_DISABLE_CLAUDE_MDS": "1",
    "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
    "ENABLE_CLAUDEAI_MCP_SERVERS": "false",
}
# Erreurs pour lesquelles attendre les nouveaux essais de Claude Code ne sert à rien.
_FATAL_RETRY_ERRORS = {"authentication_failed", "oauth_org_not_allowed", "account_on_hold",
                       "billing_error", "rate_limit", "model_not_found"}


def child_env(auth: str, environ: dict[str, str] | None = None) -> dict[str, str]:
    source = dict(os.environ if environ is None else environ)
    env = {key: value for key, value in source.items()
           if key in _USER_VARS or not (key in _PARENT_VARS or key.startswith(_PARENT_PREFIXES))}
    if auth == "subscription":
        for key in _API_CREDENTIAL_VARS:
            env.pop(key, None)
    elif "ANTHROPIC_BASE_URL" in source:
        env["ANTHROPIC_BASE_URL"] = source["ANTHROPIC_BASE_URL"]
    env.update(_ISOLATION_ENV)
    return env


def isolation_settings(enabled_plugins: Sequence[str]) -> dict[str, Any]:
    return {
        "disableAllHooks": True,
        "autoMemoryEnabled": False,
        "claudeMdExcludes": ["**/CLAUDE.md", "**/CLAUDE.local.md"],
        "enabledPlugins": {plugin: False for plugin in enabled_plugins},
    }


def _error_message(result: dict[str, Any]) -> str:
    text = str(result.get("result") or result.get("subtype") or "erreur inconnue")
    low = text.lower()
    if "authenticat" in low or "oauth" in low or "log in" in low or "login" in low:
        return "Connexion Claude Code absente ou expirée : lance `claude auth login` dans un terminal."
    if "limit" in low or "rate" in low:
        return f"Limite d'utilisation de Claude atteinte ({text})."
    return f"Claude Code a renvoyé une erreur : {text}"


class ClaudeCodeLLM:
    name = "claude"

    def __init__(self, cfg: ClaudeConfig, command: list[str] | None = None):
        if cfg.auth not in ("subscription", "api_key"):
            raise ValueError(f"claude.auth inconnu : {cfg.auth} (subscription, api_key)")
        self.cfg = cfg
        self.model = cfg.model
        self._command = command          # tests : remplace l'exécutable `claude`
        self._workdir = data_dir() / "claude"   # dossier vide : aucun .mcp.json ni réglage de projet
        self._lock = threading.Lock()
        self._proc: subprocess.Popen[str] | None = None
        self._events: queue.Queue[dict[str, Any] | None] = queue.Queue()
        self._stderr: deque[str] = deque(maxlen=20)
        self._system: str | None = None
        self._known: list[tuple[str, str]] = []   # échanges déjà présents dans la session du processus
        self._turn_open = False
        self._plugins: list[str] | None = None
        self._request_ids = itertools.count(1)
        atexit.register(self.close)

    # -- vérifications

    def _base_command(self) -> list[str]:
        if self._command:
            return list(self._command)
        exe = shutil.which(self.cfg.executable)
        if not exe:
            raise RuntimeError("Claude Code est introuvable. Installe-le (https://code.claude.com), "
                               "puis connecte-toi avec `claude auth login`.")
        return [exe]

    def _run(self, args: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
        self._workdir.mkdir(parents=True, exist_ok=True)
        return subprocess.run(self._base_command() + args, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", cwd=self._workdir, env=child_env(self.cfg.auth),
                              timeout=timeout, creationflags=_NO_WINDOW)

    def check(self) -> None:
        if self.cfg.auth == "api_key":
            self._base_command()
            if not os.environ.get("ANTHROPIC_API_KEY"):
                raise RuntimeError("claude.auth vaut api_key mais la variable ANTHROPIC_API_KEY n'est pas définie.")
            return
        try:
            completed = self._run(["auth", "status"], timeout=20)
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeError(f"Impossible de lancer Claude Code ({exc}).") from exc
        try:
            status = json.loads(completed.stdout)
        except ValueError:
            status = {}
        if not status.get("loggedIn"):
            raise RuntimeError("Claude Code n'est pas connecté à ton abonnement (connexion absente ou expirée). "
                               "Lance `claude auth login` dans un terminal : pas besoin de relancer Jarvis, "
                               "redis simplement « passe sur Claude ».")
        LOG.debug("Claude Code connecté (méthode : %s).", status.get("authMethod"))

    def _enabled_plugins(self) -> list[str]:
        if self._plugins is None:
            try:
                completed = self._run(["plugin", "list", "--json"], timeout=20)
                self._plugins = [p["id"] for p in json.loads(completed.stdout) if p.get("enabled")]
            except (OSError, subprocess.SubprocessError, ValueError, TypeError, KeyError):
                LOG.warning("Liste des plugins Claude Code illisible : ils risquent de se charger.")
                self._plugins = []
        return self._plugins

    # -- cycle de vie du processus

    def _alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def _start(self, system_prompt: str) -> None:
        self._stop()
        base = self._base_command()
        self._workdir.mkdir(parents=True, exist_ok=True)
        prompt_file = self._workdir / "system-prompt.txt"
        prompt_file.write_text(system_prompt, encoding="utf-8")
        settings_file = self._workdir / "settings.json"
        plugins = [] if self.cfg.auth == "api_key" else self._enabled_plugins()
        settings_file.write_text(json.dumps(isolation_settings(plugins)), encoding="utf-8")

        # Fichiers plutôt qu'arguments : sous Windows, `claude.cmd` passe par cmd.exe, qui
        # abîme les retours à la ligne et les guillemets d'un prompt passé en argument.
        args = ["-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
                "--include-partial-messages", "--no-session-persistence", "--model", self.cfg.model,
                "--system-prompt-file", str(prompt_file), "--settings", str(settings_file),
                "--disallowedTools", "*", "--disable-slash-commands"]
        if self.cfg.effort:
            args += ["--effort", self.cfg.effort]
        if self.cfg.auth == "api_key":
            args.insert(0, "--bare")   # plus rapide, et n'utilise que la clé API

        events: queue.Queue[dict[str, Any] | None] = queue.Queue()
        proc = subprocess.Popen(base + args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
                                bufsize=1, cwd=self._workdir, env=child_env(self.cfg.auth),
                                creationflags=_NO_WINDOW)
        threading.Thread(target=self._read_stdout, args=(proc, events), name="claude-out", daemon=True).start()
        threading.Thread(target=self._read_stderr, args=(proc,), name="claude-err", daemon=True).start()
        self._proc, self._events = proc, events
        self._system = system_prompt
        self._known = []
        self._turn_open = False
        LOG.debug("Claude Code lancé (pid %s, modèle %s).", proc.pid, self.cfg.model)

    @staticmethod
    def _read_stdout(proc: subprocess.Popen[str], events: queue.Queue[dict[str, Any] | None]) -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.strip()
            if line.startswith("{"):
                try:
                    events.put(json.loads(line))
                except ValueError:
                    LOG.debug("Sortie Claude Code illisible : %.120s", line)
        events.put(None)

    def _read_stderr(self, proc: subprocess.Popen[str]) -> None:
        assert proc.stderr is not None
        for line in proc.stderr:
            if line.strip():
                self._stderr.append(line.rstrip())

    def _stop(self) -> None:
        proc, self._proc = self._proc, None
        self._turn_open = False
        if proc is None:
            return
        try:
            if proc.stdin:
                proc.stdin.close()
            proc.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            proc.kill()

    def close(self) -> None:
        self._stop()

    def _send(self, payload: dict[str, Any]) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None or proc.poll() is not None:
            raise RuntimeError(self._died_message())
        try:
            proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
            proc.stdin.flush()
        except OSError as exc:
            self._stop()
            raise RuntimeError(self._died_message()) from exc

    def _died_message(self) -> str:
        detail = self._stderr[-1] if self._stderr else "raison inconnue"
        return f"Claude Code s'est arrêté ({detail})."

    # -- conversation

    def warmup(self, system_prompt: str) -> None:
        """Lance le processus maintenant : son démarrage n'est pas payé à la première question."""
        with self._lock:
            if not self._alive() or system_prompt != self._system:
                self._start(system_prompt)

    def stream(self, messages: Sequence[Message], cancel: threading.Event | None = None) -> Iterator[Event]:
        system = messages[0]["content"] if messages and messages[0].get("role") == "system" else ""
        conversation = [(m["role"], m["content"]) for m in messages if m.get("role") != "system"]
        if not conversation or conversation[-1][0] != "user":
            raise ValueError("Le dernier message doit venir de l'utilisateur.")
        with self._lock:
            if not self._alive() or system != self._system:
                self._start(system)
            missed = self._missed_turns(conversation)
            user_text = conversation[-1][1]
            self._send({"type": "user", "message": {"role": "user", "content": self._compose(missed, user_text)}})
            self._turn_open = True
            reply: list[str] = []
            try:
                for event in self._turn_events(cancel):
                    if isinstance(event, Delta):
                        reply.append(event.text)
                    yield event
            finally:
                if self._turn_open:   # flux abandonné en cours de route
                    self._interrupt()
                self._known += [*missed, ("user", user_text), ("assistant", "".join(reply).strip() or "…")]

    def _missed_turns(self, conversation: list[tuple[str, str]]) -> list[tuple[str, str]]:
        """Échanges que ce processus n'a pas vus : réflexes, réponses du modèle local…"""
        past = conversation[:-1]
        if self._known:
            for index in range(len(past) - 1, -1, -1):
                if past[index] == self._known[-1]:
                    return past[index + 1:]
        return past

    @staticmethod
    def _compose(missed: list[tuple[str, str]], text: str) -> str:
        if not missed:
            return text
        lines = [f"{'Utilisateur' if role == 'user' else 'Jarvis'} : {content}" for role, content in missed]
        return ("Contexte, échanges précédents que tu n'as pas traités toi-même :\n" + "\n".join(lines)
                + f"\n\nDemande actuelle : {text}")

    def _turn_events(self, cancel: threading.Event | None) -> Iterator[Event]:
        started = time.perf_counter()
        spoke = False
        while True:
            if cancel is not None and cancel.is_set():
                self._interrupt()
                return
            try:
                event = self._events.get(timeout=0.1)
            except queue.Empty:
                if not spoke and time.perf_counter() - started > self.cfg.first_token_timeout_s:
                    self._stop()
                    raise RuntimeError(f"Claude n'a pas répondu en {self.cfg.first_token_timeout_s:.0f} s.") from None
                continue
            if event is None:
                self._stop()
                raise RuntimeError(self._died_message())
            kind = event.get("type")
            if kind == "stream_event":
                delta = (event.get("event") or {}).get("delta") or {}
                if delta.get("type") == "text_delta" and delta.get("text"):
                    spoke = True
                    yield Delta(delta["text"])
            elif kind == "system" and event.get("subtype") == "api_retry":
                LOG.info("Claude : nouvel essai (%s).", event.get("error"))
                if event.get("error") in _FATAL_RETRY_ERRORS:
                    self._interrupt()
                    raise RuntimeError(_error_message({"result": str(event.get("error"))}))
            elif kind == "result":
                self._turn_open = False
                if event.get("is_error"):
                    raise RuntimeError(_error_message(event))
                usage = event.get("usage") or {}
                yield Done(
                    prompt_tokens=sum(int(usage.get(k) or 0) for k in
                                      ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")),
                    output_tokens=int(usage.get("output_tokens") or 0),
                    output_ms=float(event.get("duration_api_ms") or 0),
                )
                return

    def _interrupt(self) -> None:
        """Coupe la réponse en cours et attend la fin du tour : le processus reste réutilisable."""
        self._turn_open = False
        try:
            self._send({"type": "control_request", "request_id": f"jarvis_{next(self._request_ids)}",
                        "request": {"subtype": "interrupt"}})
        except RuntimeError:
            return
        deadline = time.perf_counter() + 3.0
        while (remaining := deadline - time.perf_counter()) > 0:
            try:
                event = self._events.get(timeout=remaining)
            except queue.Empty:
                break
            if event is None:
                break
            if event.get("type") == "result":
                return
        LOG.warning("Claude Code n'a pas confirmé l'interruption : processus relancé à la prochaine question.")
        self._stop()
