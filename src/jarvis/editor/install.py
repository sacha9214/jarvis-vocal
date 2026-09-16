"""`jarvis code` : empaquette l'extension VS Code (VSIX) et l'installe dans les éditeurs trouvés.

Un VSIX est un zip : pas besoin de Node ni de vsce. L'extension n'embarque aucun secret, elle lit le
jeton du pont dans le dossier de données de Jarvis au démarrage.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import zipfile
from pathlib import Path

from ..paths import data_dir
from ..system import IS_MAC, IS_WINDOWS, NO_WINDOW

SOURCE = Path(__file__).parent / "extension"
FILES = ("package.json", "extension.js")
_MANIFEST = """<?xml version="1.0" encoding="utf-8"?>
<PackageManifest Version="2.0.0" xmlns="http://schemas.microsoft.com/developer/vsx-schema/2011" \
xmlns:d="http://schemas.microsoft.com/developer/vsx-schema-design/2011">
  <Metadata>
    <Identity Language="fr-FR" Id="{name}" Version="{version}" Publisher="{publisher}" />
    <DisplayName>{display}</DisplayName>
    <Description xml:space="preserve">{description}</Description>
    <Tags>jarvis,assistant vocal</Tags>
    <Categories>Other</Categories>
    <GalleryFlags>Public</GalleryFlags>
    <Properties>
      <Property Id="Microsoft.VisualStudio.Code.Engine" Value="{engine}" />
      <Property Id="Microsoft.VisualStudio.Code.ExtensionDependencies" Value="" />
      <Property Id="Microsoft.VisualStudio.Code.ExtensionPack" Value="" />
      <Property Id="Microsoft.VisualStudio.Code.ExtensionKind" Value="ui,workspace" />
      <Property Id="Microsoft.VisualStudio.Code.LocalizedLanguages" Value="" />
    </Properties>
    <License>extension/LICENSE.txt</License>
  </Metadata>
  <Installation>
    <InstallationTarget Id="Microsoft.VisualStudio.Code" />
  </Installation>
  <Dependencies />
  <Assets>
    <Asset Type="Microsoft.VisualStudio.Code.Manifest" Path="extension/package.json" Addressable="true" />
    <Asset Type="Microsoft.VisualStudio.Services.Content.License" Path="extension/LICENSE.txt" Addressable="true" />
  </Assets>
</PackageManifest>
"""
_CONTENT_TYPES = """<?xml version="1.0" encoding="utf-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension=".json" ContentType="application/json" />
  <Default Extension=".vsixmanifest" ContentType="text/xml" />
  <Default Extension=".js" ContentType="application/javascript" />
  <Default Extension=".txt" ContentType="text/plain" />
</Types>
"""
_LICENSE = "MIT License. Extension Jarvis : voir le dépôt jarvis-vocal.\n"
# Éditeurs de la famille VS Code : nom affiché, commande en ligne, emplacements hors PATH.
EDITORS = (
    ("Visual Studio Code", "code", "Visual Studio Code.app", "Microsoft VS Code"),
    ("Visual Studio Code - Insiders", "code-insiders", "Visual Studio Code - Insiders.app",
     "Microsoft VS Code Insiders"),
    ("Cursor", "cursor", "Cursor.app", "cursor"),
    ("Windsurf", "windsurf", "Windsurf.app", "Windsurf"),
    ("VSCodium", "codium", "VSCodium.app", "VSCodium"),
)


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def manifest() -> dict:
    return json.loads((SOURCE / "package.json").read_text(encoding="utf-8"))


def build(target: Path | None = None) -> Path:
    """Écrit le VSIX (zip) et renvoie son chemin."""
    package = manifest()
    target = target or data_dir() / "editor" / f"{package['name']}-{package['version']}.vsix"
    target.parent.mkdir(parents=True, exist_ok=True)
    header = _MANIFEST.format(name=package["name"], version=package["version"], publisher=package["publisher"],
                              display=_escape(package["displayName"]), description=_escape(package["description"]),
                              engine=package["engines"]["vscode"])
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("extension.vsixmanifest", header)
        archive.writestr("[Content_Types].xml", _CONTENT_TYPES)
        archive.writestr("extension/LICENSE.txt", _LICENSE)
        for name in FILES:
            archive.write(SOURCE / name, f"extension/{name}")
    return target


def find_editors() -> list[tuple[str, str]]:
    """(nom, commande) des éditeurs installés, par leur commande en ligne ou leur emplacement habituel."""
    found = []
    for name, cli, mac_app, win_dir in EDITORS:
        candidates = [shutil.which(cli)]
        if IS_MAC:
            candidates += [f"/Applications/{mac_app}/Contents/Resources/app/bin/{cli}",
                           str(Path.home() / "Applications" / mac_app / "Contents" / "Resources" / "app" / "bin" / cli)]
        elif IS_WINDOWS:
            local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "Programs"
            programs = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
            candidates += [str(local / win_dir / "bin" / f"{cli}.cmd"), str(programs / win_dir / "bin" / f"{cli}.cmd")]
        if command := next((c for c in candidates if c and Path(c).exists()), None):
            found.append((name, command))
    return found


def install(vsix: Path, command: str) -> str | None:
    """Installe (ou met à jour) l'extension ; None si l'éditeur a accepté, sinon le message d'erreur."""
    try:
        completed = subprocess.run([command, "--install-extension", str(vsix), "--force"], capture_output=True,
                                   text=True, encoding="utf-8", errors="replace", timeout=120, creationflags=NO_WINDOW)
    except (OSError, subprocess.SubprocessError) as exc:
        return str(exc)
    if completed.returncode != 0:
        return (completed.stderr or completed.stdout).strip()[-400:] or f"code de sortie {completed.returncode}"
    return None


def run() -> int:
    vsix = build()
    print(f"Extension VS Code de Jarvis empaquetée : {vsix}\n")
    editors = find_editors()
    for name, command in editors:
        error = install(vsix, command)
        print(f"  {'✅' if error is None else '❌'} {name}" + (f" : {error}" if error else " : extension installée"))
    if not editors:
        print("Aucun éditeur de la famille VS Code trouvé (code, cursor, windsurf, codium).")
    print("\nSinon, dans l'éditeur : Extensions › menu « … » › « Installer à partir d'un fichier VSIX » et choisis")
    print(f"  {vsix}")
    print("Redémarre l'éditeur si Jarvis n'apparaît pas dans sa barre d'état (en bas à droite) ;")
    print("l'icône reste « hors ligne » tant que Jarvis n'est pas lancé : c'est normal.")
    return 0
