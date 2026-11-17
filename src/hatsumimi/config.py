"""Settings, stored in the user's config folder (works on Windows and Linux).

  Windows: %APPDATA%\\hatsumimi\\config.toml
  Linux:   ~/.config/hatsumimi/config.toml

Jimaku API key lookup order:
1. The JIMAKU_API_KEY environment variable.
2. API.txt in the project folder (development convenience; git-ignored).
3. jimaku_api_key in config.toml (what the app's Settings dialog writes).
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

from platformdirs import user_config_dir

APP_NAME = "hatsumimi"

DEFAULTS = {
    "jimaku_api_key": "",
    "mux": True,    # embed subtitles into .mkv files
    "sync": True,   # re-time subtitles to the video
}


def config_dir() -> Path:
    # roaming=True keeps Windows on %APPDATA% (where earlier versions saved it).
    return Path(user_config_dir(APP_NAME, appauthor=False, roaming=True))


def config_path() -> Path:
    return config_dir() / "config.toml"


def load_config() -> dict:
    cfg = dict(DEFAULTS)
    path = config_path()
    if path.is_file():
        try:
            with path.open("rb") as f:
                cfg.update(tomllib.load(f))
        except (OSError, tomllib.TOMLDecodeError):
            pass
    return cfg


def _toml_value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    s = str(v).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{s}"'


def save_config(cfg: dict) -> None:
    """Write settings (flat key = value only, which is all we store)."""
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{k} = {_toml_value(v)}" for k, v in cfg.items()]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _key_from_file() -> str | None:
    path = PROJECT_ROOT / "API.txt"
    try:
        if path.is_file():
            return path.read_text(encoding="utf-8-sig").strip() or None
    except OSError:
        pass
    return None


def find_api_key() -> str | None:
    key = (os.environ.get("JIMAKU_API_KEY")
           or _key_from_file()
           or load_config().get("jimaku_api_key"))
    return key.strip() if key else None


def jimaku_api_key() -> str:
    """For the command line: exit with instructions if there's no key."""
    key = find_api_key()
    if not key:
        raise SystemExit(
            "No Jimaku API key found.\n"
            "Create one at https://jimaku.cc (Account -> API key), then either:\n"
            "  - set the JIMAKU_API_KEY environment variable, or\n"
            "  - enter it in the Hatsumimi app's Settings, or\n"
            f'  - create {config_path()} containing:  jimaku_api_key = "your-key"'
        )
    return key
