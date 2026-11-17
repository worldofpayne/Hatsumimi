"""`Hatsumimi --self-test report.txt`: check the non-GUI parts work in this build.

Useful after packaging, where missing data files or modules only show up at
run time. Writes a plain-text report and returns 0 if everything passed.
"""

from __future__ import annotations

import io
import subprocess
import tempfile
import traceback
import zipfile
from pathlib import Path


def _check(report: list[str], name: str, fn) -> bool:
    try:
        detail = fn()
        report.append(f"PASS  {name}: {detail}")
        return True
    except Exception:  # noqa: BLE001
        report.append(f"FAIL  {name}:\n{traceback.format_exc()}")
        return False


def run(report_path: str) -> int:
    report: list[str] = []
    ok = True

    def identify():
        from ..identify import identify
        i = identify(Path("[SubsPlease] Kusuriya no Hitorigoto - 54 (1080p) [699EDF35].mkv"))
        assert i and i.title == "Kusuriya no Hitorigoto" and i.episode == 54, i
        return f"{i.title} ep {i.episode}"

    def dictionary():
        from ..dictionary.lookup import shared
        d = shared()
        if d is None:
            return "not installed (skipped)"
        r = d.lookup("書かなかった")
        assert r and r[0].kanji[0] == "書く", r[:1]
        return f"書かなかった -> {r[0].kanji[0]} ({' · '.join(r[0].reasons)})"

    def archives():
        from ..subtitles import archive_members, to_utf8_text
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("Show - 03.srt", "台詞\r\n".encode("cp932"))
        m = archive_members("x.zip", buf.getvalue())
        text = to_utf8_text(m["Show - 03.srt"])
        assert text.startswith("台詞"), text
        import py7zr  # noqa: F401  (bundled?)
        return "zip + Shift-JIS decode, py7zr importable"

    def sync():
        from ..sync import sync_subtitle
        from ..tools import check_tools
        missing = [t.name for t in check_tools() if not t.path and t.name != "mkvmerge"]
        if missing:
            return f"skipped: {', '.join(missing)} not installed"
        t = Path(tempfile.mkdtemp(prefix="hatsumimi-selftest-"))

        def ts(s):
            ms = round(s * 1000)
            h, ms = divmod(ms, 3600000)
            m, ms = divmod(ms, 60000)
            s, ms = divmod(ms, 1000)
            return f"{h:02}:{m:02}:{s:02},{ms:03}"
        cues = [(2 + i * 3.1, 3.4 + i * 3.1 + (i % 3) * 0.4) for i in range(30)]
        for name, shift in (("en.srt", 0.0), ("ja.srt", 2.5)):
            (t / name).write_text("\n".join(f"{n}\n{ts(a + shift)} --> {ts(b + shift)}\nline {n}\n"
                                            for n, (a, b) in enumerate(cues, 1)), encoding="utf-8")
        v = t / "v.mkv"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=black:s=160x120:d=100",
                        "-f", "lavfi", "-i", "anullsrc=d=100", "-i", str(t / "en.srt"), "-map", "0:v", "-map", "1:a",
                        "-map", "2:s", "-c:v", "libx264", "-c:a", "aac", "-c:s", "srt",
                        "-metadata:s:s:0", "language=eng", "-shortest", str(v)], check=True, capture_output=True)
        res = sync_subtitle(v, t / "ja.srt", t / "out.srt")
        assert res.ok and res.offset is not None and abs(res.offset + 2.5) < 0.05, res
        return f"offset {res.offset:+.2f}s against {res.reference}"

    def libmpv():
        from .libmpv import mpv
        p = mpv.MPV(vo="null", ao="null")
        version = p.mpv_version
        p.terminate()
        return version

    for name, fn in (("filename parsing", identify), ("dictionary", dictionary),
                     ("archives", archives), ("libmpv", libmpv), ("sync", sync)):
        ok &= _check(report, name, fn)

    report.append("ALL PASSED" if ok else "SOME CHECKS FAILED")
    Path(report_path).write_text("\n".join(report) + "\n", encoding="utf-8")
    return 0 if ok else 1
