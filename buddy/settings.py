"""Small settings the buddy can change about itself at runtime (e.g. volume), saved to data/settings.json
so they survive restarts. These override config.toml."""
import json
import threading

from .config import DATA

_PATH = DATA / "settings.json"
_lock = threading.Lock()


def load() -> dict:
    try:
        return json.loads(_PATH.read_text())
    except Exception:
        return {}


def save(**changes):
    with _lock:
        data = load()
        data.update(changes)
        _PATH.write_text(json.dumps(data, indent=2))


VOLUME_TOOL = {
    "name": "set_volume",
    "description": (
        "Change how loud your own voice is. Use when the user asks you to be louder, quieter, "
        "or to set a specific volume. The setting is remembered across restarts."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["up", "down", "set"]},
            "level": {"type": "integer", "description": "0-100, only for action=set"},
        },
        "required": ["action"],
    },
}


def apply_volume(state, action: str, level=None) -> str:
    with state.lock:
        cur = state.volume
    if action == "up":
        new = cur + 15
    elif action == "down":
        new = cur - 15
    else:
        new = int(level if level is not None else cur)
    new = max(5, min(100, new))
    with state.lock:
        state.volume = new
    save(volume=new)
    return f"Volume changed from {cur} to {new} (out of 100)."
