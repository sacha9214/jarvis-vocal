"""Réglages de la machine : luminosité de l'écran, Wi-Fi, Bluetooth, réseau connecté.

Tout passe par les outils fournis par le système, sans dépendance supplémentaire. Ce qui n'est pas
faisable proprement sur un système le dit au lieu de faire semblant.
"""
from __future__ import annotations

import ctypes
import re
import subprocess

from . import IS_MAC, IS_WINDOWS, NO_WINDOW, run, unsupported

_BRIGHTNESS_STEPS = 16          # macOS : une touche de luminosité vaut environ 1/16e
_VK_BRIGHTNESS_DOWN, _VK_BRIGHTNESS_UP = 0x91, 0x90      # touches multimédia, reconnues par Windows
_MAC_BRIGHTNESS_DOWN, _MAC_BRIGHTNESS_UP = "107", "113"  # F14 et F15, touches de luminosité des Mac


def _powershell(script: str, timeout: float = 15) -> str:
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
        creationflags=NO_WINDOW)
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip().splitlines()[-1] if completed.stderr.strip()
                           else "PowerShell en échec")
    return completed.stdout.strip()


# -- luminosité

def get_brightness() -> int | None:
    """Luminosité en pour cent, ou None si le système ne la donne pas."""
    if IS_WINDOWS:
        try:
            value = _powershell("(Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightness"
                                " -ErrorAction Stop).CurrentBrightness")
            return int(value.splitlines()[0])
        except (RuntimeError, ValueError, IndexError, OSError):
            return None       # écran externe : le pilote n'expose rien
    return None


def set_brightness(level: int | None = None, change: int | None = None) -> str:
    """Règle la luminosité de l'écran intégré."""
    if IS_WINDOWS:
        if level is not None:
            target = max(0, min(100, int(level)))
            try:
                _powershell("(Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightnessMethods"
                            f" -ErrorAction Stop).WmiSetBrightness(1, {target})")
                return f"Luminosité à {target} pour cent."
            except (RuntimeError, OSError) as exc:
                raise RuntimeError("cet écran ne permet pas de régler la luminosité par logiciel") from exc
        step = change if change is not None else 10
        key = _VK_BRIGHTNESS_UP if step > 0 else _VK_BRIGHTNESS_DOWN
        user32 = ctypes.windll.user32
        for _ in range(max(1, abs(int(step)) // 10)):
            user32.keybd_event(key, 0, 0, 0)
            user32.keybd_event(key, 0, 2, 0)
        return "J'augmente la luminosité." if step > 0 else "Je baisse la luminosité."
    if not IS_MAC:
        raise unsupported("Le réglage de la luminosité")
    # macOS n'expose pas la luminosité : on appuie sur les touches dédiées, par System Events.
    # Cela demande l'autorisation Accessibilité, la même que pour piloter les applications.
    def press(key: str, times: int) -> None:
        if times <= 0:
            return
        script = f'tell application "System Events" to repeat {times} times\nkey code {key}\nend repeat'
        completed = run(["osascript", "-e", script], timeout=20)
        if completed.returncode != 0:
            raise RuntimeError("macOS n'autorise pas encore Jarvis à appuyer sur les touches : "
                               "Réglages Système, Confidentialité et sécurité, Accessibilité")

    if level is not None:
        target = max(0, min(100, int(level)))
        press(_MAC_BRIGHTNESS_DOWN, _BRIGHTNESS_STEPS)                        # au minimum
        press(_MAC_BRIGHTNESS_UP, round(target / 100 * _BRIGHTNESS_STEPS))    # puis au niveau voulu
        return f"Luminosité à {target} pour cent."
    step = change if change is not None else 10
    press(_MAC_BRIGHTNESS_UP if step > 0 else _MAC_BRIGHTNESS_DOWN, max(1, abs(int(step)) // 10))
    return "J'augmente la luminosité." if step > 0 else "Je baisse la luminosité."


# -- Wi-Fi et Bluetooth

def mac_wifi_device() -> str:
    """L'interface Wi-Fi de ce Mac : en0 sur les portables, en1 ailleurs, parfois autre chose."""
    completed = run(["networksetup", "-listallhardwareports"], timeout=8)
    ports = completed.stdout.split("Hardware Port: ")
    for port in ports:
        if port.lower().startswith(("wi-fi", "airport")):
            if match := re.search(r"Device:\s*(\S+)", port):
                return match.group(1)
    return "en0"


def wifi_status() -> str:
    """Nom du réseau connecté, ou l'état du Wi-Fi."""
    if IS_MAC:
        completed = run(["networksetup", "-getairportnetwork", mac_wifi_device()], timeout=8)
        text = completed.stdout.strip()
        if "Current Wi-Fi Network" in text:
            return f"Tu es connecté au réseau {text.split(':', 1)[1].strip()}."
        power = run(["networksetup", "-getairportpower", mac_wifi_device()], timeout=8).stdout
        return "Le Wi-Fi est éteint." if "Off" in power else "Le Wi-Fi est allumé mais sans réseau connecté."
    if IS_WINDOWS:
        completed = run(["netsh", "wlan", "show", "interfaces"], timeout=10)
        text = completed.stdout
        if match := re.search(r"^\s*(?:SSID|Nom du r.seau)\s*:\s*(.+)$", text, re.MULTILINE):
            name = match.group(1).strip()
            if name and "BSSID" not in name:
                return f"Tu es connecté au réseau {name}."
        if re.search(r"(?:State|.tat)\s*:\s*(?:disconnected|d.connect)", text, re.IGNORECASE):
            return "Le Wi-Fi n'est connecté à aucun réseau."
        return "Je ne vois pas de carte Wi-Fi sur cet ordinateur."
    raise unsupported("L'état du Wi-Fi")


def set_wifi(on: bool) -> str:
    if IS_MAC:
        run(["networksetup", "-setairportpower", mac_wifi_device(), "on" if on else "off"], timeout=10)
    elif IS_WINDOWS:
        # netsh coupe l'interface : plus simple et plus fiable que l'API radio.
        completed = run(["netsh", "interface", "set", "interface",
                         "name=Wi-Fi", f"admin={'enable' if on else 'disable'}"], timeout=15)
        if completed.returncode != 0:
            raise RuntimeError("Windows demande les droits administrateur pour allumer ou couper le Wi-Fi")
    else:
        raise unsupported("Le Wi-Fi")
    return "J'ai allumé le Wi-Fi." if on else "J'ai coupé le Wi-Fi."


def bluetooth_status() -> str:
    if IS_MAC:
        completed = run(["system_profiler", "SPBluetoothDataType"], timeout=20)
        if re.search(r"State:\s*On", completed.stdout):
            return "Le Bluetooth est allumé."
        return "Le Bluetooth est éteint." if completed.stdout else "Je n'arrive pas à lire l'état du Bluetooth."
    if IS_WINDOWS:
        state = _powershell("(Get-PnpDevice -Class Bluetooth -ErrorAction SilentlyContinue |"
                            " Where-Object { $_.Status -eq 'OK' } | Measure-Object).Count")
        return "Le Bluetooth est allumé." if state.strip() not in ("", "0") else "Le Bluetooth est éteint."
    raise unsupported("L'état du Bluetooth")
