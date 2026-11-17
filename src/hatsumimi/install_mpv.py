"""install-mpv-script: copy the Hatsumimi pause panel into mpv's config folder.

Copies (rather than links) so it keeps working if this project folder moves.
Re-run it after updating the project to install the new version.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent / "mpv"


def find_mpv() -> Path | None:
    if found := shutil.which("mpv"):
        return Path(found).resolve()
    for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")):
        for folder in ("MPV Player", "mpv"):  # shinchiro installer uses "MPV Player"
            if base and (exe := Path(base) / folder / "mpv.exe").exists():
                return exe
    local = Path(os.environ.get("LOCALAPPDATA", ""))
    link = local / "Microsoft" / "WinGet" / "Links" / "mpv.exe"
    if link.exists():
        return link.resolve()
    for exe in (local / "Microsoft" / "WinGet" / "Packages").glob("*mpv*/**/mpv.exe"):
        return exe
    return None


def config_dir() -> tuple[Path, str]:
    """mpv's config folder, honouring a portable_config next to mpv.exe."""
    if sys.platform != "win32":
        base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
        return Path(base) / "mpv", "~/.config/mpv"
    mpv = find_mpv()
    if mpv and (mpv.parent / "portable_config").is_dir():
        return mpv.parent / "portable_config", f"portable_config next to {mpv}"
    return Path(os.environ["APPDATA"]) / "mpv", "%APPDATA%\\mpv"


def main() -> int:
    target, why = config_dir()
    scripts, opts = target / "scripts", target / "script-opts"
    scripts.mkdir(parents=True, exist_ok=True)
    opts.mkdir(parents=True, exist_ok=True)

    shutil.copyfile(HERE / "hatsumimi.lua", scripts / "hatsumimi.lua")
    print(f"Installed  {scripts / 'hatsumimi.lua'}")

    conf = opts / "hatsumimi.conf"
    if conf.exists():
        print(f"Kept your  {conf}")  # never overwrite the user's settings
    else:
        shutil.copyfile(HERE / "hatsumimi.conf", conf)
        print(f"Created    {conf}")
    print(f"(mpv config folder: {why})")
    if not find_mpv():
        print("Note: mpv itself wasn't found. Install it with:  winget install shinchiro.mpv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
