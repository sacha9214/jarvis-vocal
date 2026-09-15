"""État de la machine : batterie, processeur, mémoire."""
from __future__ import annotations

from typing import Any

import psutil


def snapshot() -> dict[str, Any]:
    battery = psutil.sensors_battery()
    return {
        "cpu": round(psutil.cpu_percent(interval=None)),
        "ram": round(psutil.virtual_memory().percent),
        "battery": round(battery.percent) if battery else None,
        "plugged": bool(battery.power_plugged) if battery else None,
    }


def sentence() -> str:
    psutil.cpu_percent(interval=None)
    cpu = round(psutil.cpu_percent(interval=0.3))
    state = snapshot()
    parts = []
    if state["battery"] is not None:
        parts.append(f"batterie à {state['battery']} pour cent" + (", en charge" if state["plugged"] else ""))
    parts += [f"processeur à {cpu} pour cent", f"mémoire utilisée à {state['ram']} pour cent"]
    text = ", ".join(parts)
    return text[0].upper() + text[1:] + "."
