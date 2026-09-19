"""Terminal qui affiche le journal de Jarvis en direct (réglage « Afficher le journal dans un terminal »).

Utile surtout avec l'exécutable Windows, lancé sans console : sans lui, on ne voit rien de ce que fait Jarvis.
Le terminal ne fait que lire logs/jarvis.log ; le fermer n'arrête pas Jarvis.
"""
from __future__ import annotations

import logging
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from ..logs import logs_dir

LOG = logging.getLogger("jarvis.ui")
_TAIL_LINES = 200


class LogTerminal:
    def __init__(self, path: Path | None = None):
        self.path = path or logs_dir() / "jarvis.log"
        self._process: subprocess.Popen | None = None
        self._pid_file = self.path.with_name("terminal.pid")

    def command(self) -> list[str]:
        """La commande qui ouvre le terminal, selon le système."""
        if sys.platform == "win32":
            follow = (f"$Host.UI.RawUI.WindowTitle = 'Journal de Jarvis'; "
                      f"Get-Content -LiteralPath '{self.path}' -Tail {_TAIL_LINES} -Wait -Encoding UTF8")
            return ["powershell", "-NoLogo", "-NoProfile", "-Command", follow]
        if sys.platform == "darwin":
            return ["open", "-a", "Terminal", str(self._script())]
        for terminal in ("x-terminal-emulator", "gnome-terminal", "konsole", "xterm"):
            if shutil.which(terminal):
                return [terminal, "-e", "tail", "-n", str(_TAIL_LINES), "-F", str(self.path)]
        return []

    def _script(self) -> Path:
        """macOS : Terminal ouvre un .command ; il note son numéro pour que Jarvis puisse l'arrêter."""
        script = self.path.with_name("journal.command")
        script.write_text("#!/bin/sh\n"
                          "printf '\\033]0;Journal de Jarvis\\007'\n"
                          f"echo $$ > {shlex.quote(str(self._pid_file))}\n"
                          f"exec tail -n {_TAIL_LINES} -F {shlex.quote(str(self.path))}\n", encoding="utf-8")
        script.chmod(0o755)
        return script

    @property
    def open(self) -> bool:
        if sys.platform == "darwin":
            return self._tail_pid() is not None
        return self._process is not None and self._process.poll() is None

    def _tail_pid(self) -> int | None:
        try:
            pid = int(self._pid_file.read_text().strip())
            os.kill(pid, 0)                  # encore vivant ?
            return pid
        except (OSError, ValueError):
            return None

    def show(self) -> None:
        if self.open:
            return
        self.path.touch(exist_ok=True)
        command = self.command()
        if not command:
            LOG.warning("Aucun terminal trouvé pour afficher le journal : il est dans %s", self.path)
            return
        flags = subprocess.CREATE_NEW_CONSOLE if sys.platform == "win32" else 0
        try:
            self._process = subprocess.Popen(command, creationflags=flags)
        except OSError as exc:
            LOG.warning("Terminal du journal impossible (%s) : il est dans %s", exc, self.path)
            return
        LOG.info("Journal affiché dans un terminal (%s)", self.path)

    def hide(self) -> None:
        if sys.platform == "darwin":        # `open` rend la main tout de suite : on arrête le tail lui-même
            if (pid := self._tail_pid()) is not None:
                os.kill(pid, 15)
            self._pid_file.unlink(missing_ok=True)
        elif self.open:
            self._process.terminate()        # Windows : ferme la console PowerShell
        self._process = None
