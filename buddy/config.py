"""Loads config.toml, .env and paths."""
import os
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
MEMORY_DIR = DATA / "memories"
DB_PATH = DATA / "history.db"


def load_env(path: Path = ROOT / ".env") -> None:
    """Minimal .env loader (KEY=value lines)."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


def load_config() -> dict:
    load_env()
    path = ROOT / "config.toml"
    if not path.exists():
        path = ROOT / "config.example.toml"
    with open(path, "rb") as f:
        cfg = tomllib.load(f)
    DATA.mkdir(exist_ok=True)
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    return cfg


def load_personality(cfg: dict) -> str:
    text = (ROOT / "personality.md").read_text()
    return text.format(name=cfg["buddy"]["name"], user_name=cfg["buddy"]["user_name"])
