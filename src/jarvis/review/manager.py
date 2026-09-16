"""Review en arrière-plan : une à la fois, annoncée à voix haute, rapport dans l'interface et sur disque."""
from __future__ import annotations

import logging
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from ..config import Config
from ..events import EventBus
from ..paths import data_dir
from ..system.foreground import Foreground
from . import collect, engines
from .project import Project, find_project

LOG = logging.getLogger("jarvis.review")
LOCAL, CLAUDE = "local", "claude"
SCOPES = ("project", "changes", "file")


@dataclass
class Job:
    project: Project
    material: collect.Material
    engine: str
    cancel: threading.Event = field(default_factory=threading.Event)
    progress: tuple[int, int] = (0, 0)
    started: float = field(default_factory=time.time)
    note: str = ""

    @property
    def label(self) -> str:
        if self.material.scope == "file" and self.material.files:
            name = self.material.files[0].name       # dit à voix haute : « d'engines.py », pas « de engines.py »
            return f"la review d'{name}" if name[:1].lower() in "aeiouyéèêh" else f"la review de {name}"
        if self.material.scope == "changes":
            return f"la review de tes changements dans {self.project.name}"
        return f"la review du projet {self.project.name}"

    @property
    def who(self) -> str:
        return "Claude" if self.engine == CLAUDE else "le modèle local"


def _capital(text: str) -> str:
    return text[:1].upper() + text[1:]


class ReviewManager:
    def __init__(self, cfg: Config, engine: Callable[[], str], window: Callable[[], Foreground | None] = lambda: None,
                 bus: EventBus | None = None, chat: engines.Chat | None = None,
                 claude: engines.ClaudeTask | None = None, reports: Path | None = None,
                 dirs: list[Path] | None = None, hint: Callable[[], tuple[Path, Path | None] | None] | None = None):
        self.cfg = cfg
        self.engine = engine            # moteur actif de la conversation
        self.window = window            # dernière fenêtre d'éditeur utilisée
        self.bus = bus or EventBus()
        self.reports = reports or data_dir() / "reviews"
        self.dirs = dirs                # tests : historique d'éditeur factice
        self.hint = hint                # extension VS Code reliée : (projet, fichier affiché) exacts
        self.announce: Callable[[str], None] = lambda text: None
        self.busy: Callable[[], bool] = lambda: False
        self.on_local_done: Callable[[], None] = lambda: None
        self.last_label = ""
        self._chat = chat
        self._claude = claude
        self._job: Job | None = None
        self._lock = threading.Lock()

    # -- demandes

    def start(self, scope: str = "project", target: str = "", engine: str = "auto") -> str:
        with self._lock:
            if self._job is not None:
                return f"{_capital(self._job.label)} est déjà en cours : je te préviens dès qu'elle est finie."
            project = None
            if not target and self.hint is not None and (known := self.hint()) is not None:
                project = Project(*known)
            project = project or find_project(self.window(), target, self.dirs)
            if project is None:
                if target:
                    return f"Je ne trouve pas le projet {target} parmi les dossiers ouverts récemment dans ton éditeur."
                return ("Je ne trouve pas de projet ouvert dans ton éditeur. Ouvre son dossier dans VS Code, "
                        "ou dis-moi son nom.")
            scope = scope if scope in SCOPES else "project"
            prefix = ""
            if scope == "file" and project.current_file is None:
                scope, prefix = "project", "Je ne sais pas quel fichier est ouvert, je relis donc tout le projet. "
            if scope == "file":
                material = collect.Material("file", project.root, [project.current_file])
            elif scope == "changes":
                found = collect.changes(project.root)
                if found is None:
                    return (f"{project.name} n'est pas un dépôt git : je ne peux pas savoir ce que tu as changé. "
                            "Demande la review du projet pour tout relire.")
                if not found.diff.strip() and not found.files:
                    return f"Il n'y a aucun changement non commité dans {project.name}."
                material = found
            else:
                material = collect.Material("project", project.root, collect.project_files(project.root))
                if not material.files:
                    return f"Je ne trouve pas de code à relire dans {project.name}."
                if project.current_file is not None:     # le fichier affiché d'abord : relu même si la limite coupe
                    material.files.sort(key=lambda path: path != project.current_file)
            chosen = engine if engine in (LOCAL, CLAUDE) else (CLAUDE if self.engine() == CLAUDE else LOCAL)
            job = self._job = Job(project, material, chosen)
        # Phrase figée avant le démarrage : le fil peut basculer en local aussitôt si Claude échoue.
        delay = " Ça peut prendre quelques minutes." if chosen == LOCAL and scope != "file" else ""
        sentence = f"{prefix}Je lance {job.label} avec {job.who}, je te préviens quand c'est fini.{delay}"
        self.bus.publish("review_started", label=job.label, engine=chosen, root=str(project.root))
        threading.Thread(target=self._run, args=(job,), name="review", daemon=True).start()
        return sentence

    def status(self) -> str:
        job = self._job
        if job is None:
            last = f" La dernière, {self.last_label}, est dans le journal de l'interface." if self.last_label else ""
            return f"Aucune review en cours.{last}"
        minutes = int((time.time() - job.started) // 60)
        done, total = job.progress
        step = f", {done} passes sur {total}" if total else ""
        since = f" depuis {minutes} minute{'s' if minutes > 1 else ''}" if minutes else " depuis moins d'une minute"
        return f"{_capital(job.label)} est en cours avec {job.who}{step},{since}."

    def cancel(self) -> str:
        job = self._job
        if job is None:
            return "Aucune review en cours."
        job.cancel.set()
        return f"J'arrête {job.label}."

    # -- exécution

    def _run(self, job: Job) -> None:
        try:
            if job.engine == CLAUDE:
                try:
                    report = self._with_claude(job)
                except RuntimeError as exc:
                    if job.cancel.is_set():
                        raise engines.Cancelled from None
                    LOG.warning("Review avec Claude impossible (%s) : modèle local à la place.", exc)
                    job.engine, job.note = LOCAL, f"Claude indisponible ({exc}) : review faite par le modèle local."
                    self.bus.publish("error", text=job.note)
                    report = self._with_local(job)
            else:
                report = self._with_local(job)
            if job.cancel.is_set():
                raise engines.Cancelled
            summary, body = engines.split_report(report)
            path = self._save(job, summary, body)
            self.last_label = job.label
            self.bus.publish("review", label=job.label, engine=job.engine, summary=summary, report=body,
                             path=str(path), note=job.note)
            LOG.info("Review terminée : %s", path)
            self.announce(f"{_capital(job.label)} est terminée. {summary}")
        except engines.Cancelled:
            self.bus.publish("review_cancelled", label=job.label)
        except Exception as exc:  # noqa: BLE001 - Ollama absent, dossier illisible… : dit à voix haute
            LOG.exception("Review en échec")
            self.bus.publish("error", text=f"Review impossible : {exc}")
            self.announce(f"Je n'ai pas pu terminer {job.label} : {exc}")
        finally:
            with self._lock:
                self._job = None

    def _with_claude(self, job: Job) -> str:
        if self._claude is None:
            raise RuntimeError("Claude Code n'est pas configuré")
        request, args = engines.claude_request(job.material)
        return self._claude(request, engines.CLAUDE_SYSTEM, args)

    def _with_local(self, job: Job) -> str:
        if self._chat is None:
            self._chat = engines.ollama_chat(self.cfg)

        def wait() -> None:     # la conversation passe avant : le modèle local ne sert qu'un appel à la fois
            while self.busy() and not job.cancel.is_set():
                time.sleep(0.5)

        def progress(done: int, total: int) -> None:
            job.progress = (done, total)
            self.bus.publish("review_progress", label=job.label, done=done, total=total)

        size, notes_size = engines.budgets(self.cfg.llm.num_ctx)
        try:
            report, note = engines.local_review(job.material, self._chat, self.cfg.review.max_chars, size, notes_size,
                                                cancel=job.cancel, wait=wait, progress=progress)
        finally:
            self.on_local_done()
        job.note = " ".join(part for part in (job.note, note) if part)
        return report

    def _save(self, job: Job, summary: str, body: str) -> Path:
        self.reports.mkdir(parents=True, exist_ok=True)
        now = datetime.now()
        name = re.sub(r"[^\w.-]+", "-", job.project.name).strip("-") or "projet"
        path = self.reports / f"{name}-{now:%Y-%m-%d-%H%M%S}.md"
        lines = [f"# {_capital(job.label.removeprefix('la '))}", "", f"- Dossier : `{job.project.root}`",
                 f"- Moteur : {'Claude' if job.engine == CLAUDE else 'modèle local'}", f"- Date : {now:%d/%m/%Y %H:%M}"]
        if job.note:
            lines.append(f"- Note : {job.note}")
        path.write_text("\n".join(lines) + f"\n\n**Résumé** : {summary}\n\n{body}\n", encoding="utf-8")
        return path
